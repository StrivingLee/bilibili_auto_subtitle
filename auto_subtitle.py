import requests
import json
import os
import argparse
import time
import hashlib
import urllib.parse

from pathlib import Path


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/114.0.0.0 Safari/537.36",
    "Referer": "https://www.bilibili.com/",
}


# 以脚本文件所在目录为基准，避免受 CWD 影响
BASE_DIR = Path(__file__).resolve().parent


def load_cookie(cookie_file="cookie.txt"):
    """从文件读取 cookie"""
    cookie_path = BASE_DIR / cookie_file
    if not cookie_path.exists():
        print("⚠️ 没找到 cookie.txt，继续使用游客模式（可能没有字幕）")
        return None
    return cookie_path.read_text(encoding="utf-8").strip()


cookie = load_cookie()
if cookie:
    HEADERS["Cookie"] = cookie


def get_aid_cid_and_title(bvid):
    """根据BV号获取aid, cid和视频标题"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    resp = requests.get(url, headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()
    aid = data["data"]["aid"]
    cid = data["data"]["cid"]
    title = data["data"]["title"]
    # 去掉文件名中不允许的字符
    title = "".join(c for c in title if c not in r'\/:*?"<>|')
    return aid, cid, title


# WBI mixinKey 生成算法
def get_mixin_key(img_key, sub_key):
    s = img_key + sub_key
    table = [46, 9, 25, 3, 28, 17, 46, 0, 8, 7, 12, 18, 1, 4, 45, 6,
             31, 43, 10, 11, 2, 19, 16, 44, 13, 5, 32, 34, 24, 29,
             36, 26, 38, 15, 23, 37, 22, 27, 35, 30, 21, 41, 20,
             42, 39, 14, 33, 40]
    return "".join([s[i] for i in table])[:32]


def encode_params(params, mixin_key):
    # 给参数加上 wts
    params["wts"] = int(time.time())
    # 过滤特殊字符
    for k, v in params.items():
        if isinstance(v, str):
            params[k] = v.replace("!", "").replace("'", "").replace(
                "(", "").replace(")", "").replace("*", "")
    # 排序
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    # 生成 w_rid
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params


def get_wbi_keys():
    """获取 img_key 和 sub_key"""
    resp = requests.get(
        "https://api.bilibili.com/x/web-interface/nav", headers=HEADERS)
    resp.raise_for_status()
    data = resp.json()["data"]["wbi_img"]
    img_key = data["img_url"].split("/")[-1].split(".")[0]
    sub_key = data["sub_url"].split("/")[-1].split(".")[0]
    return img_key, sub_key


def get_subtitle_url(aid, cid, lan='ai-zh', retries=3, delay=2):
    """使用新版 wbi 接口获取字幕 URL"""
    img_key, sub_key = get_wbi_keys()
    mixin_key = get_mixin_key(img_key, sub_key)

    base_params = {
        "aid": aid,
        "cid": cid,
        "isGaiaAvoided": "false",
        "web_location": "1315873",
    }
    params = encode_params(base_params, mixin_key)

    url = "https://api.bilibili.com/x/player/wbi/v2"
    for attempt in range(1, retries + 1):
        resp = requests.get(url, headers=HEADERS, params=params)
        resp.raise_for_status()
        data = resp.json()
        subtitles = data["data"]["subtitle"].get("subtitles", [])
        # subtitles = data.get("data", {})["subtitle"].get("subtitles", [])

        if subtitles:
            if lan:
                for sub in subtitles:
                    if sub["lan"] == lan and sub.get("subtitle_url"):
                        return "https:" + sub["subtitle_url"], subtitles
            for sub in subtitles:
                if sub.get("subtitle_url"):
                    return "https:" + sub["subtitle_url"], subtitles

        print(f"⚠️ 第 {attempt} 次请求未获取到字幕，等待 {delay} 秒重试...")
        time.sleep(delay)

    return None, []


def download_subtitle(sub_url, save_dir: Path, save_name: str="subtitle.json"):
    """下载字幕JSON"""
    resp = requests.get(sub_url, headers=HEADERS)
    resp.raise_for_status()
    save_dir.mkdir(parents=True, exist_ok=True)   # 确保目录存在
    save_path = save_dir / save_name              # 用 Path 组合路径
    save_path.write_text(resp.text, encoding="utf-8")
    print(f"💾 JSON 已保存至: {save_path.resolve()}")  # ★修改：打印绝对路径
    return str(save_path)


def extract_bilibili_subtitle(json_file, output_dir="output"):
    """从字幕JSON提取文本并保存"""
    with open(json_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    base_name = os.path.splitext(os.path.basename(json_file))[0]
    output_file = os.path.join(output_dir, f"{base_name}.txt")

    lines = []
    if "body" in data:
        for item in data["body"]:
            if "content" in item:
                lines.append(item["content"])

    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    print(f"✅ 字幕已提取完成: {output_file}")


def main():
    parser = argparse.ArgumentParser(description="输入BV号，自动下载并提取B站字幕（兼容最新API）")
    parser.add_argument("bvid", help="视频的BV号")
    parser.add_argument("-o", "--output", default="output",
                        help="输出目录（默认 output/）")
    parser.add_argument("--json-dir", default="input", 
                        help="原始JSON保存目录（默认 input/）")
    parser.add_argument("--lan", help="字幕语言代码，例如 zh-CN, en, ai-zh 等")
    args = parser.parse_args()

    bvid = args.bvid
    aid, cid, title = get_aid_cid_and_title(bvid)
    sub_url, subtitle_list = get_subtitle_url(aid, cid, args.lan)

    if not sub_url:
        print("❌ 该视频没有字幕（或需要登录状态）")
        return

    print("🎬 可用字幕列表：")
    for sub in subtitle_list:
        print(f"  {sub['lan']} - {sub['lan_doc']}")

    print(f"👉 选择的字幕地址: {sub_url}")
    json_dir = (BASE_DIR / args.json_dir)          # json 目录固定到脚本目录
    json_file = download_subtitle(sub_url, json_dir, f"{title}.json")
    extract_bilibili_subtitle(json_file, args.output)


if __name__ == "__main__":
    main()

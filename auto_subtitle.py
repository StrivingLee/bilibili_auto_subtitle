import requests
import json
import os
import argparse
import time
import hashlib
import urllib.parse
import qrcode
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# ==============================================================================
# BiliBiliSession: 将固定的 HEADERS 和 cookie 管理封装到 Session 类中
# ==============================================================================

class BiliAuthError(Exception):
    """自定义B站登录认证异常"""
    pass

class BiliBiliSession:
    """
    用于管理B站请求会话、Cookie加载和扫码登录的类。
    """
    BASE_DIR = Path(__file__).resolve().parent
    COOKIE_FILE = BASE_DIR / "cookie.json"  # 使用.json存储更结构化

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/114.0.0.0 Safari/537.36"
            ),
            "Referer": "https://www.bilibili.com/",
        })
        self.csrf = ""
        self.load_cookie()

    def load_cookie(self):
        """从 cookie.json 加载 cookie 并验证其有效性"""
        if not self.COOKIE_FILE.exists():
            print("ℹ️ 未找到本地 cookie 文件, 将尝试扫码登录。")
            self.login_by_qrcode()
            return

        try:
            with open(self.COOKIE_FILE, "r", encoding="utf-8") as f:
                cookies = json.load(f)
            
            # 提取关键 cookie
            sessdata = cookies.get("SESSDATA")
            self.csrf = cookies.get("bili_jct")
            
            if not sessdata or not self.csrf:
                raise ValueError("Cookie 文件中缺少关键字段 SESSDATA 或 bili_jct")

            self.session.cookies.update({"SESSDATA": sessdata, "bili_jct": self.csrf})
            
            # 通过访问一个需要登录的接口来验证 cookie 是否有效
            if not self.check_login_status():
                 print("⚠️ Cookie 已失效, 请重新扫码登录。")
                 self.login_by_qrcode()
            else:
                print("✅ 已成功加载本地 Cookie, 登录状态有效。")

        except (json.JSONDecodeError, ValueError) as e:
            print(f"❌ 加载 Cookie 失败: {e}。将尝试扫码登录。")
            self.login_by_qrcode()

    def check_login_status(self) -> bool:
        """检查当前登录状态是否有效"""
        url = "https://api.bilibili.com/x/web-interface/nav"
        try:
            resp = self.session.get(url, timeout=5)
            resp.raise_for_status()
            data = resp.json()
            return data.get("code") == 0 and data.get("data", {}).get("isLogin", False)
        except requests.RequestException:
            return False

    def save_cookie(self):
        """将会话中的 cookie 保存到文件"""
        cookies = {
            "SESSDATA": self.session.cookies.get("SESSDATA"),
            "bili_jct": self.session.cookies.get("bili_jct"),
            "DedeUserID": self.session.cookies.get("DedeUserID"),
        }
        with open(self.COOKIE_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, indent=4)
        print(f"💾 Cookie 已保存至: {self.COOKIE_FILE}")

    def login_by_qrcode(self):
        """通过二维码扫描进行登录"""
        print("🚀 正在生成登录二维码...")
        # 1. 获取二维码及 qrcode_key
        get_qrcode_url = "https://passport.bilibili.com/x/passport-login/web/qrcode/generate"
        resp = self.session.get(get_qrcode_url)
        data = resp.json()["data"]
        qrcode_key = data["qrcode_key"]
        qr_url = data["url"]

        # 2. 在终端显示二维码
        qr = qrcode.QRCode()
        qr.add_data(qr_url)
        qr.make(fit=True)
        # 使用invert参数让二维码在深色背景终端更好看
        qr.print_tty(invert=True) # 使用 tty 模式输出，尺寸更适合终端
        print("请使用Bilibili手机客户端扫描上方二维码登录...")

        # 3. 轮询扫码状态
        try:
            while True:
                poll_url = f"https://passport.bilibili.com/x/passport-login/web/qrcode/poll?qrcode_key={qrcode_key}"
                resp = self.session.get(poll_url)
                poll_data = resp.json()["data"]
                code = poll_data["code"]
                
                if code == 0:  # 0: 登录成功
                    print("\n✅ 扫码成功, 登录成功！")
                    self.csrf = self.session.cookies.get("bili_jct")
                    self.save_cookie()
                    break
                elif code == 86038: # 86038: 二维码已失效
                    print("\n❌ 二维码已失效, 请重新运行程序。")
                    exit()
                elif code == 86090: # 86090: 已扫描待确认
                    print("扫描成功, 请在手机上确认登录...", end='\r')
                # 其他状态码 (86101:未扫描) 则继续等待
                
                time.sleep(2)
        except KeyboardInterrupt:
            print("\n🚫 用户取消登录。")
            exit()
    
    def get(self, url: str, **kwargs: Any) -> requests.Response:
        """
        封装了 requests.get 方法，并增加了对登录失效的自动处理能力。
        """
        try:
            # 首次尝试发送请求
            resp = self.session.get(url, **kwargs)
            resp.raise_for_status() # 检查 HTTP 错误 (如 404, 500)
            
            # 检查B站业务错误码
            data = resp.json()
            if data.get("code") == -101: # -101 通常代表 "账号未登录"
                raise BiliAuthError("Cookie 已失效 (code: -101)")
            
            return resp

        except BiliAuthError as e:
            print(f"\n⚠️ 警告: {e}。 Cookie 可能在运行中已过期。")
            print("🔄 正在尝试重新登录以刷新 Cookie...")
            
            # 调用扫码登录流程来获取新 Cookie
            self.login_by_qrcode()
            
            # 使用全新的、有效的 Cookie 重新发送刚才失败的请求
            print("✅ 登录成功! 正在重试刚才失败的请求...")
            resp = self.session.get(url, **kwargs)
            resp.raise_for_status()
            return resp

# ==============================================================================
# 将业务逻辑函数与 Session 实例解耦
# ==============================================================================

def get_aid_cid_and_title(bili_session: BiliBiliSession, bvid: str) -> Tuple[int, int, str]:
    """根据BV号获取aid, cid和视频标题"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    resp = bili_session.get(url) # 使用 session 对象
    resp.raise_for_status()
    data = resp.json()
    
    if data["code"] != 0:
        raise RuntimeError(f"获取视频信息失败: {data.get('message', '未知错误')}")

    aid = data["data"]["aid"]
    cid = data["data"]["cid"]
    title = data["data"]["title"]
    # 去掉文件名中不允许的字符
    title = "".join(c for c in title if c not in r'\/:*?"<>|')
    return aid, cid, title

# WBI mixinKey 生成算法
def get_mixin_key(img_key: str, sub_key: str) -> str:
    s = img_key + sub_key
    table = [
        46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49,
        33, 9, 42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40,
        61, 26, 17, 0, 1, 60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11,
        36, 20, 34, 44, 52
    ]
    return "".join([s[i] for i in table])[:32]

# WBI 签名参数编码
def encode_params(params: Dict[str, Any], mixin_key: str) -> Dict[str, Any]:
    # 给参数加上 wts
    params["wts"] = int(time.time())
    # 过滤特殊字符
    for k, v in params.items():
        if isinstance(v, str):
            v = v.replace("!", "").replace("'", "").replace("(", "").replace(")", "").replace("*", "")
            params[k] = v
    params = dict(sorted(params.items()))
    query = urllib.parse.urlencode(params)
    w_rid = hashlib.md5((query + mixin_key).encode()).hexdigest()
    params["w_rid"] = w_rid
    return params

def get_wbi_keys(bili_session: BiliBiliSession) -> Tuple[str, str]:
    """获取 img_key 和 sub_key"""
    resp = bili_session.get("https://api.bilibili.com/x/web-interface/nav") # 使用 session 对象
    resp.raise_for_status()
    data = resp.json()["data"]["wbi_img"]
    img_key = data["img_url"].split("/")[-1].split(".")[0]
    sub_key = data["sub_url"].split("/")[-1].split(".")[0]
    return img_key, sub_key

def get_subtitle_url(
    bili_session: BiliBiliSession, aid: int, cid: int, lan: Optional[str] = 'ai-zh', retries: int = 3, delay: int = 2
) -> Tuple[Optional[str], list]:
    """使用新版 wbi 接口获取字幕 URL"""
    img_key, sub_key = get_wbi_keys(bili_session)
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
        try:
            resp = bili_session.get(url, params=params) # ★ 使用 session 对象
            resp.raise_for_status()
            data = resp.json()

            if data["code"] != 0:
                print(f"⚠️ API 返回错误: {data['message']}")
                return None, []
            
            subtitles = data["data"]["subtitle"].get("subtitles", [])

            if subtitles:
                if lan:
                    for sub in subtitles:
                        if sub["lan"] == lan and sub.get("subtitle_url"):
                            return "https:" + sub["subtitle_url"], subtitles
                # 如果指定语言找不到或未指定，返回第一个
                for sub in subtitles:
                    if sub.get("subtitle_url"):
                        return "https:" + sub["subtitle_url"], subtitles

            print(f"⚠️ 第 {attempt} 次请求未获取到字幕，等待 {delay} 秒重试...")
            time.sleep(delay)
        
        except requests.RequestException as e:
            print(f"❌ 网络请求失败: {e}")
            time.sleep(delay)

    return None, []


# 下载和文件处理函数
def download_subtitle(bili_session: BiliBiliSession, sub_url: str, save_path: Path) -> Path:
    """下载字幕JSON"""
    resp = bili_session.get(sub_url) # ★ 使用 session 对象
    resp.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(resp.text, encoding="utf-8")
    print(f"💾 JSON 已保存至: {save_path.resolve()}")
    return save_path

def extract_bilibili_subtitle(json_path: Path, output_path: Path):
    """从字幕JSON提取文本并保存"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    lines = [item["content"] for item in data.get("body", []) if "content" in item]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"✅ 字幕已提取完成: {output_path.resolve()}")

# ==============================================================================
# 主函数
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="输入BV号，自动下载并提取B站字幕（兼容最新API）")
    parser.add_argument("bvid", help="视频的BV号")
    parser.add_argument("-o", "--output", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--json-dir", default="input", help="原始JSON保存目录（默认 input/）")
    parser.add_argument("--lan", help="字幕语言代码，例如 zh-CN, en-US, ai-zh 等")
    args = parser.parse_args()

    # 实例化并初始化 Session
    try:
        bili_session = BiliBiliSession()
        
        print(f"\n📄 正在获取视频信息: {args.bvid}")
        aid, cid, title = get_aid_cid_and_title(bili_session, args.bvid)
        print(f"   - 标题: {title}")
        print(f"   - AID: {aid}, CID: {cid}")

        sub_url, subtitle_list = get_subtitle_url(bili_session, aid, cid, args.lan)

        if not sub_url:
            print("❌ 该视频没有可用字幕。")
            # 没有字幕时也列出可用列表
            if subtitle_list:
                print("但是找到了以下语言选项:")
                for sub in subtitle_list:
                    print(f"  - {sub['lan']:<8} ({sub['lan_doc']})")
            return

        print("\n📜 可用字幕列表：")
        for sub in subtitle_list:
            is_selected = "https:" + sub.get("subtitle_url", "") == sub_url
            prefix = "👉" if is_selected else "  "
            print(f" {prefix} {sub['lan']:<8} ({sub['lan_doc']})")

        # 使用 pathlib 进行路径管理
        base_dir = Path(__file__).resolve().parent
        json_save_path = base_dir / args.json_dir / f"{title}.json"
        txt_output_path = base_dir / args.output / f"{title}.txt"

        download_subtitle(bili_session, sub_url, json_save_path)
        extract_bilibili_subtitle(json_save_path, txt_output_path)

    except (requests.RequestException, RuntimeError, KeyError) as e:
        print(f"\n💥 程序运行出错: {e}")
    except KeyboardInterrupt:
        print("\n👋 用户中断操作, 程序退出。")


if __name__ == "__main__":
    main()
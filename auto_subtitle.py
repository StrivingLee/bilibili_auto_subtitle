import requests
import json
import os
import argparse
import time
import hashlib
import urllib.parse
import qrcode
from pathlib import Path
from typing import Optional, Tuple, Dict, List, Any

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
def sanitize_filename(filename: str) -> str:
    """去除文件名中的非法字符"""
    return "".join(c for c in filename if c not in r'\/:*?"<>|')

def get_video_info(bili_session: BiliBiliSession, bvid: str) -> Tuple[int, str, List[Dict]]:
    """根据BV号获取aid, 视频主标题, 以及所有分P的列表"""
    url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
    resp = bili_session.get(url)
    data = resp.json()
    
    if data["code"] != 0:
        raise RuntimeError(f"获取视频信息失败: {data.get('message', '未知错误')}")
    
    view_data = data["data"]
    aid = view_data["aid"]
    title = view_data["title"]
    pages_list = view_data.get("pages", [])
    
    # ★ 升级点 2: 兼容单个视频的情况
    # 如果没有pages列表，说明是单个视频，我们手动构建一个和多P兼容的列表结构
    if not pages_list:
        pages_list = [{
            "cid": view_data["cid"],
            "page": 1,
            "part": title  # 单个视频的分P标题就是主标题
        }]

    return aid, title, pages_list

def process_video_part(
    bili_session: BiliBiliSession, 
    aid: int, 
    page_info: Dict, 
    main_title: str, 
    is_collection: bool, 
    args: argparse.Namespace,
    json_save_dir: Path,
    txt_save_dir: Path
) -> Optional[List[str]]:
    """
    处理单个视频分P的完整流程：获取字幕、下载、提取。
    
    Args:
        bili_session: Bilibili会话对象。
        aid: 视频的 aid。
        page_info: 单个分P的信息字典 (包含 cid, page, part)。
        main_title: 视频的主标题。
        is_collection: 是否为合集。
        args: 命令行参数。
        json_save_dir: JSON 保存目录。
        txt_save_dir: TXT 保存目录。
    """
    cid = page_info["cid"]
    page_num = page_info["page"]
    part_title = page_info["part"]
    
    print("-" * 50)
    if is_collection:
        print(f"▶️ 正在处理 P{page_num}: {part_title}")
    
    sub_url, subtitle_list = get_subtitle_url(bili_session, aid, cid, args.lan)

    if not sub_url:
        print("    ❌ 该分P没有可用字幕。")
        return None # 直接返回None，表示失败

    print("    📜 可用字幕列表：")
    for sub in subtitle_list:
        is_selected = "https:" + sub.get("subtitle_url", "") == sub_url
        prefix = "👉" if is_selected else "  "
        print(f"     {prefix} {sub['lan']:<8} ({sub['lan_doc']})")
    
    # 智能生成文件名
    safe_main_title = sanitize_filename(main_title)
    safe_part_title = sanitize_filename(part_title)
    
    if is_collection and main_title != safe_part_title:
        file_stem = f"P{page_num} {safe_part_title}" # 合集内文件名更简洁
    else: # 单个视频或分P标题与主标题相同时
        file_stem = safe_main_title

    # 使用传入的目录来构建最终路径
    json_save_path = json_save_dir / f"{file_stem}.json"
    download_subtitle(bili_session, sub_url, json_save_path)

    # 根据 --merge 参数决定是否保存单个TXT文件
    should_save_individual_file = not (args.merge and is_collection and not args.part)
    if should_save_individual_file:
        txt_output_path = txt_save_dir / f"{file_stem}.txt"
        extract_bilibili_subtitle(json_save_path, txt_output_path)

    with open(json_save_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    lines = [item["content"] for item in data.get("body", []) if "content" in item]
    return lines

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

def create_clickable_link(uri: str, text: str) -> str:
    """
    使用 OSC 8 终端序列创建可点击的超链接。
    
    Args:
        uri: 链接的目标地址 (例如 'file:///C:/path/to/file')。
        text: 要在终端中显示的文本 (例如 'C:\\path\\to\\file')。

    Returns:
        一个特殊的字符串，在兼容的终端中会显示为超链接。
    """
    # OSC 8 序列格式: \x1b]8;;URI\x1b\\TEXT\x1b]8;;\x1b\\
    # \x1b 是 ESC 字符
    # \x1b\\ 是字符串终止符 (ST)
    return f"\x1b]8;;{uri}\x1b\\{text}\x1b]8;;\x1b\\"

def print_clickable_path(message: str, file_path: Path) -> None:
    """
    打印一条包含可点击文件路径的消息。

    这会在终端中显示文件的可读路径，但链接到其 URI 编码版本，
    从而解决了中文和空格路径的点击问题。

    Args:
        message: 打印在链接前的前缀消息 (例如 "文件已保存至: ")。
        file_path: 指向文件的 Path 对象。
    """
    abs_path = file_path.resolve()
    uri_target = abs_path.as_uri()
    display_text = str(abs_path)
    
    clickable_output = create_clickable_link(uri_target, display_text)
    print(f"{message}{clickable_output}")

# 下载和文件处理函数
def download_subtitle(bili_session: BiliBiliSession, sub_url: str, save_path: Path) -> Path:
    """下载字幕JSON"""
    resp = bili_session.get(sub_url) # 使用 session 对象
    resp.raise_for_status()
    save_path.parent.mkdir(parents=True, exist_ok=True)
    save_path.write_text(resp.text, encoding="utf-8")
    
    print_clickable_path("💾 JSON 已保存至 ", save_path)
    return save_path

def extract_bilibili_subtitle(json_path: Path, output_path: Path):
    """从字幕JSON提取文本并保存"""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    lines = [item["content"] for item in data.get("body", []) if "content" in item]

    output_path.write_text("\n".join(lines), encoding="utf-8")
    
    print_clickable_path("✅ 字幕已提取完成 ", output_path)

# ==============================================================================
# 主函数
# ==============================================================================

def main():
    parser = argparse.ArgumentParser(description="输入BV号，自动下载并提取B站字幕（兼容最新API）")
    parser.add_argument("bvid", help="视频的BV号 (单个视频或合集均可)")
    parser.add_argument("-o", "--output", default="output", help="输出目录（默认 output/）")
    parser.add_argument("--json-dir", default="input", help="原始JSON保存目录（默认 input/）")
    parser.add_argument("--lan", help="字幕语言代码，例如 zh-CN, en-US, ai-zh 等")
    parser.add_argument("-p", "--part", type=int, help="指定要提取的单个分P编号 (例如: -p 5)")
    parser.add_argument("--merge", action="store_true", help="将多P合集的字幕合并输出到一个TXT文件中 (仅对处理整个合集时有效)"
    )
    args = parser.parse_args()

    # 实例化并初始化 Session
    try:
        bili_session = BiliBiliSession()
        
        print(f"\n📄 正在获取视频信息: {args.bvid}")
        aid, main_title, pages_list = get_video_info(bili_session, args.bvid)

        # 根据 --part 参数筛选要处理的分P列表
        target_pages_list = pages_list
        if args.part:
            # 从全部分P列表中查找用户指定的那一P
            found_part = next((p for p in pages_list if p['page'] == args.part), None)
            if found_part:
                target_pages_list = [found_part] # 将目标列表缩减为仅含指定的那一P
                print(f"✅ 已指定提取 P{args.part}。")
            else:
                print(f"❌ 错误: 分P号 {args.part} 不存在。该视频共有 {len(pages_list)} 个分P。")
                return # 找不到指定分P，直接退出

        is_collection = len(pages_list) > 1
        # 根据是否为合集，决定最终的输出目录
        base_dir = Path(__file__).resolve().parent
        safe_main_title = sanitize_filename(main_title)
        # 默认输出目录为根目录
        json_output_dir = base_dir / args.json_dir
        txt_output_dir = base_dir / args.output
        if is_collection and not args.part: # 仅在处理整个合集且不合并时显示此信息并创建文件夹
            json_output_dir = json_output_dir / safe_main_title
            if not args.merge:
                txt_output_dir = txt_output_dir / safe_main_title
            print(f"合集标题: {main_title}")
            print(f"合集包含 {len(pages_list)} 个分P。")
        elif is_collection and args.part: # 处理合集的单个P或合并
            print(f"合集标题: {main_title}")
        else:
            print(f"视频标题: {main_title}")

        all_subtitle_lines = []

        for page_info in target_pages_list:
            returned_lines = process_video_part(
                bili_session, aid, page_info, main_title, is_collection, args,
                json_output_dir, txt_output_dir
            )
            
            # 将返回的字幕行和分隔符添加到主列表中
            if returned_lines:
                part_title = page_info["part"]
                page_num = page_info["page"]
                separator = f"\n\n--- P{page_num} {part_title} ---\n\n"
                
                if all_subtitle_lines: # 避免在文件开头添加分隔符
                    all_subtitle_lines.append(separator)
                
                all_subtitle_lines.extend(returned_lines)
            
            time.sleep(1)

        # 循环结束后，根据 --merge 参数执行合并写入操作
        if args.merge and is_collection and not args.part and all_subtitle_lines:
            merged_filename = f"{safe_main_title} (完整字幕).txt"
            merged_output_path = txt_output_dir / merged_filename
            
            merged_output_path.parent.mkdir(parents=True, exist_ok=True)
            merged_output_path.write_text("".join(all_subtitle_lines), encoding="utf-8")
            
            print("-" * 50)
            print_clickable_path("✅ 所有分P字幕已成功合并至: ", merged_output_path)

    except (requests.RequestException, RuntimeError, KeyError) as e:
        print(f"\n💥 程序运行出错: {e}")
    except KeyboardInterrupt:
        print("\n👋 用户中断操作, 程序退出。")


if __name__ == "__main__":
    main()
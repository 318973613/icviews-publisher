"""Browserless ICViews publisher. Credentials never enter the workspace."""
import argparse
import ctypes
from ctypes import wintypes
import getpass
import hashlib
import html
import json
import os
from pathlib import Path
import re
import sys
import time
import unicodedata
from urllib.parse import unquote, urlsplit

import requests
from markdown_it import MarkdownIt

BASE = "https://www.icviews.cn"
STATE = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ICViewsPublisher"
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif"}


class PublishError(Exception):
    pass


class APIError(PublishError):
    """Only safe messages and non-sensitive status fields may leave the HTTP layer."""
    def __init__(self, kind, message, method, route, status=None, code=None):
        self.kind = kind
        self.diagnostic = {"kind": kind, "method": method, "path": urlsplit(route).path,
                           "httpStatus": status, "serverCode": code, "time": time.time()}
        suffix = f" [HTTP {status}]" if status is not None else ""
        if code is not None:
            suffix += f" [code {code}]"
        super().__init__(message + suffix)


def record_error(error):
    if isinstance(error, APIError):
        try:
            save_json(STATE / "last-error.json", error.diagnostic)
            if error.kind == "login_response":
                save_json(STATE / "last-login-schema.json", error.diagnostic)
        except OSError:
            pass


def safe_server_message(result, secrets=()):
    message = result.get("message") or result.get("msg") or ""
    if not isinstance(message, str):
        return ""
    # Redact before truncating, so a token cannot leak as a shortened prefix.
    for secret in sorted({str(value) for value in secrets if value}, key=len, reverse=True):
        message = message.replace(secret, "[已隐藏]")
    message = re.sub(r"(?i)https?://[^\s\"'<>]+|[\w.+-]+@[\w.-]+", "[已隐藏]", message)
    message = re.sub(r"(?i)(sessionKey|authorization|third-session|token|password)\s*[\"']?\s*[:=]\s*[\"']?[^\s,;\"'}]+",
                     r"\1=[已隐藏]", message)
    message = re.sub(r"[A-Za-z0-9_+/=.-]{16,}|\d{4,}", "[已隐藏]", message)
    message = "".join(c if unicodedata.category(c) not in ("Cc", "Cf") else " " for c in message)
    return message.strip()[:300]


def digest(data):
    return hashlib.sha256(data).hexdigest()


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def read_json(path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def protect(data, decrypt=False):
    if os.name != "nt":
        raise PublishError("Credential storage requires Windows DPAPI.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_byte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    fn = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                   ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
    fn.restype = wintypes.BOOL
    if not fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise PublishError("Windows credential encryption/decryption failed.")
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def credentials():
    path = STATE / "credentials.json"
    if not path.exists():
        raise PublishError("Run auth once in your terminal first. Do not paste credentials into chat.")
    value = read_json(path, {})
    return value["userId"], protect(bytes.fromhex(value["encryptedSession"]), True).decode()


def valid_mobile(mobile):
    return (len(mobile) == 11 and mobile.isascii() and mobile.isdecimal()
            and mobile[:1] == "1" and mobile[1:2] in "3456789")


def send_sms(mobile):
    if not valid_mobile(mobile):
        raise PublishError("请输入正确的 11 位手机号。")
    API("").call("POST", "/back/icviews/web/sendSmsCode", json={"mobile": mobile, "scene": 3})


def parse_login_response(result):
    def invalid(message):
        error = APIError("login_response", message, "POST", "/back/icviews/web/smsLogin", 200, 200)
        fields = result if isinstance(result, dict) else {}
        error.diagnostic["schema"] = {
            "dataType": type(result).__name__,
            "idPresent": "id" in fields,
            "idType": type(fields.get("id")).__name__,
            "sessionKeyPresent": "sessionKey" in fields,
            "sessionKeyType": type(fields.get("sessionKey")).__name__,
        }
        raise error

    if not isinstance(result, dict):
        invalid("登录响应的数据结构无法识别，未保存登录信息。")
    user_id, token = result.get("id"), result.get("sessionKey")
    # JSON APIs commonly encode long IDs as strings to preserve their precision in JavaScript.
    if isinstance(user_id, str):
        value = user_id.strip()
        if value.isascii() and value.isdecimal() and len(value) <= 64:
            user_id = int(value)
    if type(user_id) is not int or user_id <= 0:
        invalid("登录响应的账号 ID 不是有效整数或数字字符串，未保存登录信息。")
    if not isinstance(token, str) or not token.strip() or any(ord(c) < 32 or ord(c) == 127 for c in token):
        invalid("登录响应没有可用的 sessionKey，未保存登录信息。")
    return user_id, token


def login_sms(mobile, code):
    if not valid_mobile(mobile) or not (len(code) == 6 and code.isascii() and code.isdecimal()):
        raise PublishError("请检查手机号和 6 位短信验证码。")
    result = API("").call("POST", "/back/icviews/web/smsLogin",
                          json={"mobile": int(mobile), "code": int(code), "scene": 3})
    user_id, token = parse_login_response(result)
    save_json(STATE / "credentials.json", {"userId": user_id,
                                          "encryptedSession": protect(token.encode()).hex()})
    return user_id


class API:
    def __init__(self, token, session=None):
        self.session = session or requests.Session()
        self.headers = {"Authorization": token, "third-session": token}

    def call(self, method, route, **kwargs):
        try:
            response = self.session.request(method, BASE + route, headers=self.headers,
                                            params={"_t": int(time.time() * 1000)},
                                            timeout=(10, 90), allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            kinds = (
                (requests.exceptions.ProxyError, "proxy", "无法连接本机配置的代理。请确认代理正在运行；程序未更改代理设置。"),
                (requests.exceptions.SSLError, "tls", "HTTPS 证书校验或握手失败。程序不会关闭证书校验，请检查网络与证书配置。"),
                (requests.exceptions.ConnectTimeout, "connect_timeout", "连接网站超时，尚未收到服务器响应。"),
                (requests.exceptions.ReadTimeout, "read_timeout", "等待服务器响应超时；请求可能已经生效。请先检查短信或网站结果，不要直接重发。"),
                (requests.exceptions.Timeout, "timeout", "请求超时；结果不确定，未自动重试。"),
                (requests.exceptions.ConnectionError, "connection", "连接中断或域名解析失败；请求结果不确定，未自动重试。"),
            )
            for cls, kind, message in kinds:
                if isinstance(error, cls):
                    raise APIError(kind, message, method, route) from None
            raise APIError("request", "HTTP 请求失败，未自动重试。", method, route) from None
        if response.status_code == 403:
            raise APIError("access_denied", "服务器拒绝访问。可能是账号权限或访问限制，不能据此断定验证码错误。",
                           method, route, 403)
        if response.status_code == 429:
            raise APIError("rate_limit", "服务器限制了请求频率，请稍后再试，不要连续发送短信。", method, route, 429)
        if response.status_code == 401:
            try:
                data = response.json()
                code = data.get("code") if isinstance(data, dict) else None
            except ValueError:
                code = None
            raise APIError("login_required", "服务器要求登录，或已有登录状态已失效。", method, route, 401,
                           code if type(code) is int else None)
        if response.status_code != 200:
            raise APIError("http", "服务器返回异常 HTTP 状态，请求未确认成功。", method, route, response.status_code)
        if not response.content:
            raise APIError("empty_response", "服务器返回了空响应，请求结果不确定，未自动重试。", method, route, 200)
        try:
            result = response.json()
        except ValueError:
            raise APIError("non_json", "服务器返回的不是 JSON 数据，可能是网关或登录页面；请求未确认成功。",
                           method, route, 200) from None
        if not isinstance(result, dict) or result.get("code") != 200:
            code = result.get("code") if isinstance(result, dict) else None
            code = code if type(code) is int else None
            if code == 60001:
                raise APIError("login_required", "登录状态已失效，请重新登录。", method, route, 200, code)
            payload = kwargs.get("json")
            secrets = list(self.headers.values())
            if isinstance(payload, dict):
                secrets.extend(payload.get(key) for key in ("mobile", "code", "sessionKey", "password"))
            message = safe_server_message(result, secrets) if isinstance(result, dict) else ""
            hint = "服务器返回：" + message if message else "服务器拒绝了请求，但未提供原因。"
            error = APIError("server_rejected", hint, method, route, 200, code)
            if message:
                error.diagnostic["serverMessage"] = message
            raise error
        return result.get("data")

    def upload(self, path):
        data = path.read_bytes()
        validate_image(path, data)
        result = self.call("POST", "/back/icviews/common/file/upload/image",
                           files={"file": (path.name, data, MIME[path.suffix.lower()])})
        url = result.get("url") if isinstance(result, dict) else None
        if not online_image(url):
            raise PublishError("Image upload returned an unexpected URL.")
        return url


def check_connection():
    try:
        API("").call("GET", "/back/icviews/wx/semi/topics?current=1&pageSize=1")
    except APIError as error:
        if error.kind != "login_required" or error.diagnostic["serverCode"] != 60001:
            raise
    return "已连接到 ICViews API。此检测不登录、不发送短信、不发布文章。"


def online_image(url):
    if not isinstance(url, str):
        return False
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and not parsed.username and
            parsed.netloc == "icviews-1314446280.cos.ap-beijing.myqcloud.com")


def validate_image(path, data):
    suffix = path.suffix.lower()
    valid = ((suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")) or
             (suffix in (".jpg", ".jpeg") and data.startswith(b"\xff\xd8\xff")) or
             (suffix == ".gif" and data[:6] in (b"GIF87a", b"GIF89a")))
    if not valid or not 0 < len(data) <= 10 * 1024 * 1024:
        raise PublishError(f"Invalid image or larger than 10 MiB: {path}")


def local_image(root, src):
    parsed = urlsplit(src)
    if parsed.scheme in ("http", "https"):
        if not parsed.netloc or parsed.username:
            raise PublishError("Invalid remote image URL.")
        return None
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment:
        raise PublishError(f"Unsupported image reference: {src}")
    path = (root / unquote(parsed.path)).resolve()
    if not path.is_relative_to(root):
        raise PublishError(f"Image is outside the article directory: {src}")
    if not path.is_file():
        raise PublishError(f"Image not found: {path}")
    validate_image(path, path.read_bytes())
    return path


class Article:
    def __init__(self, source, title=None):
        self.source = source.resolve()
        self.raw = self.source.read_text(encoding="utf-8-sig")
        self.md = MarkdownIt("commonmark", {"html": False}).enable("table").enable("strikethrough")
        self.tokens = self.md.parse(self.raw)
        heading = self.tokens[:3]
        if len(heading) == 3 and heading[0].type == "heading_open" and heading[0].tag == "h1":
            self.title = title or heading[1].content
            self.tokens = self.tokens[3:]
        elif title:
            self.title = title
        else:
            raise PublishError("Article needs a leading # title or --title.")
        if not self.title.strip() or len(self.title.encode("utf-16-le")) // 2 > 120:
            raise PublishError("Title must contain 1-120 characters.")
        self.images = []
        self.text = []
        self._walk(self.tokens)
        self.render({})

    def _walk(self, tokens):
        for token in tokens:
            if token.type == "image":
                path = local_image(self.source.parent, token.attrGet("src"))
                if path:
                    self.images.append((token, path, digest(path.read_bytes())))
            if token.type in ("text", "code_inline", "fence", "code_block"):
                self.text.append(token.content)
            if token.children:
                self._walk(token.children)

    def render(self, urls):
        original = []
        for token, path, key in self.images:
            original.append((token, token.attrGet("src")))
            token.attrSet("src", urls.get(key, path.as_uri()))
        try:
            content = self.md.renderer.render(self.tokens, self.md.options, {})
        finally:
            for token, src in original:
                token.attrSet("src", src)
        if not content.strip():
            raise PublishError("Article body is empty.")
        # Mirrors the site's tag-stripped character limit, conservatively including entities.
        from html.parser import HTMLParser
        class Counter(HTMLParser):
            size = 0
            def handle_data(self, data):
                self.size += len(data.encode("utf-16-le")) // 2
            def handle_entityref(self, name):
                self.size += len(name) + 2
            def handle_charref(self, name):
                self.size += len(name) + 3
        counter = Counter(convert_charrefs=False)
        counter.feed(content)
        if counter.size > 100000:
            raise PublishError("Article exceeds the 100000-character body limit.")
        return content


def upload_images(article, api, cache_path, imported=None):
    urls = read_json(cache_path, {})
    if imported:
        for item in read_json(imported, {}).get("images", []):
            if online_image(item.get("url")):
                path = local_image(article.source.parent, item["path"])
                if path:
                    urls[digest(path.read_bytes())] = item["url"]
        save_json(cache_path, urls)
    for index, (_, path, key) in enumerate(article.images, 1):
        if digest(path.read_bytes()) != key:
            raise PublishError(f"Image changed during processing: {path}")
        if not online_image(urls.get(key)):
            urls[key] = api.upload(path)
            save_json(cache_path, urls)
        print(f"图片 {index}/{len(article.images)}：已就绪", flush=True)
    return urls


def submit(api, route, payload, receipt):
    existing = read_json(receipt, {})
    if existing:
        raise PublishError(f"Submission already recorded ({existing.get('status')}). Review {receipt}; do not retry blindly.")
    record = {"status": "unknown", "title": payload["title"], "time": time.time(),
              "payloadHash": digest(json.dumps(payload, sort_keys=True).encode())}
    # Persist before sending: a lost response must never cause an automatic duplicate post.
    save_json(receipt, record)
    result = api.call("POST", route, json=payload)
    record.update(status="confirmed", data=result)
    save_json(receipt, record)
    return result


def run(args):
    if args.command == "doctor":
        print(check_connection())
        return
    if args.command == "auth":
        user_id = input("ICViews userId: ").strip()
        if not user_id.isdecimal():
            raise PublishError("userId must be numeric.")
        token = getpass.getpass("Session (hidden; raw Authorization value, no added Bearer): ").strip()
        if not token or any(c in token for c in "\r\n"):
            raise PublishError("Invalid session.")
        save_json(STATE / "credentials.json", {"userId": int(user_id),
                                               "encryptedSession": protect(token.encode()).hex()})
        print("Credentials encrypted for this Windows user. Network validation occurs on use.")
        return
    if args.command == "logout":
        (STATE / "credentials.json").unlink(missing_ok=True)
        print("Local credential removed.")
        return
    if args.command == "topics":
        _, token = credentials()
        print(json.dumps(API(token).call("GET", "/back/icviews/wx/semi/topics?current=1&pageSize=9999999"), ensure_ascii=False, indent=2))
        return
    article = Article(Path(args.article), args.title)
    print(f"标题：{article.title}\n本地图片：{len(article.images)} 张", flush=True)
    if args.command == "check":
        output = Path(args.output).resolve() if args.output else STATE / "preview.html"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text('<!doctype html><meta charset="utf-8"><title>' + html.escape(article.title) +
                          '</title><style>body{max-width:900px;margin:32px auto;font:16px/1.7 sans-serif}'
                          'img{max-width:100%}pre{overflow:auto;background:#eee;padding:12px}'
                          'table{border-collapse:collapse}td,th{border:1px solid #999;padding:6px}</style><h1>' +
                          html.escape(article.title) + '</h1>' + article.render({}), encoding="utf-8")
        print(f"离线检查通过。预览文件：{output}")
        return
    if args.command == "publish" and not args.yes:
        raise PublishError("Public publishing requires --yes. Use check or draft first.")
    user_id, token = credentials()
    api = API(token)
    receipt = STATE / str(user_id) / (digest(str(article.source).casefold().encode()) + f"-{args.command}.json")
    if args.command in ("draft", "publish") and receipt.exists():
        raise PublishError(f"Previous submission recorded. Review receipt first: {receipt}")
    current_draft = api.call("GET", "/back/icviews/wx/semi/draft?type=0")
    if args.command == "draft" and current_draft and not args.replace_draft:
        raise PublishError("A website draft exists. --replace-draft explicitly allows replacing it.")
    topic_ids = args.topic or []
    if topic_ids:
        topics = api.call("GET", "/back/icviews/wx/semi/topics?current=1&pageSize=9999999")
        allowed = {row["id"] for row in topics.get("rows", [])}
        if not set(topic_ids).issubset(allowed):
            raise PublishError("Unknown topic ID; use topics to list valid IDs.")
    urls = upload_images(article, api, STATE / str(user_id) / "images.json",
                         Path(args.import_map) if args.import_map else None)
    content = article.render(urls)
    if args.command == "upload":
        target = STATE / str(user_id) / "uploaded.html"
        target.write_text(content, encoding="utf-8")
        print(f"Images ready. No article submitted. HTML: {target}")
        return
    payload = {"title": article.title, "content": content, "topicIds": topic_ids, "userId": user_id}
    route = "/back/icviews/wx/semi/posts"
    if args.command == "draft":
        payload["type"] = 0
        route = "/back/icviews/wx/semi/draft"
    submit(api, route, payload, receipt)
    print(f"{'草稿保存' if args.command == 'draft' else '发布'}成功，服务器已确认。提交记录：{receipt}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["auth", "logout", "topics", "doctor", "check", "upload", "draft", "publish"])
    parser.add_argument("article", nargs="?")
    parser.add_argument("--title")
    parser.add_argument("--topic", type=int, action="append")
    parser.add_argument("--import-map", help="Explicitly trust an old path-to-URL map; images must be unchanged")
    parser.add_argument("--output", help="Offline preview output")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--replace-draft", action="store_true")
    args = parser.parse_args()
    if args.command in ("check", "upload", "draft", "publish") and not args.article:
        parser.error("article path is required")
    STATE.mkdir(parents=True, exist_ok=True)
    # One publisher process per Windows account, including check/auth, to protect cache and journal.
    import msvcrt
    with (STATE / "publisher.lock").open("a+b") as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            print("Another publisher is running.", file=sys.stderr)
            return 1
        try:
            run(args)
        except (PublishError, OSError, ValueError, KeyError) as error:
            record_error(error)
            print(f"Stopped: {error}", file=sys.stderr)
            return 1
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

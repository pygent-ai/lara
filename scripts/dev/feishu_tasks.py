#!/usr/bin/env python3
"""本地拉取飞书「任务中心」里我的任务。

首次运行会引导录入自建应用凭证并打开浏览器完成 OAuth 授权；
token 缓存在 ~/.feishu-tasks/，过期自动用 refresh_token 续期，
refresh_token 也失效时会自动重新弹出浏览器授权。

用法:
  python scripts/dev/feishu_tasks.py             # 未完成任务
  python scripts/dev/feishu_tasks.py --done      # 已完成任务
  python scripts/dev/feishu_tasks.py --limit 20  # 最多显示条数
  python scripts/dev/feishu_tasks.py --setup     # 重新录入凭证
  python scripts/dev/feishu_tasks.py --logout    # 清除本地登录状态

前提（飞书开放平台）:
  1. 创建企业自建应用，开通「任务」权限（task:task:readonly 或 task:task）并发布版本；
  2. 在应用「安全设置」里添加重定向 URL: http://localhost:5366/callback
"""

import argparse
import getpass
import http.server
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime
from pathlib import Path

OPEN_BASE = "https://open.feishu.cn"
AUTHORIZE_URL = f"{OPEN_BASE}/open-apis/authen/v1/authorize"
TOKEN_URL = f"{OPEN_BASE}/open-apis/authen/v2/oauth/token"
TASK_LIST_URL = f"{OPEN_BASE}/open-apis/task/v2/tasks"

# 这些错误码表示 user_access_token 失效，强制刷新后重试一次即可
AUTH_ERROR_CODES = {99991661, 99991663, 99991668, 99991679}

CONFIG_DIR = Path.home() / ".feishu-tasks"
CONFIG_FILE = CONFIG_DIR / "config.json"
TOKEN_FILE = CONFIG_DIR / "tokens.json"
REDIRECT_PATH = "/callback"
DEFAULT_PORT = 5366
EXPIRY_BUFFER_SECONDS = 60
# 授权响应里没有 refresh_token_expires_in 时的兜底（文档标注约 30 天）
DEFAULT_REFRESH_TTL = 30 * 24 * 3600
OAUTH_TIMEOUT_SECONDS = 600
# 新版 OAuth 不显式传 scope 时只授予基础身份权限；offline_access 用于下发 refresh_token
OAUTH_SCOPES = "offline_access task:task:read task:task:readonly"


class ApiError(RuntimeError):
    def __init__(self, code, msg):
        super().__init__(f"飞书接口错误 code={code}: {msg}")


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        return {}  # 缓存损坏时按未登录处理，走重新授权


def save_json(path, data):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        path.chmod(0o600)  # 凭证文件仅当前用户可读；Windows 上忽略失败
    except OSError:
        pass


def http_json(url, params=None, data=None, headers=None):
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    payload = json.dumps(data).encode("utf-8") if data is not None else None
    req = urllib.request.Request(
        url, data=payload, headers=headers or {},
        method="POST" if payload is not None else "GET",
    )
    if payload is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise ApiError(exc.code, exc.read().decode("utf-8", "replace")[:300]) from exc
    except urllib.error.URLError as exc:
        raise ApiError("network", str(exc)) from exc


def request_token(payload):
    """换取/刷新 user_access_token，兼容 flat 与 data 包裹两种响应结构。"""
    body = http_json(TOKEN_URL, data=payload)
    nested = body.get("data") if isinstance(body.get("data"), dict) else {}
    access_token = nested.get("access_token") or body.get("access_token")
    if not access_token:
        raise ApiError(body.get("code", -1), json.dumps(body, ensure_ascii=False)[:300])
    now = time.time()
    expires_in = float(nested.get("expires_in") or body.get("expires_in") or 7200)
    refresh_ttl = float(
        nested.get("refresh_token_expires_in") or body.get("refresh_token_expires_in")
        or DEFAULT_REFRESH_TTL
    )
    return {
        "access_token": access_token,
        # 飞书刷新时会滚动下发新的 refresh_token；没下发就沿用旧的
        "refresh_token": nested.get("refresh_token") or body.get("refresh_token")
        or payload.get("refresh_token", ""),
        "expires_at": now + expires_in - EXPIRY_BUFFER_SECONDS,
        "refresh_expires_at": now + refresh_ttl - EXPIRY_BUFFER_SECONDS,
        # 记录飞书实际下发的权限集合，便于诊断“已开通但接口仍报无权限”的问题
        "scope": nested.get("scope") or body.get("scope") or "",
    }


def run_oauth(cfg):
    port = int(cfg.get("port") or DEFAULT_PORT)
    redirect_uri = f"http://localhost:{port}{REDIRECT_PATH}"
    state = secrets.token_urlsafe(16)
    auth_url = AUTHORIZE_URL + "?" + urllib.parse.urlencode({
        "app_id": cfg["app_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "state": state,
        "scope": OAUTH_SCOPES,
    }, quote_via=urllib.parse.quote)

    result = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != REDIRECT_PATH:
                self.send_error(404)
                return
            result.update(
                {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(
                "<meta charset='utf-8'><p>授权成功，请回到终端（可关闭此页面）。</p>".encode()
            )

        def log_message(self, *args):
            pass

    try:
        server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    except OSError as exc:
        sys.exit(
            f"本地端口 {port} 无法监听（{exc}）。请确认开放平台「安全设置」里"
            f"的重定向 URL 为 {redirect_uri}，或确认端口未被占用。"
        )

    print("即将打开浏览器进行飞书授权，请登录并点击「同意授权」……")
    print(f"若浏览器未自动打开，请手动访问：\n{auth_url}")
    webbrowser.open(auth_url)
    server.timeout = 1
    deadline = time.time() + OAUTH_TIMEOUT_SECONDS
    try:
        while not result:
            server.handle_request()
            if time.time() > deadline:
                sys.exit("等待授权超时（10 分钟），请重试。")
    finally:
        server.server_close()

    if result.get("state") != state:
        sys.exit("回调 state 校验失败，请重新运行。")
    if "code" not in result:
        sys.exit(f"授权被取消或失败：{result}")

    tokens = request_token({
        "grant_type": "authorization_code",
        "client_id": cfg["app_id"],
        "client_secret": cfg["app_secret"],
        "code": result["code"],
        "redirect_uri": redirect_uri,
    })
    save_json(TOKEN_FILE, tokens)
    print(f"授权成功，token 已缓存到 {TOKEN_FILE}")
    if tokens.get("scope"):
        print(f"本次授权实际获得的权限: {tokens['scope']}")
    return tokens["access_token"]


def get_access_token(cfg, force_refresh=False):
    tokens = load_json(TOKEN_FILE)
    if not force_refresh and tokens.get("expires_at", 0) > time.time():
        if tokens.get("access_token"):
            return tokens["access_token"]
    if tokens.get("refresh_token") and tokens.get("refresh_expires_at", 0) > time.time():
        try:
            fresh = request_token({
                "grant_type": "refresh_token",
                "client_id": cfg["app_id"],
                "client_secret": cfg["app_secret"],
                "refresh_token": tokens["refresh_token"],
            })
            save_json(TOKEN_FILE, fresh)
            return fresh["access_token"]
        except ApiError as exc:
            print(f"刷新 token 失败（{exc}），改走浏览器重新授权。")
    return run_oauth(cfg)


def api_get(cfg, path, params):
    def attempt(token):
        return http_json(
            f"{OPEN_BASE}{path}", params=params,
            headers={"Authorization": f"Bearer {token}"},
        )

    body = attempt(get_access_token(cfg))
    if body.get("code") in AUTH_ERROR_CODES:
        body = attempt(get_access_token(cfg, force_refresh=True))
    if body.get("code") != 0:
        raise ApiError(body.get("code", -1), body.get("msg", "未知错误"))
    return body.get("data") or {}


def fetch_tasks(cfg, completed):
    items = []
    params = {"completed": "true" if completed else "false", "page_size": "100"}
    while True:
        data = api_get(cfg, "/open-apis/task/v2/tasks", params)
        items.extend(data.get("items") or [])
        next_token = data.get("page_token")
        if not data.get("has_more") or not next_token or next_token == params.get("page_token"):
            return items
        params["page_token"] = next_token


def parse_rfc3339(text):
    try:
        return datetime.fromisoformat(text)
    except (TypeError, ValueError):
        return None


def format_due(item):
    due = item.get("due") or {}
    raw = due.get("date")
    if not raw:
        return ""
    if due.get("is_all_day"):
        return str(raw)  # 全天任务直接给日期，不换时区
    dt = parse_rfc3339(str(raw))
    if dt is None:
        return str(raw)
    text = dt.astimezone().strftime("%Y-%m-%d %H:%M")
    if dt.timestamp() < time.time():
        text += "（已逾期）"
    return text


def describe_when(item, done):
    if done:
        dt = parse_rfc3339(item.get("completed_at")) if item.get("completed_at") else None
        return f"完成于 {dt.astimezone().strftime('%Y-%m-%d %H:%M')}" if dt else ""
    return format_due(item)


def ensure_config(force=False):
    stored = load_json(CONFIG_FILE) if CONFIG_FILE.exists() else {}
    env_id = os.environ.get("FEISHU_APP_ID", "").strip()
    env_secret = os.environ.get("FEISHU_APP_SECRET", "").strip()
    app_id = env_id or stored.get("app_id", "")
    app_secret = env_secret or stored.get("app_secret", "")
    port = int(stored.get("port") or DEFAULT_PORT)
    if not force and app_id and app_secret:
        return {"app_id": app_id, "app_secret": app_secret, "port": port}

    print("需要飞书自建应用凭证（开放平台 → 你的应用 → 凭证与基础信息）。")
    try:
        app_id = input(f"App ID{f' [{app_id}]' if app_id else ''}: ").strip() or app_id
        if not env_secret:
            hint = "，回车沿用已保存的" if app_secret else ""
            app_secret = getpass.getpass(f"App Secret（输入不回显{hint}）: ").strip() or app_secret
    except EOFError:
        sys.exit("当前终端无法交互输入。请编辑 ~/.feishu-tasks/config.json 直接填入 "
                 "app_id / app_secret，或设置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量。")
    if not app_id or not app_secret:
        sys.exit("缺少 App ID / App Secret。请先在开放平台创建自建应用，"
                 "或设置 FEISHU_APP_ID / FEISHU_APP_SECRET 环境变量。")
    if not env_id or not env_secret:
        save_json(CONFIG_FILE, {"app_id": app_id, "app_secret": app_secret, "port": port})
    return {"app_id": app_id, "app_secret": app_secret, "port": port}


def main():
    parser = argparse.ArgumentParser(description="拉取飞书任务中心的我的任务")
    parser.add_argument("--done", action="store_true", help="查看已完成任务")
    parser.add_argument("--limit", type=int, default=None, help="最多显示条数")
    parser.add_argument("--setup", action="store_true", help="重新录入应用凭证")
    parser.add_argument("--logout", action="store_true", help="清除本地 token")
    args = parser.parse_args()

    if args.logout:
        TOKEN_FILE.unlink(missing_ok=True)
        print(f"已清除本地登录状态（{TOKEN_FILE}）。")
        return

    cfg = ensure_config(force=args.setup)
    try:
        items = fetch_tasks(cfg, completed=args.done)
    except ApiError as exc:
        sys.exit(str(exc))

    if args.limit:
        items = items[: args.limit]
    label = "已完成任务" if args.done else "未完成任务"
    if not items:
        print(f"没有{label}。")
        return
    print(f"{label}（{len(items)} 条）")
    for i, item in enumerate(items, 1):
        summary = (item.get("summary") or "").strip() or "（无标题）"
        when = describe_when(item, args.done)
        print(f"{i:>3}. {summary}" + (f"  |  {when}" if when else ""))


if __name__ == "__main__":
    main()

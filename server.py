#!/usr/bin/env python3
# 青鸟 Bluebird —— Webhook → Bark/飞书/企业微信 通知网关
# Python 标准库单文件：多来源 webhook + 签名校验 + 事件过滤去重 + 多渠道推送 + 审计
import base64
import contextlib
import datetime
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

HOST = os.environ.get("NOTIFY_HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT") or os.environ.get("NOTIFY_PORT") or "8082")
SECRET = os.environ.get("WEBHOOK_SECRET", "")
NOTIFY_OWNER = os.environ.get("NOTIFY_OWNER", "")
BARK_URL = os.environ.get("BARK_URL", "https://api.day.app").rstrip("/")
BARK_KEY = os.environ.get("BARK_KEY", "")
BARK_SOURCE = os.environ.get("BARK_SOURCE", "github")
FEISHU_BASE_URL = os.environ.get("FEISHU_BASE_URL", "https://open.feishu.cn").rstrip("/")
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
# 接收目标：飞书 IM API 必须显式指定收件人，类型见 FEISHU_RECEIVE_ID_TYPES
FEISHU_RECEIVE_ID = os.environ.get("FEISHU_RECEIVE_ID", "")
FEISHU_RECEIVE_ID_TYPE = os.environ.get("FEISHU_RECEIVE_ID_TYPE", "chat_id")
WECOM_WEBHOOK = os.environ.get("WECOM_WEBHOOK", "")
PUSHDEER_URL = os.environ.get("PUSHDEER_URL", "https://api2.pushdeer.com").rstrip("/")
PUSHDEER_KEY = os.environ.get("PUSHDEER_KEY", "")
SLACK_WEBHOOK = os.environ.get("SLACK_WEBHOOK", "")
GENERIC_WEBHOOK_URL = os.environ.get("GENERIC_WEBHOOK_URL", "")
GENERIC_WEBHOOK_SECRET = os.environ.get("GENERIC_WEBHOOK_SECRET", "")
GENERIC_TOKEN = os.environ.get("GENERIC_TOKEN", "")
DB_PATH = os.environ.get("NOTIFY_DB", "/opt/bluebird/bluebird.db")
VERSION_FILE = os.path.join(os.path.dirname(DB_PATH), "version")
# 本次进程启动时刻（epoch 秒）：随 /health 暴露，面板作为「部署时间」展示（容器每次部署都会重启）
START_TIME = int(time.time())
DEDUP_SECONDS = int(os.environ.get("DEDUP_SECONDS", "60"))
LOG_RETENTION_DAYS = int(os.environ.get("LOG_RETENTION_DAYS", "30"))
# 显示时区白名单：仅允许这些 tz 字符串写入 settings.display.tz，避免任意字符串注入
# zoneinfo 失败或污染；与 ui.html 的 TZ_VALUES 保持一致。
TZ_WHITELIST = ("Asia/Shanghai", "UTC", "Asia/Tokyo", "Asia/Singapore",
                "Europe/London", "America/New_York", "America/Los_Angeles")
DEFAULT_DISPLAY_TZ = "Asia/Shanghai"
UI_AUTH_USER = os.environ.get("NOTIFY_AUTH_USER", "")
UI_AUTH_PASS = os.environ.get("NOTIFY_AUTH_PASS", "")
UI_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
LOGIN_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "login.html")
FAVICON_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "favicon.ico")
# 主题色 favicon：与 ui.html / login.html 的五色主题（--brand-700）一一对应，
# 按 /favicon-<theme>.svg 动态生成（零额外文件）；favicon.ico 保留作旧浏览器回退。
FAVICON_THEME_COLOR = {
    "indigo": "#4338ca",
    "emerald": "#047857",
    "rose": "#be123c",
    "amber": "#b45309",
    "slate": "#334155",
}
FAVICON_SVG_TEMPLATE = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" width="32" height="32" role="img" aria-label="青鸟 Bluebird">
  <title>青鸟 Bluebird</title>
  <g transform="translate(1.3382 1.3445) scale(1.2218)" fill="none" stroke="{color}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M16 7h.01"/>
    <path d="M3.4 18H12a8 8 0 0 0 8-8V7a4 4 0 0 0-7.28-2.3L2 20"/>
    <path d="m20 7 2 .5-2 .5"/>
    <path d="M10 18v3"/>
    <path d="M14 17.75V21"/>
    <path d="M7 18a6 6 0 0 0 3.84-10.61"/>
  </g>
</svg>"""
# 审计面板登录会话时长（小时）
UI_SESSION_HOURS = int(os.environ.get("UI_SESSION_HOURS", "24"))
# 面板会话签名密钥：独立于 WEBHOOK_SECRET；未配置时首启随机生成并持久化到数据库
UI_SESSION_SECRET = os.environ.get("UI_SESSION_SECRET", "")
# 单个请求体上限（字节，默认 1MB）：防止超大 body 占用内存与线程
MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES") or 1024 * 1024)
# 单连接读写超时（秒，默认 30）：防止慢连接 / 半开连接长期占用线程
SOCKET_TIMEOUT = int(os.environ.get("NOTIFY_TIMEOUT") or 30)
# 子路径部署前缀（如 https://example.com/bluebird/hooks/github → "bluebird"）
# 默认根路径部署（子域名，如 https://bluebird.example.com/hooks/github）；需要子路径时显式设置
BASE_PATH = os.environ.get("BASE_PATH", "").strip("/")

# 机器人账号不通知（避免 dependabot 等刷屏），可扩展
BOT_LOGINS = {"dependabot[bot]", "renovate[bot]", "github-actions[bot]", "dependabot-preview[bot]"}

# 支持的 GitHub 来源事件（用于运行时启停开关）
EVENT_TYPES = ("watch", "fork", "issues", "issue_comment", "pull_request", "workflow_run")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bluebird")


@contextlib.contextmanager
def db():
    d = os.path.dirname(DB_PATH)
    if d:
        os.makedirs(d, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    with contextlib.suppress(OSError):
        os.chmod(DB_PATH, 0o600)   # 库内含渠道 key / token，收紧到仅服务进程可读
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS seen (delivery TEXT PRIMARY KEY, ts INTEGER)")
        conn.execute("CREATE TABLE IF NOT EXISTS push_log ("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL,"
                     "channel TEXT NOT NULL, source TEXT NOT NULL DEFAULT '',"
                     "event_type TEXT NOT NULL DEFAULT '',"
                     "repo TEXT NOT NULL DEFAULT '', title TEXT NOT NULL,"
                     "body TEXT NOT NULL DEFAULT '', status TEXT NOT NULL)")
        try:
            conn.execute("ALTER TABLE push_log ADD COLUMN source TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError:
            pass  # 旧库已存在 source 列
        conn.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        yield conn
        conn.commit()
    finally:
        conn.close()


def seen(delivery):
    """delivery 在去重窗口内出现过 → True（丢弃）；否则记录并返回 False。"""
    now = int(time.time())
    with db() as conn:
        conn.execute("DELETE FROM seen WHERE ts < ?", (now - DEDUP_SECONDS,))
        if conn.execute("SELECT 1 FROM seen WHERE delivery = ?", (delivery,)).fetchone():
            return True
        conn.execute("INSERT INTO seen(delivery, ts) VALUES(?, ?)", (delivery, now))
    return False


def log_push(channel, event_type, repo, title, body, status, ts=None, source=""):
    """记录一条推送审计日志，并清理过期记录。"""
    ts = int(time.time()) if ts is None else int(ts)
    with db() as conn:
        conn.execute(
            "INSERT INTO push_log(ts, channel, source, event_type, repo, title, body, status)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (ts, channel, source, event_type, repo, title, body, status))
        conn.execute("DELETE FROM push_log WHERE ts < ?",
                     (ts - retention_days() * 86400,))


def _log_where(channel="", event_type="", days=7, source="", since=None):
    """构建审计日志查询的 WHERE 条件与参数（query_logs / count_logs 共用）。
    since：epoch 秒下限（ts >= since），与 days 条件可叠加；不传则行为不变。"""
    where, args = [], []
    if channel:
        where.append("channel = ?")
        args.append(channel)
    if event_type:
        where.append("event_type = ?")
        args.append(event_type)
    if source:
        where.append("source = ?")
        args.append(source)
    if days:
        where.append("ts >= ?")
        args.append(int(time.time()) - int(days) * 86400)
    if since is not None:
        where.append("ts >= ?")
        args.append(int(since))
    return where, args


def query_logs(channel="", event_type="", days=7, limit=100, source="", offset=0, since=None):
    """按条件查询审计日志，按时间倒序，支持 offset 分页。"""
    where, args = _log_where(channel, event_type, days, source, since)
    sql = ("SELECT id, ts, channel, source, event_type, repo, title, body, status"
           " FROM push_log")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ts DESC LIMIT ? OFFSET ?"
    args += [int(limit), int(offset)]
    with db() as conn:
        rows = conn.execute(sql, args).fetchall()
    cols = ("id", "ts", "channel", "source", "event_type", "repo", "title", "body", "status")
    return [dict(zip(cols, r)) for r in rows]


def count_logs(channel="", event_type="", days=7, source="", since=None):
    """统计符合条件的总条数与成功/失败数（供分页与 KPI 使用）。"""
    where, args = _log_where(channel, event_type, days, source, since)
    sql = ("SELECT COUNT(*), "
           "COALESCE(SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END), 0)"
           " FROM push_log")
    if where:
        sql += " WHERE " + " AND ".join(where)
    with db() as conn:
        row = conn.execute(sql, args).fetchone()
    return {"total": row[0], "ok": row[1], "error": row[0] - row[1]}


STAT_GROUPS = {"source": "source", "channel": "channel", "status": "status"}


def query_stats(days=30, group="source"):
    """按日聚合推送统计（日期轴为 settings.display.tz 自然日，缺数据日期补 0）。
    group: source / channel / status，返回 {"labels": [日期...], "series": [{"key", "values"}...]}。
    时区分桶在 Python 内做：epoch → tz-aware datetime → Y-M-D；这样任何 tz（含 DST）都准确，
    与前端管理界面 / 消息卡片页脚共用同一权威 display.tz。"""
    col = STAT_GROUPS.get(group)
    if col is None:
        raise ValueError(f"unknown stats group: {group}")
    days = max(1, min(int(days), 3650))
    zone = _tz_zone()
    today = datetime.datetime.now(zone).date()
    start = today - datetime.timedelta(days=days - 1)   # 含今天共 days 个自然日
    # cutoff = 起始日 0:00 (tz 内) 对应的 epoch 秒；窗口外行不进分组
    cutoff = int(datetime.datetime(start.year, start.month, start.day, tzinfo=zone).timestamp())
    with db() as conn:
        rows = conn.execute(
            f"SELECT ts, CASE WHEN {col} = '' THEN '(空)' ELSE {col} END AS k "
            f"FROM push_log WHERE ts >= ?", (cutoff,)).fetchall()
    acc = {}
    for ts, k in rows:
        d = datetime.datetime.fromtimestamp(int(ts), zone).strftime("%Y-%m-%d")
        acc.setdefault(k, {})[d] = acc.get(k, {}).get(d, 0) + 1
    # 日期轴：起始日（含）到今天（含），按 tz 内的自然日列出
    labels = []
    d = start
    while d <= today:
        labels.append(d.strftime("%Y-%m-%d"))
        d += datetime.timedelta(days=1)
    # status 分组固定输出 ok/error 两序列（失败可能为 0 也要显示）；其余分组动态
    keys = ["ok", "error"] if group == "status" else list(acc.keys())
    series = [{"key": k, "values": [acc.get(k, {}).get(d, 0) for d in labels]} for k in keys]
    return {"labels": labels, "series": series}


def clear_logs():
    """清空全部推送历史，返回删除条数。"""
    with db() as conn:
        cur = conn.execute("DELETE FROM push_log")
    return cur.rowcount


def get_settings():
    """读取全部运行设置（key -> value）。"""
    with db() as conn:
        return dict(conn.execute("SELECT key, value FROM settings").fetchall())


def set_setting(key, value):
    """写入运行设置（key 形如 event.<type> / channel.<name> / source.<name>）。"""
    with db() as conn:
        conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                     "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))


def is_enabled(prefix, name, default="1"):
    """开关是否启用（未设置用 default）。"""
    return get_settings().get(f"{prefix}.{name}", default) != "0"


def retention_days():
    """日志保留天数：settings.retention.days 优先，未配置时用环境变量默认值。"""
    try:
        return max(1, min(int(get_settings().get("retention.days", LOG_RETENTION_DAYS)), 3650))
    except (TypeError, ValueError):
        return LOG_RETENTION_DAYS


def display_tz():
    """系统设置里的显示时区：管理界面、消息卡片时间戳、统计日期轴共用的权威值。
    仅接受白名单 TZ_WHITELIST（与 ui.html 的 TZ_VALUES 对齐），未配置或非法值回落到默认。"""
    raw = get_settings().get("display.tz", DEFAULT_DISPLAY_TZ)
    return raw if raw in TZ_WHITELIST else DEFAULT_DISPLAY_TZ


def _tz_zone():
    """显示时区对应的 zoneinfo；解析失败时回退默认（防御性，不应触发：display_tz 已白名单）。"""
    try:
        return ZoneInfo(display_tz())
    except Exception:
        return ZoneInfo(DEFAULT_DISPLAY_TZ)


def _format_ts(ts):
    """按 settings.display.tz 把 epoch 秒格式化为 'YYYY-MM-DD HH:MM:SS'。
    飞书/Slack/Slack-text 卡片页脚时间戳共用——与前端 Intl 走的同一时区。"""
    return datetime.datetime.fromtimestamp(int(ts), _tz_zone()).strftime("%Y-%m-%d %H:%M:%S")


# ---------- 动态配置模型（渠道 / 通知源实例，settings 表 JSON 存储；首次调用时从环境变量迁移） ----------

CHANNEL_TYPES = ("bark", "feishu", "wecom", "slack", "pushdeer", "webhook")
SOURCE_TYPES = ("github", "generic")
# 飞书接收目标类型（im/v1/messages 的 receive_id_type）
FEISHU_RECEIVE_ID_TYPES = ("chat_id", "open_id", "user_id", "union_id", "email")


def _default_channels_from_env():
    """从环境变量构建初始渠道实例（兼容旧部署，一次性迁移）。"""
    lst = []
    if BARK_KEY:
        lst.append({"name": "bark", "type": "bark",
                    "config": {"url": BARK_URL, "key": BARK_KEY, "source": BARK_SOURCE}})
    if FEISHU_APP_ID:
        lst.append({"name": "feishu", "type": "feishu",
                    "config": {"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET,
                               "receive_id": FEISHU_RECEIVE_ID,
                               "receive_id_type": FEISHU_RECEIVE_ID_TYPE}})
    if WECOM_WEBHOOK:
        lst.append({"name": "wecom", "type": "wecom",
                    "config": {"webhook": WECOM_WEBHOOK}})
    if SLACK_WEBHOOK:
        lst.append({"name": "slack", "type": "slack",
                    "config": {"webhook": SLACK_WEBHOOK}})
    if PUSHDEER_KEY:
        lst.append({"name": "pushdeer", "type": "pushdeer",
                    "config": {"url": PUSHDEER_URL, "key": PUSHDEER_KEY}})
    if GENERIC_WEBHOOK_URL:
        lst.append({"name": "webhook", "type": "webhook",
                    "config": {"url": GENERIC_WEBHOOK_URL, "secret": GENERIC_WEBHOOK_SECRET}})
    return lst


def _default_sources_from_env():
    """从环境变量构建初始通知源实例（兼容旧部署，一次性迁移）。"""
    lst = [{"name": "github", "type": "github", "config": {"secret": SECRET}}]
    if GENERIC_TOKEN:
        lst.append({"name": "generic", "type": "generic", "config": {"token": GENERIC_TOKEN}})
    return lst


SOURCE_ID_BYTES = 9   # token_urlsafe(9) → 12 字符，URL 安全


def _new_source_ids(items):
    """给缺失 id 的来源补一个稳定 ID（懒迁移）；与已有 name/id 不冲突。返回是否有变更。"""
    taken = {v for it in items for v in (it.get("id"), it.get("name")) if v}
    changed = False
    for it in items:
        if not it.get("id"):
            while True:
                sid = secrets.token_urlsafe(SOURCE_ID_BYTES)
                if sid not in taken:
                    break
            it["id"] = sid
            taken.add(sid)
            changed = True
    return changed


def _load_list(key, fallback, ensure_source_id=False):
    """读取 JSON 实例列表；从未配置时用 fallback 生成并落库（懒迁移）。
    ensure_source_id：来源列表补全缺失的稳定 ID（/hooks/<ID> 不随改名失效）。"""
    raw = get_settings().get(key)
    if raw is None:
        lst = fallback()
        if ensure_source_id:
            _new_source_ids(lst)
        set_setting(key, json.dumps(lst, ensure_ascii=False))
        return lst
    try:
        lst = json.loads(raw)
    except ValueError:
        log.warning("配置 %s 损坏，按空列表处理", key)
        return []
    if ensure_source_id and isinstance(lst, list) and _new_source_ids(lst):
        set_setting(key, json.dumps(lst, ensure_ascii=False))
    return lst


def get_channels():
    """全部渠道实例（[{name,type,config}]）。"""
    return _load_list("channels.list", _default_channels_from_env)


def set_channels(lst):
    set_setting("channels.list", json.dumps(lst, ensure_ascii=False))


def get_sources():
    """全部通知源实例（[{id,name,type,config}]）。"""
    return _load_list("sources.list", _default_sources_from_env, ensure_source_id=True)


def set_sources(lst):
    set_setting("sources.list", json.dumps(lst, ensure_ascii=False))


def find_source(key):
    """按 ID 或名称定位来源：ID 优先（改名后调用方不受影响），其次名称（兼容历史地址）。"""
    items = get_sources()
    for s in items:
        if s.get("id") and s["id"] == key:
            return s
    for s in items:
        if s.get("name") == key:
            return s
    return None


# 通知源名称会拼进 Webhook 路径（/hooks/<名称>），仅允许 URL 路径安全字符
INSTANCE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,32}$")
# 渠道名称只作为展示名与 settings key 使用（不进 URL），因此允许任意可打印字符（含中文）
CHANNEL_NAME_MAX = 32


def _name_error(name):
    """校验通知源名称，返回错误信息或 None。"""
    if not name:
        return "名称必填"
    if len(name) > 32:
        return "名称不超过 32 字符"
    if not INSTANCE_NAME_RE.match(name):
        return "名称仅支持字母、数字、-、_、."
    return None


def _channel_name_error(name):
    """校验渠道名称：任意可打印字符（含中文）均可，仅限长度并排除控制字符。"""
    if not name:
        return "渠道名必填"
    if len(name) > CHANNEL_NAME_MAX:
        return f"渠道名不超过 {CHANNEL_NAME_MAX} 个字符"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in name):
        return "渠道名不能包含换行、制表符等控制字符"
    return None


def channel_name_taken(name, original_name=""):
    """渠道名是否已被其它渠道占用（改名时忽略自身）。"""
    return any(c["name"] == name and c["name"] != original_name for c in get_channels())


def rename_history(col, old, new):
    """实例改名后同步历史：推送日志、启停开关、来源渠道引用跟随新名称。"""
    with db() as conn:
        conn.execute(f"UPDATE push_log SET {col}=? WHERE {col}=?", (new, old))
        prefix = "channel" if col == "channel" else "source"
        conn.execute("UPDATE settings SET key=? WHERE key=?",
                     (f"{prefix}.{new}", f"{prefix}.{old}"))
    if col == "channel":
        # 通知源实例 config.channels 里引用的旧渠道名一并更新
        items = get_sources()
        changed = False
        for s in items:
            chs = (s.get("config") or {}).get("channels")
            if chs and old in chs:
                s.setdefault("config", {})["channels"] = [new if c == old else c for c in chs]
                changed = True
        if changed:
            set_sources(items)


def channel_error(item):
    """校验渠道实例，返回错误信息或 None。"""
    name = str(item.get("name") or "").strip()
    typ = str(item.get("type") or "")
    cfg = item.get("config") or {}
    err = _channel_name_error(name)
    if err:
        return err
    if typ not in CHANNEL_TYPES:
        return f"未知渠道类型: {typ}"
    if typ == "bark":
        if not cfg.get("key"):
            return "Bark 需要填写 Key"
    elif typ == "feishu":
        if not cfg.get("app_id"):
            return "飞书需要填写 App ID"
        if not cfg.get("app_secret"):
            return "飞书需要填写 App Secret"
        if not cfg.get("receive_id"):
            return "飞书需要填写接收 ID（群 chat_id / 用户 open_id 等）"
        if cfg.get("receive_id_type", "chat_id") not in FEISHU_RECEIVE_ID_TYPES:
            return f"未知的飞书接收 ID 类型: {cfg.get('receive_id_type')}"
        if cfg.get("style") and cfg["style"] not in FEISHU_STYLES:
            return f"未知的飞书消息样式: {cfg['style']}"
    elif typ == "pushdeer":
        if not cfg.get("key"):
            return "PushDeer 需要填写 Key"
    elif typ == "slack":
        if not cfg.get("webhook"):
            return "Slack 需要填写 Webhook URL"
        if cfg.get("style") and cfg["style"] not in SLACK_STYLES:
            return f"未知的 Slack 消息样式: {cfg['style']}"
    elif typ == "webhook":
        if not cfg.get("url"):
            return "通用 Webhook 需要填写 URL"
    else:
        if not cfg.get("webhook"):
            return "需要填写 Webhook 地址"
    return None


def source_error(item):
    """校验通知源实例，返回错误信息或 None。"""
    name = str(item.get("name") or "").strip()
    typ = str(item.get("type") or "")
    cfg = item.get("config") or {}
    err = _name_error(name)
    if err:
        return err
    if typ not in SOURCE_TYPES:
        return f"未知通知源类型: {typ}"
    for k in ("channels", "events"):
        v = cfg.get(k)
        if v is not None and not isinstance(v, list):
            return f"{k} 必须是列表"
    return None


def verify(signature, body, secret=SECRET):
    if not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature, expected)


def _open_json(req):
    """发请求并解析 JSON 响应。

    HTTP 4xx/5xx 也尝试解析响应体：飞书、企业微信的错误码都在 body 里，
    让 HTTPError 直接冒出去，面板就只剩一句「HTTP Error 400」看不到原因。
    """
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        try:
            return json.loads(body)
        except ValueError:
            raise RuntimeError(f"HTTP {e.code}: {body.strip()[:200]}") from e


def _post_json(url, payload, headers=None):
    """POST JSON 并解析响应（Bark/飞书/企业微信通用）。"""
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), headers=hdrs, method="POST")
    return _open_json(req)


def _post_form(url, payload):
    """POST form-encoded 并解析响应（PushDeer 用 form 参数，不用 JSON）。"""
    req = urllib.request.Request(
        url, data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    return _open_json(req)


def _post_raw(url, body, headers):
    """POST 原始 body，返回响应是否 2xx（通用 Webhook：响应体不解析，任意内容都算成功）。"""
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        return 200 <= r.status < 300


# tenant_access_token 有效期 2h，按应用缓存避免每条通知都换一次
_feishu_tokens = {}

# 实测自飞书开放平台：把最常见的失败码翻成能直接照做的中文，供面板「测试推送」显示
FEISHU_ERROR_HINTS = {
    10003: "App ID / App Secret 不完整或不合法",
    10014: "App ID 或 App Secret 不正确",
    230001: "接收 ID 无效：请核对接收 ID 与其类型是否匹配（群用 oc_ 开头的 chat_id）",
    99992402: "接收 ID 类型不合法：可选 chat_id / open_id / user_id / union_id / email",
    99991672: "应用权限不足：请在开发者后台开通 im:message:send_as_bot（或 im:message）并发布新版本",
}


def _feishu_error_hint(res):
    """命中已知错误码时返回可操作的中文提示，否则返回空串。"""
    code = res.get("code") if isinstance(res, dict) else None
    hint = FEISHU_ERROR_HINTS.get(code)
    return f"{hint}（飞书 code={code}）" if hint else ""


def _feishu_token(app_id, app_secret):
    """获取（并缓存）飞书应用的 tenant_access_token；失败时抛异常。"""
    key = (app_id, app_secret)
    cached = _feishu_tokens.get(key)
    if cached and cached[1] - 60 > time.time():
        return cached[0]
    res = _post_json(f"{FEISHU_BASE_URL}/open-apis/auth/v3/tenant_access_token/internal",
                     {"app_id": app_id, "app_secret": app_secret})
    token = res.get("tenant_access_token") if isinstance(res, dict) else ""
    if not token:
        raise RuntimeError("获取飞书 tenant_access_token 失败："
                           f"{_feishu_error_hint(res) or res}")
    try:
        expire = int(res.get("expire") or 7200)
    except (TypeError, ValueError):
        expire = 7200
    _feishu_tokens[key] = (token, time.time() + expire)
    return token


# ---- Message Card ----
# 中间表示 + 全局唯一调色板（写死，不开放用户配置）。
# 事件 key → (中性颜色名, 中文标签, 飞书 header.template, Slack attachment.hex)
EVENT_PALETTE = {
    "watch":          ("star",    "新 Star",  "yellow",    "#dfac03"),
    "fork":           ("fork",    "新 Fork",  "purple",    "#6f42c1"),
    "issues":         ("issue",   "新 Issue", "orange",    "#d93f0b"),
    "issue_comment":  ("comment", "新评论",   "turquoise", "#0d8a8a"),
    "pull_request":   ("pr",      "新 PR",    "blue",      "#0969da"),
    "workflow_run":   ("ci",      "CI 状态",  None,        None),  # 配色由 accent_color 决定
    "release":        ("release", "新发布",   "green",     "#2da44e"),
    "generic":        ("generic", "通知",     "blue",      "#586069"),
    "test":           ("test",    "测试推送", "blue",      "#586069"),
}
# workflow_run 按结果（success/failure）决定具体配色
EVENT_PALETTE_COLOR = {
    "green": ("ci_pass", "通过", "green", "#2da44e"),
    "red":   ("ci_fail", "失败", "red",   "#cf222e"),
}
EVENT_PALETTE_DEFAULT = ("generic", "通知", "blue", "#586069")


def _event_palette_row(event, color=""):
    """按 (event, color) 在 EVENT_PALETTE 中查一行；未知 key 或 color=untracked 回落到默认值。
    color 仅 workflow_run 使用（green=red 决定 CI 通过/失败），其他事件忽略。"""
    if event == "workflow_run" and color in EVENT_PALETTE_COLOR:
        return EVENT_PALETTE_COLOR[color]
    return EVENT_PALETTE.get(str(event or ""), EVENT_PALETTE_DEFAULT)


@dataclass
class Card:
    """渠道无关的卡片中间表示：notify() 把来源层 meta 装配成 Card，再下发到渠道渲染层。"""
    title: str
    accent: str = "generic"          # 事件 key（watch/issue_comment/...）
    accent_color: str = ""           # 仅 workflow_run：green / red
    fields: list = field(default_factory=list)   # [(label, value), ...]
    body: str = ""
    action_url: str = ""
    source: str = ""                 # 来源名；test 为空
    timestamp: int = 0               # unix 秒；0 → 渠道层用"现在"
    kind: str = "default"            # default | text（仅飞书/Slack 用）


def assemble_card(title, body, meta=None):
    """把来源层产出的 (title, body, meta) 装配成 Card。
    - meta 仍是 untyped 字典（向后兼容）
    - 字段去冗余规则：watch/fork 的 actor 不进 fields（已出现在 title）；其他事件仍进
    - workflow_run 的 accent 由 meta.color（green/red）走 EVENT_PALETTE_COLOR 表
    """
    meta = meta or {}
    event = str(meta.get("event") or "generic")
    accent = event
    accent_color = ""
    if event == "workflow_run":
        accent_color = str(meta.get("color") or "").lower()
    repo = str(meta.get("repo") or "").strip()
    actor = str(meta.get("actor") or "").strip()
    branch = str(meta.get("branch") or "").strip()

    fields = []
    if event in ("watch", "fork"):
        # title 已写「⭐ X star 了 Y」/「🍴 X fork 了 Y」，actor 不进 fields
        if repo:
            fields.append(("仓库", repo))
    else:
        if repo:
            fields.append(("仓库", repo))
        if actor:
            fields.append(("提交人", actor))
    if branch:
        fields.append(("分支", branch))
    if event == "workflow_run" and meta.get("wf_status"):
        fields.append(("状态", str(meta["wf_status"])))
    if meta.get("labels"):
        fields.append(("标签", str(meta["labels"])))
    if meta.get("commit"):
        fields.append(("commit", str(meta["commit"])))
    if meta.get("comment"):
        fields.append(("评论摘录", str(meta["comment"])))

    return Card(
        title=str(title or ""),
        accent=accent,
        accent_color=accent_color,
        fields=fields,
        body=str(body or ""),
        action_url=str(meta.get("url") or ""),
        source=str(meta.get("source") or ""),
        timestamp=int(meta.get("timestamp") or 0),
        kind=str(meta.get("kind") or "default"),
    )


# ---- 预置示例事件（设计稿 §7 / B1）----
# 面板「示例事件推送」用固定 payload 走真实渠道（验证配色切换与 fields 渲染），不读历史数据。
# 夹具随镜像发布到 /app/fixtures/；本地跑（仓库根目录）回落到 tests/fixtures/。
_EXAMPLE_EVENTS_PATHS = (
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "example_events.json"),
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "tests", "fixtures", "example_events.json"),
)
# 设计稿里写的是 success/failure，夹具 key 用 pass/fail，两种写法都认
_EXAMPLE_EVENT_ALIASES = {"workflow_run_success": "workflow_run_pass",
                          "workflow_run_failure": "workflow_run_fail"}

_example_events_cache = None


def example_events():
    """预置示例事件：{key: {"title","body","meta"}}（保持夹具顺序）。
    夹具缺失时返回空 dict —— 面板的示例事件入口随之隐藏，不影响正常推送。"""
    global _example_events_cache
    if _example_events_cache is not None:
        return _example_events_cache
    data = {}
    for path in _EXAMPLE_EVENTS_PATHS:
        try:
            with open(path, encoding="utf-8") as f:
                data = {ev["key"]: ev for ev in json.load(f).get("events") or [] if ev.get("key")}
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if data:
            break
    if not data:
        log.warning("示例事件夹具未找到（%s）：面板「示例事件推送」不可用", _EXAMPLE_EVENTS_PATHS[0])
    _example_events_cache = data
    return data


def example_event_options():
    """面板下拉用的 [{key,label}]：label 由 EVENT_PALETTE 推导成中文，保持夹具顺序。"""
    out = []
    for key, ev in example_events().items():
        meta = ev.get("meta") or {}
        event = str(meta.get("event") or "generic")
        _name, label, _template, _hex = _event_palette_row(event, str(meta.get("color") or ""))
        out.append({"key": key, "label": f"CI {label}" if event == "workflow_run" else label})
    return out


# 飞书消息样式：卡片（默认，彩色标题栏 + 分栏 + 跳转按钮）或纯文本（一行标题一行正文）
FEISHU_STYLES = ("card", "text")


def _feishu_md(text):
    return {"tag": "lark_md", "content": str(text)}


def _feishu_field(label, value):
    return {"is_short": True, "text": _feishu_md(f"**{label}**\n{value}")}


def _feishu_card(card, *legacy):
    """飞书卡片 1.0。
    新签名：_feishu_card(card)
    旧签名（测试 shim）：_feishu_card(title, body, meta, source) — 内部装 Card 再渲染
    """
    if not isinstance(card, Card):
        title, body, meta, source = card, legacy[0], legacy[1], legacy[2]
        merged = {"source": source or ""}
        if isinstance(meta, dict):
            merged.update(meta)
        card = assemble_card(title, body, merged)
    _name, _label, template, _hex = _event_palette_row(card.accent, card.accent_color)
    template = template or "blue"

    field_elements = [_feishu_field(lbl, val) for lbl, val in card.fields]
    elements = []
    if field_elements:
        elements.append({"tag": "div", "fields": field_elements})
    if card.body:
        if elements:
            elements.append({"tag": "hr"})
        elements.append({"tag": "div", "text": _feishu_md(card.body)})
    if card.action_url.startswith(("http://", "https://")):
        elements.append({"tag": "action", "actions": [{
            "tag": "button", "type": "primary", "url": card.action_url,
            "text": {"tag": "plain_text", "content": "查看详情"}}]})
    # test 推送的 source 是占位值，不进页脚
    origin = "" if card.source == "test" else card.source
    ts_text = "—"
    if card.timestamp:
        ts_text = _format_ts(card.timestamp)
    footer_parts = [x for x in (origin, "青鸟 Bluebird", ts_text) if x]
    elements.append({"tag": "note",
                     "elements": [{"tag": "plain_text", "content": " · ".join(footer_parts)}]})
    return {"config": {"wide_screen_mode": True},
            "header": {"template": template,
                       "title": {"tag": "plain_text", "content": card.title}},
            "elements": elements}


# Slack 消息样式：card = Block Kit 卡片（彩色侧栏 + header + 上下文，Slack 默认），text = 一段纯文本
SLACK_STYLES = ("card", "text")

# 事件 → header emoji（Slack 用，让 header 与具体事件标题合并可读）
_SLACK_EMOJI = {
    "watch": "⭐", "fork": "🍴", "issues": "📌", "issue_comment": "💬",
    "pull_request": "🔀", "workflow_run": "🛠️", "release": "🚀",
    "generic": "📢", "test": "🔔",
}


def _slack_field(label, value):
    """Block Kit section.fields 一格：粗体标签 + 换行 + 值；空值返回 None 便于过滤。"""
    if value is None or value == "":
        return None
    return {"type": "mrkdwn", "text": f"*{label}*\n{value}"}


def _slack_payload(card, *legacy):
    """Slack Block Kit 卡片（attachment 配色 + header + fields + body + actions + context）。
    新签名：_slack_payload(card)；旧签名（测试 shim）：_slack_payload(title, body, meta, source)。"""
    if not isinstance(card, Card):
        title, body, meta, source = card, legacy[0], legacy[1], legacy[2]
        merged = {"source": source or ""}
        if isinstance(meta, dict):
            merged.update(meta)
        card = assemble_card(title, body, merged)
    _name, _label, _feishu_t, hex_color = _event_palette_row(card.accent, card.accent_color)
    color = hex_color or "#586069"
    emoji = _SLACK_EMOJI.get(card.accent, "📢")

    fields = list(filter(None, [_slack_field(l, v) for l, v in card.fields]))
    # Slack 段最长 150 字符；emoji 让 header 与具体事件标题合并可读
    header_text = (emoji + card.title)
    blocks = [{"type": "header",
               "text": {"type": "plain_text", "text": header_text[:150],
                        "emoji": True}}]
    if fields:
        blocks.append({"type": "section", "fields": fields[:10]})   # Slack 一节最多 10 格
    if card.body:
        # mrkdwn 单段上限 3000 字符，超长直接截断避免 Slack 拒收
        blocks.append({"type": "section",
                       "text": {"type": "mrkdwn", "text": card.body[:3000]}})
    if card.action_url.startswith(("http://", "https://")):
        blocks.append({"type": "actions",
                       "elements": [{"type": "button", "style": "primary",
                                     "text": {"type": "plain_text",
                                              "text": "查看详情", "emoji": True},
                                     "url": card.action_url}]})
    origin = "" if card.source == "test" else card.source
    ts_text = "—"
    if card.timestamp:
        ts_text = _format_ts(card.timestamp)
    footer_parts = [x for x in (origin, "青鸟 Bluebird", ts_text) if x]
    blocks.append({"type": "context",
                   "elements": [{"type": "mrkdwn", "text": " · ".join(footer_parts)}]})

    # text 字段是 Slack 推送 / 无障碍回退文本，必须在 blocks 之外也保留一份
    fallback = "\n".join(x for x in (card.title, card.body) if x)[:2000]
    return {"text": fallback or header_text[:2000],
            "blocks": blocks,
            "attachments": [{"color": color, "text": ""}]}


def _slack_text_payload(card, *legacy):
    """text 样式：单段文本 + 彩色侧栏，不带 Block Kit 区块。
    新签名：_slack_text_payload(card)；旧签名：_slack_text_payload(title, body, meta, source)。"""
    if not isinstance(card, Card):
        title, body, meta, source = card, legacy[0], legacy[1], legacy[2]
        merged = {"source": source or ""}
        if isinstance(meta, dict):
            merged.update(meta)
        card = assemble_card(title, body, merged)
    _name, _label, _feishu_t, hex_color = _event_palette_row(card.accent, card.accent_color)
    color = hex_color or "#586069"
    emoji = _SLACK_EMOJI.get(card.accent, "📢")
    text = (emoji + " " + (card.title or "")).strip()
    if card.body:
        text = f"{text}\n{card.body}"
    origin = "" if card.source == "test" else card.source
    ts_text = "—"
    if card.timestamp:
        ts_text = _format_ts(card.timestamp)
    footer_parts = [x for x in (origin, "青鸟 Bluebird", ts_text) if x]
    return {"text": text[:3000],
            "attachments": [{"color": color, "text": " · ".join(footer_parts)}]}


def _slack_send(webhook, payload):
    """Slack Incoming Webhook：成功为纯文本 200/ok，失败可能是 JSON {ok:false,error}。
    _post_json 会因响应不是 JSON 抛错，故直接走 urllib 按状态码判断。"""
    req = urllib.request.Request(
        webhook, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            body = r.read().decode("utf-8", "replace").strip()
            if r.status == 200 and body == "ok":
                return True
            log.warning("Slack 返回异常: %s", body[:200])
            return False
    except urllib.error.HTTPError as e:
        log.warning("Slack HTTP %s: %s", e.code,
                    e.read().decode("utf-8", "replace")[:200])
        return False


def _do_push(typ, card, cfg):
    """按渠道类型与实例配置推送（吃 Card）；返回是否成功。
    渠道配置里的 (style) 用于飞书/Slack 切换 card/text，其他渠道走默认。"""
    style = (cfg.get("style") or "card") if typ in ("feishu", "slack") else "card"
    if typ == "bark":
        key = cfg.get("key", "")
        if not key:
            return False
        payload = {"title": card.title, "body": card.body,
                   "subtitle": cfg.get("source", ""),
                   "group": card.source or "github", "sound": "default"}
        url = (cfg.get("url") or BARK_URL).rstrip("/") + "/" + key
        res = _post_json(url, payload)
        ok = isinstance(res, dict) and res.get("code") == 200
        if not ok:
            log.warning("Bark 返回异常: %s", res)
        return ok

    if typ == "feishu":
        app_id = cfg.get("app_id", "")
        app_secret = cfg.get("app_secret", "")
        receive_id = cfg.get("receive_id", "")
        if not (app_id and app_secret and receive_id):
            return False
        token = _feishu_token(app_id, app_secret)
        rid_type = cfg.get("receive_id_type") or "chat_id"
        url = (f"{FEISHU_BASE_URL}/open-apis/im/v1/messages"
               f"?receive_id_type={urllib.parse.quote(rid_type)}")
        if style == "text":
            msg_type = "text"
            content = {"text": f"{card.title}\n{card.body}"}
        else:
            # interactive 的 content 是「裸卡片对象」的字符串化 JSON（包一层 card 会被飞书拒）
            msg_type = "interactive"
            content = _feishu_card(card)
        payload = {"receive_id": receive_id, "msg_type": msg_type,
                   "content": json.dumps(content, ensure_ascii=False)}
        res = _post_json(url, payload, {"Authorization": f"Bearer {token}"})
        ok = isinstance(res, dict) and res.get("code") == 0
        if not ok:
            log.warning("飞书返回异常: %s", res)
            hint = _feishu_error_hint(res)
            if hint:
                # 这类失败是配置问题，抛出去让面板「测试推送」原样显示
                raise RuntimeError(hint)
        return ok

    if typ == "wecom":
        webhook = cfg.get("webhook", "")
        if not webhook:
            return False
        payload = {"msgtype": "text", "text": {"content": f"{card.title}\n{card.body}"}}
        res = _post_json(webhook, payload)
        ok = isinstance(res, dict) and res.get("errcode") == 0
        if not ok:
            log.warning("企业微信返回异常: %s", res)
        return ok

    if typ == "slack":
        webhook = cfg.get("webhook", "")
        if not webhook:
            return False
        payload = _slack_text_payload(card) if style == "text" else _slack_payload(card)
        return _slack_send(webhook, payload)

    if typ == "pushdeer":
        key = cfg.get("key", "")
        if not key:
            return False
        # 失败时 HTTP 仍是 200，只能靠 code 判断（0 = 成功）
        url = (cfg.get("url") or PUSHDEER_URL).rstrip("/") + "/message/push"
        res = _post_form(url, {"pushkey": key, "text": card.title,
                               "desp": card.body, "type": "markdown"})
        ok = isinstance(res, dict) and res.get("code") == 0
        if not ok:
            log.warning("PushDeer 返回异常: %s", res)
        return ok

    if typ == "webhook":
        url = cfg.get("url", "")
        if not url:
            return False
        # 通用 Webhook 走完整字段（§6.5 固定顺序）；旧调用方只读 title/body 不受影响
        accent_name, _, _, _ = _event_palette_row(card.accent, card.accent_color)
        fields_obj = [{"label": lbl, "value": val} for lbl, val in card.fields]
        ts = card.timestamp or int(time.time())
        payload_dict = {
            "title":      card.title,
            "body":       card.body,
            "accent":     accent_name,
            "fields":     fields_obj,
            "action_url": card.action_url,
            "source":     card.source,
            "timestamp":  ts,
        }
        body_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = cfg.get("secret", "")
        if secret:
            # 接收方按同样方式计算并对齐即可校验来源（hex，形如 sha256=<digest>）
            headers["X-Bluebird-Signature-256"] = "sha256=" + hmac.new(
                secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        ok = _post_raw(url, body_bytes, headers)
        if not ok:
            log.warning("通用 Webhook 返回异常: %s", url)
        return ok

    log.warning("未知渠道类型: %s", typ)
    return False


def _push_channel(typ, *args, **kwargs):
    """支持新旧两种签名：
      新：_push_channel(typ, card, cfg)
      旧：_push_channel(typ, title, body, cfg, source='', meta=None) / 关键字 source/meta
    内部把 (title, body, meta) 装 Card 后调 _do_push 真正分发。"""
    if len(args) == 2 and isinstance(args[0], Card):
        return _do_push(typ, args[0], args[1])
    if len(args) >= 3:
        title, body, cfg = args[0], args[1], args[2]
        if len(args) >= 4 and isinstance(args[3], str):
            source = args[3]
            meta = args[4] if len(args) >= 5 and isinstance(args[4], dict) else None
        elif len(args) >= 4 and isinstance(args[3], dict):
            source = ""
            meta = args[3]
        else:
            source = ""
            meta = None
    else:
        raise TypeError(f"_push_channel 期望 (typ, card, cfg) 或 (typ, title, body, cfg[, source][, meta])，收到 {len(args)+1} 个参数")
    # 关键字参数覆盖
    source = kwargs.get("source", source)
    meta = kwargs.get("meta", meta)
    merged = dict(meta or {})
    if source:
        merged["source"] = source
    card = assemble_card(title, body, merged)
    return _do_push(typ, card, cfg)


def notify(title, body, meta=None):
    """按来源配置推送到指定渠道并记录审计日志；任一成功即 True，单个渠道异常不影响其他。
    来源未配置 channels 时推送到全部已启用渠道；全局渠道开关仍生效。
    来源层产出的 (title, body, meta) 在此装配成 Card 再下发到各渠道。"""
    pushed = False
    meta = meta or {}
    event_type = meta.get("event", "")
    repo = meta.get("repo", "")
    source = meta.get("source", "")
    allowed = None
    if source:
        for s in get_sources():
            if s["name"] == source:
                chans = (s.get("config") or {}).get("channels")
                # 显式配置 channels 后以此为准：空列表 = 该来源不推送任何渠道；
                # 未配置（None）时推送到全部已启用渠道（旧配置兼容）
                allowed = set(chans) if chans is not None else None
                break
    # 先装配 Card 给新代码（_do_push 走这条路）；同时保留旧 4 参入口，
    # 让 mock.patch("server._push_channel") 能拦截到 (typ, title, body, cfg)。
    meta_for_card = dict(meta)
    meta_for_card.setdefault("source", source)
    meta_for_card.setdefault("timestamp", int(time.time()))
    card = assemble_card(title, body, meta_for_card)
    for ch in get_channels():
        name = ch["name"]
        if allowed is not None and name not in allowed:
            continue
        if not is_enabled("channel", name):
            continue
        try:
            ok = _push_channel(ch["type"], card.title, card.body,
                               ch.get("config") or {}, source, meta_for_card)
            status = "ok" if ok else "error"
        except Exception as e:
            log.error("渠道 %s 推送异常: %s (%s)", name, title, e)
            ok, status = False, "error"
        log_push(name, event_type, repo, title, body, status, source=source)
        if ok:
            pushed = True
            log.info("渠道 %s 推送成功: %s", name, title)
    return pushed


def channel_test(name, card=None):
    """向指定渠道实例发一条测试推送，返回 (是否成功, 错误信息)。
    仅供面板「测试推送」使用：不写推送日志、不影响统计。
    card=None 时走默认（event=test，固定标题「青鸟 Bluebird 测试推送」）；
    传 card 时整张下发（面板「示例事件推送」），保留配色 / 字段 / 跳转按钮。"""
    ch = next((c for c in get_channels() if c["name"] == name), None)
    if ch is None:
        return False, "渠道不存在"
    try:
        if card is None:
            card = assemble_card(
                "青鸟 Bluebird 测试推送",
                f"渠道「{name}」配置测试，收到即表示该通道可用。",
                {"event": "test", "source": "test", "timestamp": int(time.time())},
            )
            # 默认测试推送走旧 4 参，让 mock.patch("server._push_channel") 拦到的形态与历史一致
            ok = _push_channel(ch.get("type", ""), card.title, card.body,
                               ch.get("config") or {}, card.source or "test")
        else:
            ok = _push_channel(ch.get("type", ""), card, ch.get("config") or {})
    except Exception as e:
        log.warning("渠道 %s 测试推送异常: %s", name, e)
        return False, f"推送异常：{e}"
    return (True, "") if ok else (False, "渠道返回失败，详见服务端日志")


def channel_test_event(name, event):
    """向指定渠道实例推送一条**预置示例事件**（设计稿 §7），返回 (是否成功, 错误信息)。
    与 channel_test 一样不写推送日志；event 取夹具 key（如 workflow_run_fail）。"""
    key = _EXAMPLE_EVENT_ALIASES.get(str(event or ""), str(event or ""))
    ev = example_events().get(key)
    if ev is None:
        return False, f"未知示例事件：{event}"
    meta = dict(ev.get("meta") or {})
    # source=test：页脚不显示来源占位符，且不给 Bark 的 group 传入来源名
    meta["source"] = "test"
    meta["timestamp"] = int(time.time())
    return channel_test(name, assemble_card(ev.get("title", ""), ev.get("body", ""), meta))


def repo_name(payload):
    return (payload.get("repository") or {}).get("name") or "unknown"


def repo_owner(payload):
    repo = payload.get("repository") or {}
    return (repo.get("owner") or {}).get("login") or (repo.get("full_name") or "/").split("/")[0]


def ignore_actor(payload, self_notify=None):
    """机器人操作始终过滤；本人操作默认过滤。
    self_notify 为 None 时回退全局 notify.self 开关（向后兼容），否则用来源级配置。"""
    actor = ((payload.get("sender") or {}).get("login") or "").lower()
    if actor in BOT_LOGINS:
        return True
    if actor and actor == NOTIFY_OWNER.lower():
        if self_notify is None:
            self_notify = is_enabled("notify", "self", default="0")
        return not self_notify
    return False


# ---------- 来源层（类型 = 一对 verify/handle，注册到 SOURCES；实例配置来自动态配置） ----------

def _verify_github(headers, body, cfg=None):
    """GitHub HMAC-SHA256 校验；实例未配 secret 时一律拒绝（fail-closed，避免无认证注入）。"""
    secret = (cfg or {}).get("secret", "")
    if not secret:
        return False
    return verify(headers.get("X-Hub-Signature-256", ""), body, secret)


def _generic_token(headers):
    """提取通用来源 token：标准 Authorization 头（Bearer <token>，兼容裸 token）。"""
    auth = headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[len("Bearer "):]
    if auth and not auth.startswith("Basic "):
        return auth
    return ""


def _verify_generic(headers, body, cfg=None):
    """通用来源 token 校验（Authorization: Bearer <token>）；未配 token 时拒绝。"""
    token = (cfg or {}).get("token", "")
    return bool(token) and hmac.compare_digest(_generic_token(headers), token)


def _handle_github(payload, event, cfg=None):
    """GitHub 事件 → (title, body, meta)；忽略返回 None。
    cfg.events（来源级事件白名单）显式配置后以此为准：空列表 = 该来源不推送任何事件；
    未配置（None）时回退全局 event 开关。"""
    if not payload or (NOTIFY_OWNER and repo_owner(payload) != NOTIFY_OWNER):
        return None
    events_cfg = (cfg or {}).get("events")
    if events_cfg is not None:
        if event not in events_cfg:
            return None
    elif event in EVENT_TYPES and not is_enabled("event", event):
        return None
    # 本人操作通知：来源级 self 配置优先，未配置回退全局 notify.self
    self_notify = None if cfg is None else bool(cfg.get("self"))

    if event == "watch":
        if payload.get("action") != "started" or ignore_actor(payload, self_notify):
            return None
        actor = (payload.get("sender") or {}).get("login", "")
        repo = repo_name(payload)
        stars = (payload.get("repository") or {}).get("stargazers_count", "?")
        return (f"⭐ {actor} star 了 {repo}", f"{repo} 被 {actor} star，共 {stars} 星",
                {"event": "watch", "repo": repo, "actor": actor,
                 "url": (payload.get("repository") or {}).get("html_url", "")})

    if event == "fork":
        if ignore_actor(payload, self_notify):
            return None
        actor = (payload.get("sender") or {}).get("login", "")
        repo = repo_name(payload)
        return (f"🍴 {actor} fork 了 {repo}", f"{actor} fork 了 {repo}",
                {"event": "fork", "repo": repo, "actor": actor,
                 "url": (payload.get("forkee") or {}).get("html_url", "")})

    if event == "issues":
        if payload.get("action") != "opened" or ignore_actor(payload, self_notify):
            return None
        actor = (payload.get("sender") or {}).get("login", "")
        repo = repo_name(payload)
        issue = payload.get("issue") or {}
        n = issue.get("number", "?")
        labels = ", ".join(lb.get("name", "") for lb in (issue.get("labels") or []) if lb)
        return (f"🐛 新 issue #{n}：{issue.get('title', '')}",
                f"{actor} 在 {repo} 提了 issue #{n}",
                {"event": "issues", "repo": repo, "actor": actor,
                 "labels": labels,
                 "url": issue.get("html_url", "")})

    if event == "issue_comment":
        if payload.get("action") != "created" or ignore_actor(payload, self_notify):
            return None
        actor = (payload.get("sender") or {}).get("login", "")
        repo = repo_name(payload)
        issue = payload.get("issue") or {}
        n = issue.get("number", "?")
        kind = "PR" if "pull_request" in issue else "issue"
        # 取全文，渲染端按渠道能力截断（飞书 lark_md / Slack mrkdwn 各有上限）
        comment = (payload.get("comment") or {}).get("body", "")
        return (f"💬 {actor} 评论了 {kind} #{n}",
                f"{repo} 的 {kind} #{n} 有新评论",
                {"event": "issue_comment", "repo": repo, "actor": actor,
                 "comment": comment,
                 "url": (payload.get("comment") or {}).get("html_url", "")})

    if event == "pull_request":
        if payload.get("action") != "opened" or ignore_actor(payload, self_notify):
            return None
        actor = (payload.get("sender") or {}).get("login", "")
        repo = repo_name(payload)
        pr = payload.get("pull_request") or {}
        n = pr.get("number", "?")
        head = pr.get("head") or {}
        sha = (head.get("sha") or "")[:7]
        labels = ", ".join(lb.get("name", "") for lb in (pr.get("labels") or []) if lb)
        return (f"🔀 新 PR #{n}：{pr.get('title', '')}",
                f"{actor} 向 {repo} 提交了 PR #{n}",
                {"event": "pull_request", "repo": repo, "actor": actor,
                 "branch": head.get("ref", ""),
                 "commit": sha, "labels": labels,
                 "url": pr.get("html_url", "")})

    if event == "workflow_run":
        run = payload.get("workflow_run") or {}
        if payload.get("action") != "completed" or run.get("status") != "completed":
            return None
        conclusion = run.get("conclusion", "")
        if conclusion in ("skipped", "neutral"):
            return None
        repo = repo_name(payload)
        wf = payload.get("workflow") or {}
        wf_name = (wf.get("name") if isinstance(wf, dict) else wf) or "workflow"
        mark = {"success": "✅ CI 通过", "failure": "❌ CI 失败", "cancelled": "⏹ CI 取消",
                "timed_out": "⏰ CI 超时", "action_required": "⚠️ CI 待处理",
                "stale": "❌ CI 过期", "startup_failure": "❌ CI 启动失败"}.get(conclusion, "❓ CI")
        wf_status_cn = {"success": "通过", "failure": "失败",
                        "cancelled": "取消", "timed_out": "超时",
                        "action_required": "待处理", "stale": "过期",
                        "startup_failure": "启动失败"}.get(conclusion, "未知")
        branch = run.get("head_branch", "")
        display = run.get("display_title", "") or ""
        sha = (run.get("head_sha") or "")[:7]
        body = f"{repo} · {branch}".strip(" ·")
        if display and display != branch:
            body += f" · {display}"
        return (f"{mark}：{wf_name}", body,
                {"event": "workflow_run", "repo": repo, "branch": branch,
                 "actor": (payload.get("sender") or {}).get("login", ""),
                 "commit": sha, "wf_status": wf_status_cn,
                 "color": "green" if conclusion == "success" else "red",
                 "url": run.get("html_url", "")})

    return None


def _handle_generic(payload, event="", cfg=None):
    """通用来源 → (title, body, meta)；忽略返回 None。
    JSON: {"title","body","event","repo","url","actor","branch","labels","commit","comment"}；纯文本：首行为标题。
    cfg.events 显式配置后以此为准：空列表 = 该来源不推送任何事件；
    未配置（None）时不按事件过滤。"""
    events_cfg = (cfg or {}).get("events")
    if isinstance(payload, str):
        text = payload.strip()
        if not text:
            return None
        if events_cfg is not None and "generic" not in events_cfg:
            return None
        return (text.splitlines()[0][:60], text, {"event": "generic", "repo": ""})
    if isinstance(payload, dict):
        title = str(payload.get("title") or payload.get("message") or "").strip()
        body = str(payload.get("body") or payload.get("content") or "").strip()
        if not title and not body:
            return None
        ev = str(payload.get("event") or "generic")
        if events_cfg is not None and ev not in events_cfg:
            return None
        meta = {"event": ev, "repo": str(payload.get("repo") or "")}
        # 可选字段：调用方主动传时透传进 meta，assemble_card 按字段规则决定是否进 fields
        for k in ("actor", "branch", "labels", "commit", "comment", "url"):
            v = payload.get(k)
            if v:
                meta[k] = str(v)
        return (title or "通知", body, meta)
    return None


SOURCES = {
    "github": {"verify": _verify_github, "handle": _handle_github},
    "generic": {"verify": _verify_generic, "handle": _handle_generic},
}


def read_version():
    """当前部署版本：优先读数据目录 version 文件（CI 打 tag 部署时写入），否则环境变量，默认 dev。"""
    try:
        with open(VERSION_FILE) as f:
            v = f.read().strip()
            if v:
                return v
    except OSError:
        pass
    return os.environ.get("NOTIFY_VERSION", "dev")


SESSION_SECRET_KEY = "session.secret"
_session_secret_cache = None


def session_secret():
    """面板会话签名密钥：环境变量优先，否则首启随机生成并持久化（重启不失效）。
    绝不回退到可预测常量，否则任何人都能伪造登录态。"""
    global _session_secret_cache
    if UI_SESSION_SECRET:
        return UI_SESSION_SECRET
    if _session_secret_cache:
        return _session_secret_cache
    with db() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?",
                           (SESSION_SECRET_KEY,)).fetchone()
        if not row or not row[0]:
            row = (secrets.token_urlsafe(32),)
            conn.execute("INSERT INTO settings(key, value) VALUES(?, ?) "
                         "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                         (SESSION_SECRET_KEY, row[0]))
    _session_secret_cache = row[0]
    return _session_secret_cache


def ui_token(user):
    """签发审计会话 token（HMAC 签名，防篡改）。"""
    exp = int(time.time()) + UI_SESSION_HOURS * 3600
    payload = f"{user}.{exp}"
    sig = hmac.new(session_secret().encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def ui_token_ok(token):
    """校验会话 token：签名有效、未过期、用户匹配。"""
    try:
        user, exp, sig = token.rsplit(".", 2)
        expect = hmac.new(session_secret().encode(), f"{user}.{exp}".encode(),
                          hashlib.sha256).hexdigest()
        return (hmac.compare_digest(sig, expect) and user == UI_AUTH_USER
                and int(exp) >= int(time.time()))
    except Exception:
        return False


def _abs(path):
    """构造含 BASE_PATH 前缀的完整路径。"""
    return "/" + (BASE_PATH + "/" if BASE_PATH else "") + path.lstrip("/")


def auth_ok(handler):
    """审计面板认证：签名 cookie 优先，兼容 Basic Auth。
    未配置凭据（用户名或密码为空）时一律拒绝（fail-closed）——面板能读到全部渠道 key/token，
    默认开放等于把密钥挂在公网上。"""
    if not (UI_AUTH_USER and UI_AUTH_PASS):
        return False
    cookies = handler.headers.get("Cookie", "")
    for part in cookies.split("; "):
        if part.startswith("bb_token=") and ui_token_ok(part[len("bb_token="):]):
            return True
    header = handler.headers.get("Authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        user, _, pwd = base64.b64decode(header[6:]).decode("utf-8").partition(":")
    except Exception:
        return False
    return hmac.compare_digest(user, UI_AUTH_USER) and hmac.compare_digest(pwd, UI_AUTH_PASS)


# 安全响应头：面板是单页应用（内联脚本/样式 + Google Fonts），故 script/style 放开 inline
CSP = ("default-src 'self'; script-src 'self' 'unsafe-inline'; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src https://fonts.gstatic.com; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")


class BodyError(Exception):
    """请求体不合法：Content-Length 非法 / 超限 / 分块传输。"""

    def __init__(self, status, error):
        super().__init__(error)
        self.status = status
        self.error = error


class Handler(BaseHTTPRequestHandler):
    timeout = SOCKET_TIMEOUT   # 慢连接 / 半开连接不长期占用线程

    def log_message(self, fmt, *args):
        pass

    def end_headers(self):
        # 统一补安全响应头，各响应分支无需重复设置
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("Content-Security-Policy", CSP)
        super().end_headers()

    @staticmethod
    def route(path):
        """规范化请求路径：去掉 BASE_PATH 前缀后返回内部路径；不在子路径下返回 None。"""
        path = path.rstrip("/") or "/"
        if BASE_PATH:
            prefix = "/" + BASE_PATH
            if path == prefix:
                return "/"
            if path.startswith(prefix + "/"):
                return path[len(prefix):]
            return None
        return path

    def _is_https(self):
        """是否经 HTTPS 反代访问（决定登录 Cookie 是否带 Secure）。"""
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"

    def _same_origin(self):
        """CSRF 防护：带 Origin/Referer 的写请求需与 Host 同源；
        两者都没有（脚本 / curl / webhook 调用方）时放行。"""
        src = self.headers.get("Origin") or self.headers.get("Referer") or ""
        if not src:
            return True
        return (urllib.parse.urlparse(src).netloc.lower()
                == (self.headers.get("Host") or "").lower())

    def _read_body(self):
        """读取请求体：拒绝非法 / 超限 Content-Length 与分块传输。
        拒绝时 body 未消费，一并关闭连接，避免残留字节被当成下一个请求解析。"""
        if (self.headers.get("Transfer-Encoding") or "").strip():
            self.close_connection = True
            raise BodyError(411, "chunked request body not supported")
        try:
            n = int((self.headers.get("Content-Length") or "0").strip())
        except ValueError:
            self.close_connection = True
            raise BodyError(400, "bad Content-Length")
        if n < 0:
            self.close_connection = True
            raise BodyError(400, "bad Content-Length")
        if n > MAX_BODY_BYTES:
            self.close_connection = True
            raise BodyError(413, f"body too large (limit {MAX_BODY_BYTES} bytes)")
        return self.rfile.read(n) if n else b""

    def _json(self, status, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _unauthorized(self):
        if not (UI_AUTH_USER and UI_AUTH_PASS):
            return self._json(503, {"ok": False, "error": (
                "面板未配置登录凭据（NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS），已拒绝访问")})
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="bluebird"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self.route(parsed.path)
        if path is None:
            return self._json(404, {"ok": False, "error": "not found"})

        # 根路径（子域名部署）：未登录跳登录、已登录跳面板
        if path == "/":
            return self._redirect(_abs("ui"))

        if path == "/health":
            return self._json(200, {"ok": True, "started_at": START_TIME})

        if path == "/favicon.ico":
            try:
                with open(FAVICON_FILE, "rb") as f:
                    body = f.read()
            except OSError:
                return self._json(404, {"ok": False, "error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", "image/x-icon")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        fm = re.fullmatch(r"/favicon-([a-z]+)\.svg", path)
        if fm and fm.group(1) in FAVICON_THEME_COLOR:
            body = FAVICON_SVG_TEMPLATE.format(
                color=FAVICON_THEME_COLOR[fm.group(1)]).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "image/svg+xml")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        if path == "/login":
            if not (UI_AUTH_USER and UI_AUTH_PASS):
                return self._json(503, {"ok": False, "error": (
                    "面板未配置登录凭据（NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS），已禁用面板访问")})
            if auth_ok(self):
                return self._redirect(_abs("ui"))
            try:
                with open(LOGIN_FILE, "rb") as f:
                    html = f.read()
            except OSError:
                return self._json(500, {"ok": False, "error": "login.html missing"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            return self.wfile.write(html)

        if path == "/ui":
            if not auth_ok(self):
                return self._redirect(_abs("login"))
            try:
                with open(UI_FILE, "rb") as f:
                    html = f.read()
            except OSError:
                return self._json(500, {"ok": False, "error": "ui.html missing"})
            html = html.replace(b"__BB_VERSION__", read_version().encode("utf-8"))
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            return self.wfile.write(html)

        if path == "/help":
            # 帮助已并入 ui.html 的 #/help 视图：/help 保留 302 兼容旧书签，不再读 help.html
            if not auth_ok(self):
                return self._redirect(_abs("login"))
            return self._redirect(_abs("ui#/help"))

        if path.startswith("/api/logs"):
            if not auth_ok(self):
                return self._unauthorized()
            q = urllib.parse.parse_qs(parsed.query)

            def g(key, default):
                try:
                    return (q.get(key) or [default])[0]
                except Exception:
                    return default

            def gint(key, default):
                try:
                    return int(g(key, str(default)))
                except (TypeError, ValueError):
                    return default

            channel, event_type, days, source = (g("channel", ""), g("event_type", ""),
                                                 g("days", "7"), g("source", ""))
            raw_since = g("since", "").strip()
            try:
                since = int(raw_since) if raw_since else None
            except ValueError:
                since = None   # 非法的 since 视为未传，保持向后兼容
            page = max(1, gint("page", 1))
            page_size = min(max(gint("page_size", gint("limit", 100)), 1), 500)
            counts = count_logs(channel=channel, event_type=event_type,
                                days=days, source=source, since=since)
            items = query_logs(channel=channel, event_type=event_type,
                               days=days, limit=page_size,
                               source=source, offset=(page - 1) * page_size,
                               since=since)
            return self._json(200, {"ok": True, "items": items, "total": counts["total"],
                                    "ok_count": counts["ok"], "error_count": counts["error"],
                                    "page": page, "page_size": page_size})

        if path.startswith("/api/stats"):
            if not auth_ok(self):
                return self._unauthorized()
            q = urllib.parse.parse_qs(parsed.query)

            def g2(key, default):
                try:
                    return (q.get(key) or [default])[0]
                except Exception:
                    return default

            group = g2("group", "source")
            if group not in STAT_GROUPS:
                return self._json(400, {"ok": False, "error": f"unknown group: {group}"})
            raw_days = g2("days", "")
            try:
                days = int(raw_days) if raw_days else retention_days()
            except ValueError:
                days = retention_days()
            stats = query_stats(days=days, group=group)
            return self._json(200, {"ok": True, "group": group, "days": days, **stats})

        if path == "/api/version":
            if not auth_ok(self):
                return self._unauthorized()
            return self._json(200, {"ok": True, "version": read_version()})

        if path.startswith("/docs/"):
            if not auth_ok(self):
                return self._unauthorized()
            fname = urllib.parse.unquote(path[len("/docs/"):])
            if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", fname):
                return self._json(404, {"ok": False, "error": "not found"})
            fpath = os.path.join(os.path.dirname(os.path.abspath(__file__)), "docs", fname)
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
            except OSError:
                return self._json(404, {"ok": False, "error": "not found"})
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            return self.wfile.write(body)

        if path.startswith("/api/settings"):
            if not auth_ok(self):
                return self._unauthorized()
            s = get_settings()
            return self._json(200, {
                "ok": True,
                "sources": {i["name"]: is_enabled("source", i["name"]) for i in get_sources()},
                "events": {e: s.get(f"event.{e}", "1") != "0" for e in EVENT_TYPES},
                "channels": {i["name"]: is_enabled("channel", i["name"]) for i in get_channels()},
                "notify": {"self": is_enabled("notify", "self", default="0")},
                "retention": {"days": retention_days()},
                "display": {"tz": display_tz()},
                "tz_whitelist": list(TZ_WHITELIST),
                "source_items": get_sources(),
                "channel_items": get_channels(),
                "example_events": example_event_options(),
            })

        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = self.route(parsed.path)
        if path is None:
            return self._json(404, {"ok": False, "error": "not found"})
        if path == "/api/settings":
            if not auth_ok(self):
                return self._unauthorized()
            if not self._same_origin():
                return self._json(403, {"ok": False, "error": "cross-origin request rejected"})
            try:
                data = json.loads(self._read_body().decode("utf-8") or "{}")
            except BodyError as e:
                return self._json(e.status, {"ok": False, "error": e.error})
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"ok": False, "error": "bad json"})
            key = data.get("key", "")
            prefix, _, name = key.partition(".")

            def _save_item(loader, saver, validator, action_name, col):
                item = data.get("item") or {}
                err = validator(item)
                if err:
                    return self._json(400, {"ok": False, "error": err})
                n = str(item["name"]).strip()
                old = str(data.get("original_name") or "").strip()
                items = loader()
                # 改名 / 编辑时保留原条目的稳定 ID：旧的 /hooks/<ID> 地址继续可用
                pos = next((k for k, c in enumerate(items) if c["name"] == (old or n)), None)
                prev = items[pos] if pos is not None else None
                if prev and prev.get("id") and not item.get("id"):
                    item["id"] = prev["id"]
                # 移除与新名称或原名称（编辑改名时）相同的旧条目，避免"编辑变新增"
                cur = [c for c in items if c["name"] != n and c["name"] != old]
                # 编辑/改名保持原来的位置，不会跳到列表末尾
                if pos is None:
                    cur.append(item)
                else:
                    cur.insert(min(pos, len(cur)), item)
                saver(cur)
                if old and old != n:
                    rename_history(col, old, n)
                log.info("%s %s 保存（%s）", action_name, n, self.client_address[0])
                return self._json(200, {"ok": True, "item": item})

            if action := data.get("action", ""):
                if action == "channel/save":
                    item = data.get("item") or {}
                    n = str(item.get("name") or "").strip()
                    old = str(data.get("original_name") or "").strip()
                    if n and channel_name_taken(n, old):
                        return self._json(400, {"ok": False, "error": f"渠道名「{n}」已存在，请换一个"})
                    return _save_item(get_channels, set_channels, channel_error, "渠道", "channel")
                if action == "channel/test":
                    n = str(data.get("name") or "").strip()
                    ev = str(data.get("event") or "").strip()
                    if ev:
                        # 示例事件推送（设计稿 §7）：真实下发预置 payload，二次确认在前端
                        ok, err = channel_test_event(n, ev)
                        log.info("渠道 %s 示例事件推送（%s）%s（%s）", n, ev,
                                 "成功" if ok else "失败", self.client_address[0])
                    else:
                        ok, err = channel_test(n)
                        log.info("渠道 %s 测试推送 %s（%s）", n, "成功" if ok else "失败",
                                 self.client_address[0])
                    return self._json(200, {"ok": True, "success": ok, "error": err})
                if action == "channel/remove":
                    n = str(data.get("name") or "").strip()
                    set_channels([c for c in get_channels() if c["name"] != n])
                    log.info("渠道 %s 删除（%s）", n, self.client_address[0])
                    return self._json(200, {"ok": True})
                if action == "source/save":
                    item = data.get("item") or {}
                    cfg = item.setdefault("config", {})
                    typ = str(item.get("type") or "")
                    # Token/Secret 一律由服务端管理：为空（新建或留空）时自动生成；
                    # 编辑已有实例且未携带原值时保留原值，避免无关保存让旧值静默失效。
                    if typ in SOURCE_TYPES:
                        key = "secret" if typ == "github" else "token"
                        if not str(cfg.get(key) or "").strip():
                            keep = ""
                            old = str(data.get("original_name") or "").strip()
                            if old:
                                prev = next((s for s in get_sources()
                                             if s["name"] == old and s.get("type") == typ), None)
                                if prev:
                                    keep = str(((prev.get("config") or {}).get(key) or "")).strip()
                            cfg[key] = keep or secrets.token_urlsafe(24)
                    return _save_item(get_sources, set_sources, source_error, "通知源", "source")
                if action == "source/regen":
                    n = str(data.get("name") or "").strip()
                    items = get_sources()
                    idx = next((i for i, s in enumerate(items)
                                if s["name"] == n and s["type"] in SOURCE_TYPES), None)
                    if idx is None:
                        return self._json(404, {"ok": False, "error": "通知源不存在"})
                    key = "secret" if items[idx]["type"] == "github" else "token"
                    items[idx].setdefault("config", {})[key] = secrets.token_urlsafe(24)
                    set_sources(items)
                    log.info("通知源 %s %s 重新生成（%s）", n,
                             "Secret" if key == "secret" else "Token", self.client_address[0])
                    return self._json(200, {"ok": True, "item": items[idx]})
                if action == "source/remove":
                    n = str(data.get("name") or "").strip()
                    set_sources([s for s in get_sources() if s["name"] != n])
                    log.info("通知源 %s 删除（%s）", n, self.client_address[0])
                    return self._json(200, {"ok": True})
                if action == "logs/clear":
                    n = clear_logs()
                    log.info("清理推送历史 %s 条（%s）", n, self.client_address[0])
                    return self._json(200, {"ok": True, "deleted": n})

            if prefix == "retention" and name == "days":
                try:
                    v = int(data.get("value", LOG_RETENTION_DAYS))
                except (TypeError, ValueError):
                    return self._json(400, {"ok": False, "error": "invalid retention days"})
                v = max(1, min(v, 3650))
                set_setting(key, str(v))
                with db() as conn:
                    conn.execute("DELETE FROM push_log WHERE ts < ?",
                                 (int(time.time()) - v * 86400,))
                log.info("设置 %s=%s（%s）", key, v, self.client_address[0])
                return self._json(200, {"ok": True, "key": key, "value": str(v)})
            if prefix == "display" and name == "tz":
                # 显示时区只接受白名单（避免任意字符串进 zoneinfo；与前端 TZ_VALUES 对齐）
                tz_val = str(data.get("value", "") or "").strip()
                if tz_val not in TZ_WHITELIST:
                    return self._json(400, {"ok": False, "error": f"invalid display tz: {tz_val}"})
                set_setting("display.tz", tz_val)
                log.info("设置 display.tz=%s（%s）", tz_val, self.client_address[0])
                return self._json(200, {"ok": True, "key": "display.tz", "value": tz_val})
            valid = (prefix == "event" and name in EVENT_TYPES) or \
                    (prefix == "source" and any(s["name"] == name for s in get_sources())) or \
                    (prefix == "channel" and any(c["name"] == name for c in get_channels())) or \
                    (prefix == "notify" and name == "self")
            if not valid:
                return self._json(400, {"ok": False, "error": f"invalid key: {key}"})
            value = "1" if str(data.get("value", "0")) in ("1", "true", "True", "on") else "0"
            set_setting(key, value)
            log.info("设置 %s=%s（%s）", key, value, self.client_address[0])
            return self._json(200, {"ok": True, "key": key, "value": value})

        if path == "/login":
            if not (UI_AUTH_USER and UI_AUTH_PASS):
                return self._json(503, {"ok": False, "error": (
                    "面板未配置登录凭据（NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS），已禁用面板访问")})
            if not self._same_origin():
                return self._json(403, {"ok": False, "error": "cross-origin request rejected"})
            try:
                raw = self._read_body().decode("utf-8", errors="replace")
            except BodyError as e:
                return self._json(e.status, {"ok": False, "error": e.error})
            data = urllib.parse.parse_qs(raw)
            user = (data.get("username") or [""])[0].strip()
            pwd = (data.get("password") or [""])[0].strip()
            if (hmac.compare_digest(user, UI_AUTH_USER)
                    and hmac.compare_digest(pwd, UI_AUTH_PASS)):
                token = ui_token(user)
                self.send_response(302)
                self.send_header("Set-Cookie",
                                 f"bb_token={token}; Path=/; HttpOnly; SameSite=Lax; "
                                 f"Max-Age={UI_SESSION_HOURS * 3600}"
                                 + ("; Secure" if self._is_https() else ""))
                self.send_header("Location", _abs("ui"))
                self.send_header("Content-Length", "0")
                self.end_headers()
                log.info("面板登录成功（%s）", self.client_address[0])
                return
            log.warning("面板登录失败 user=%r from %s", user, self.client_address[0])
            return self._redirect(_abs("login?error=1"))

        if path == "/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", "bb_token=; Path=/; HttpOnly; Max-Age=0")
            self.send_header("Location", _abs("login"))
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if path.startswith("/hooks/"):
            key = path[len("/hooks/"):]
            entry = find_source(key)
            if not entry or entry["type"] not in SOURCES:
                return self._json(404, {"ok": False, "error": f"unknown source: {key}"})
            # 日志 / 审计 / 渠道分组一律用实例名，ID 只作稳定入口
            source = entry["name"]
            verify_fn = SOURCES[entry["type"]]["verify"]
            handle_fn = SOURCES[entry["type"]]["handle"]
            try:
                try:
                    body = self._read_body()
                except BodyError as e:
                    return self._json(e.status, {"ok": False, "error": e.error})
                if not verify_fn(self.headers, body, entry.get("config") or {}):
                    log.warning("%s 签名/token 校验失败 from %s", source, self.client_address[0])
                    return self._json(401, {"ok": False, "error": "bad signature"})
                delivery = self.headers.get("X-GitHub-Delivery") or f"{source}-{int(time.time() * 1000)}"
                if seen(delivery):
                    return self._json(200, {"ok": True, "dup": True})
                event = self.headers.get("X-GitHub-Event", "")
                try:
                    payload = json.loads(body.decode("utf-8")) if body else {}
                except ValueError:
                    payload = body.decode("utf-8", errors="replace") if entry["type"] == "generic" else {}
                result = handle_fn(payload, event, entry.get("config") or {})
                if result is None:
                    return self._json(200, {"ok": True, "source": source, "ignored": True})
                title, msg, meta = result
                meta["source"] = source
                pushed = notify(title, msg, meta)
                return self._json(200, {"ok": True, "source": source, "pushed": pushed})
            except Exception:
                log.exception("处理 %s webhook 异常", source)
                return self._json(500, {"ok": False, "error": "internal error"})

        if path == "/api/preview":
            if not auth_ok(self):
                return self._unauthorized()
            if not self._same_origin():
                return self._json(403, {"ok": False, "error": "cross-origin request rejected"})
            try:
                data = json.loads(self._read_body().decode("utf-8") or "{}")
            except BodyError as e:
                return self._json(e.status, {"ok": False, "error": e.error})
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"ok": False, "error": "bad json"})
            typ = data.get("type", "")
            cfg = data.get("config") or {}
            event_key = data.get("event", "watch")
            color = data.get("color") or None
            try:
                meta = dict(data.get("meta") or {})
                if color:
                    meta["color"] = color
                if event_key:
                    meta.setdefault("event", event_key)
                card = assemble_card(
                    data.get("title") or f"[{event_key}] 示例事件",
                    data.get("body") or "预览面板生成的示例事件",
                    meta,
                )
                payload, url = _build_payload_for_preview(typ, cfg, card)
                if payload is None:
                    return self._json(400, {"ok": False,
                                            "error": f"渠道 {typ} 配置不完整或不支持预览"})
                return self._json(200, {"ok": True, "type": typ, "url": url,
                                        "payload": payload,
                                        "accent": card.accent,
                                        "accent_color": card.accent_color,
                                        "palette": _event_palette_row(
                                            card.accent, card.accent_color)})
            except Exception as e:
                log.exception("preview 失败")
                return self._json(500, {"ok": False, "error": f"preview 失败: {e}"})

        return self._json(404, {"ok": False, "error": "not found"})


def _build_payload_for_preview(typ, cfg, card):
    """按渠道类型构造「真实下发 payload」但不发送；供 /api/preview 使用。
    返回 (payload_dict, url_or_None)。"""
    style = (cfg.get("style") or "card") if typ in ("feishu", "slack") else "card"
    if typ == "bark":
        key = cfg.get("key", "")
        if not key:
            return None, None
        payload = {"title": card.title, "body": card.body,
                   "subtitle": cfg.get("source", ""),
                   "group": card.source or "github", "sound": "default"}
        url = (cfg.get("url") or BARK_URL).rstrip("/") + "/" + key
        return payload, url

    if typ == "feishu":
        rid_type = cfg.get("receive_id_type") or "chat_id"
        url = (f"{FEISHU_BASE_URL}/open-apis/im/v1/messages"
               f"?receive_id_type={urllib.parse.quote(rid_type)}")
        if style == "text":
            msg_type = "text"
            content = {"text": f"{card.title}\n{card.body}"}
        else:
            msg_type = "interactive"
            content = _feishu_card(card)
        return {"receive_id": cfg.get("receive_id", ""), "msg_type": msg_type,
                "content": json.dumps(content, ensure_ascii=False)}, url

    if typ == "wecom":
        return {"msgtype": "text", "text": {"content": f"{card.title}\n{card.body}"}}, \
               cfg.get("webhook", "")

    if typ == "slack":
        payload = _slack_text_payload(card) if style == "text" else _slack_payload(card)
        return payload, cfg.get("webhook", "")

    if typ == "pushdeer":
        url = (cfg.get("url") or PUSHDEER_URL).rstrip("/") + "/message/push"
        return {"pushkey": cfg.get("key", ""), "text": card.title,
                "desp": card.body, "type": "markdown"}, url

    if typ == "webhook":
        accent_name, _, _, _ = _event_palette_row(card.accent, card.accent_color)
        fields_obj = [{"label": lbl, "value": val} for lbl, val in card.fields]
        ts = card.timestamp or int(time.time())
        payload_dict = {
            "title":      card.title,
            "body":       card.body,
            "accent":     accent_name,
            "fields":     fields_obj,
            "action_url": card.action_url,
            "source":     card.source,
            "timestamp":  ts,
        }
        body_bytes = json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        secret = cfg.get("secret", "")
        if secret:
            headers["X-Bluebird-Signature-256"] = "sha256=" + hmac.new(
                secret.encode(), body_bytes, hashlib.sha256).hexdigest()
        return {"_body_bytes": body_bytes.decode("utf-8"),
                "_headers": headers}, cfg.get("url", "")

    return None, None


def main():
    if not SECRET:
        log.warning("WEBHOOK_SECRET 未配置：github 来源需在面板生成 Secret，"
                    "未配 secret 的来源一律拒绝请求（401）")
    if not (UI_AUTH_USER and UI_AUTH_PASS):
        log.warning("NOTIFY_AUTH_USER / NOTIFY_AUTH_PASS 未配置：面板与 API 一律拒绝访问（fail-closed）")
    channels = ",".join(c["name"] for c in get_channels()) or "未配置"
    sources = ",".join(s["name"] for s in get_sources()) or "无"
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("青鸟 Bluebird 监听 %s:%s（sources=%s, channels=%s, ui_auth=%s）",
             HOST, PORT, sources, channels, "on" if (UI_AUTH_USER and UI_AUTH_PASS) else "off")
    server.serve_forever()


if __name__ == "__main__":
    main()

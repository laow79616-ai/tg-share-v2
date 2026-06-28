"""
tg_share_v2 主程序 - 优化版
功能：
1. Bot 服务（处理 /start、预览、inline query）
2. Worker 管理（水军号连接、分享任务）
3. IP 代理池管理
4. Web 管理面板 API
5. 定时任务调度

内存优化：
- 按需连接水军号（不全部同时在线）
- 最多5个同时连接
- 连接间隔10秒
- 内存超80%自动清理
"""
import os
import sys
import json
import time
import asyncio
import logging
import random
import hashlib
import psutil
from pathlib import Path
from datetime import datetime, timedelta
from collections import deque

from aiohttp import web
import aiohttp_cors

# Telegram Bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, InlineQueryHandler,
    filters, ContextTypes
)
from telegram import (
    InlineQueryResultCachedPhoto, InlineQueryResultArticle,
    InputTextMessageContent, InlineQueryResultPhoto
)
from telethon.tl.types import (
    ReplyInlineMarkup, KeyboardButtonCallback, KeyboardButtonSwitchInline
)

# 本地模块
from worker import ShareWorker
import auth
from config import (
    DATA_DIR, SESSIONS_DIR, LOGS_DIR,
    load_json, save_json,
    AD_CONFIG_FILE, BOT_CONFIG_FILE, WORKERS_CONFIG_FILE,
BOTS_FILE, TARGETS_FILE, PROXY_POOL_FILE, SCHEDULE_FILE, STATS_FILE
)

# ============ 日志配置 ============
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(name)s] %(levelname)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(str(LOGS_DIR / "app.log"), encoding='utf-8')
    ]
)
logger = logging.getLogger("App")

# ============ 全局状态 ============
workers = {}  # worker_id -> ShareWorker

def _persist_worker_rate_limit(worker_id, rate_limit_count, has_been_banned_24h, is_restricted):
    """持久化水军的限制次数到配置文件"""
    try:
        data = load_json(WORKERS_CONFIG_FILE)
        worker_list = data.get("workers", [])
        for w in worker_list:
            if w["id"] == worker_id:
                w["rate_limit_count"] = rate_limit_count
                w["has_been_banned_24h"] = has_been_banned_24h
                w["is_restricted"] = is_restricted
                break
        save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
    except Exception as e:
        logger.error(f"持久化限制次数失败: {e}")


bot_app = None  # Telegram Bot Application
scheduler_task = None
# 工作流活动日志
activity_log = []  # 最近50条活动记录
current_activity = {}  # 当前正在进行的操作
MAX_ACTIVITY_LOG = 50

def log_activity(action, details="", worker_phone="", target="", status="info"):
    """记录工作流活动"""
    global activity_log
    entry = {
        "time": datetime.now().strftime("%H:%M:%S"),
        "action": action,
        "details": details,
        "worker": worker_phone,
        "target": target,
        "status": status  # info, success, warning, error
    }
    activity_log.append(entry)
    if len(activity_log) > MAX_ACTIVITY_LOG:
        activity_log = activity_log[-MAX_ACTIVITY_LOG:]

connected_count = 0
MAX_CONCURRENT_CONNECTIONS = 20
CONNECTION_INTERVAL = 10  # 秒
MEMORY_THRESHOLD = 80  # %

# ============ IP 代理池管理 ============
class ProxyPool:
    """IP代理池管理"""

    def __init__(self):
        self.proxies = []  # [{host, port, type, username, password, assigned_bots: []}]
        self.load()

    def load(self):
        data = load_json(PROXY_POOL_FILE)
        self.proxies = data.get("proxies", [])

    def save(self):
        save_json(PROXY_POOL_FILE, {"proxies": self.proxies})

    def add_proxy(self, host, port, proxy_type="socks5", username="", password=""):
        proxy = {
            "id": hashlib.md5(f"{host}:{port}".encode()).hexdigest()[:8],
            "host": host,
            "port": int(port),
            "type": proxy_type,
            "username": username,
            "password": password,
            "assigned_bots": [],
            "status": "active"
        }
        self.proxies.append(proxy)
        self.save()
        return proxy

    def remove_proxy(self, proxy_id):
        self.proxies = [p for p in self.proxies if p["id"] != proxy_id]
        self.save()

    def get_proxy_for_worker(self, worker_id):
        """获取分配给某个worker的代理"""
        for proxy in self.proxies:
            if worker_id in proxy.get("assigned_bots", []):
                return proxy
        return None

    def auto_assign(self, worker_ids, bot_ids=None, workers_per_proxy=5, bots_per_proxy=40):
        """一键均匀分配：水军和Bot均匀分配到所有IP"""
        num_proxies = len(self.proxies)
        if num_proxies == 0:
            return {"error": "没有可用的代理IP"}
        # 先清除所有分配
        for proxy in self.proxies:
            proxy["assigned_bots"] = []
            proxy["assigned_bot_accounts"] = []
        # 均匀分配水军号（轮询方式）
        for i, wid in enumerate(worker_ids):
            self.proxies[i % num_proxies]["assigned_bots"].append(wid)
        # 均匀分配Bot（轮询方式）
        if bot_ids:
            for i, bid in enumerate(bot_ids):
                self.proxies[i % num_proxies]["assigned_bot_accounts"].append(bid)
        self.save()
        return {
            "total_workers": len(worker_ids),
            "total_bots": len(bot_ids) if bot_ids else 0,
            "total_proxies": num_proxies,
            "workers_per_proxy": f"{len(worker_ids)//num_proxies}-{len(worker_ids)//num_proxies+1}",
            "bots_per_proxy": f"{len(bot_ids)//num_proxies}-{len(bot_ids)//num_proxies+1}" if bot_ids else "0",
            "assigned_workers": sum(len(p.get("assigned_bots", [])) for p in self.proxies),
            "assigned_bots": sum(len(p.get("assigned_bot_accounts", [])) for p in self.proxies)
        }


    def get_all(self):
        return self.proxies


proxy_pool = ProxyPool()

# ============ Bot 处理器 ============
def load_ads():
    data = load_json(AD_CONFIG_FILE)
    return data.get("ads", [])


def save_ads(ads):
    save_json(AD_CONFIG_FILE, {"ads": ads})


async def bot_start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("👁 预览消息", callback_data="preview_menu")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "欢迎使用快约到家推广系统！\n\n请点击下方按钮预览广告消息：",
        reply_markup=reply_markup
    )


async def bot_preview_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ads = load_ads()
    if not ads:
        await update.message.reply_text("暂无广告，请在管理面板添加广告。")
        return
    keyboard = []
    for i, ad in enumerate(ads):
        keyboard.append([InlineKeyboardButton(
            ad.get("name", f"广告{i+1}"),
            callback_data=f"preview_{i}"
        )])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("请选择要预览的广告：", reply_markup=reply_markup)


async def bot_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "preview_menu":
        ads = load_ads()
        if not ads:
            await query.edit_message_text("暂无广告，请在管理面板添加广告。")
            return
        keyboard = []
        for i, ad in enumerate(ads):
            keyboard.append([InlineKeyboardButton(
                ad.get("name", f"广告{i+1}"),
                callback_data=f"preview_{i}"
            )])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text("请选择要预览的广告：", reply_markup=reply_markup)

    elif data.startswith("preview_"):
        idx = int(data.split("_")[1])
        ads = load_ads()
        if idx >= len(ads):
            await query.edit_message_text("广告不存在")
            return

        ad = ads[idx]
        caption = ad.get("message", "")
        image_url = ad.get("image_url", "")
        image_file_id = ad.get("image_file_id", "")

        keyboard = []
        url_buttons = ad.get("url_buttons", [])
        for btn in url_buttons:
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])

        # 分享按钮
        keyboard.append([InlineKeyboardButton(
            "📤 分享给用户",
            switch_inline_query=str(idx)
        )])
        reply_markup = InlineKeyboardMarkup(keyboard)

        if image_file_id:
            await query.message.reply_photo(
                photo=image_file_id,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        elif image_url:
            await query.message.reply_photo(
                photo=image_url,
                caption=caption,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )
        else:
            await query.message.reply_text(
                caption,
                reply_markup=reply_markup,
                parse_mode="HTML"
            )


async def bot_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    query_text = query.query.strip()
    ads = load_ads()
    results = []

    if query_text.isdigit():
        idx = int(query_text)
        if idx < len(ads):
            ads_to_show = [(idx, ads[idx])]
        else:
            ads_to_show = list(enumerate(ads))
    else:
        ads_to_show = list(enumerate(ads))

    for i, ad in ads_to_show:
        caption = ad.get("message", "")
        image_file_id = ad.get("image_file_id", "")
        image_url = ad.get("image_url", "")

        keyboard = []
        url_buttons = ad.get("url_buttons", [])
        for btn in url_buttons:
            keyboard.append([InlineKeyboardButton(btn["text"], url=btn["url"])])
        reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None

        if image_file_id:
            from telegram import InlineQueryResultCachedPhoto
            result = InlineQueryResultCachedPhoto(
                id=str(i),
                photo_file_id=image_file_id,
                title=ad.get("name", f"广告{i+1}"),
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            results.append(result)
        elif image_url:
            result = InlineQueryResultPhoto(
                id=str(i),
                photo_url=image_url,
                thumbnail_url=image_url,
                title=ad.get("name", f"广告{i+1}"),
                caption=caption,
                parse_mode="HTML",
                reply_markup=reply_markup
            )
            results.append(result)
        else:
            result = InlineQueryResultArticle(
                id=str(i),
                title=ad.get("name", f"广告{i+1}"),
                description=caption[:100],
                input_message_content=InputTextMessageContent(
                    message_text=caption,
                    parse_mode="HTML"
                ),
                reply_markup=reply_markup
            )
            results.append(result)

    await query.answer(results, cache_time=5, is_personal=True)


# ============ Web API 路由 ============
routes = web.RouteTableDef()
# 批量导入模块
from batch_import import register_batch_import_routes


@routes.get("/")
async def index(request):
    """前端页面"""
    frontend_path = Path("/root/tg_share_v2/frontend/index.html")
    if frontend_path.exists():
        return web.FileResponse(frontend_path)
    return web.Response(text="tg_share_v2 running", content_type="text/html")


@web.middleware
async def auth_middleware(request, handler):
    """鉴权中间件: 放行登录端点与静态资源, 其余 /api/* 需有效 token"""
    path = request.path
    if path == "/api/login" or path.startswith("/static/") or not path.startswith("/api/"):
        return await handler(request)
    if not auth.verify_token(request.headers.get("X-Auth-Token", "")):
        return web.json_response({"ok": False, "error": "未授权或登录已过期, 请重新登录"}, status=401)
    return await handler(request)


@routes.post("/api/login")
async def api_login(request):
    """管理员登录, 校验通过返回 token"""
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"ok": False, "error": "请求格式错误"}, status=400)
    username = body.get("username", "")
    password = body.get("password", "")
    if auth.verify_credentials(username, password):
        return web.json_response({"ok": True, "token": auth.create_token(username)})
    return web.json_response({"ok": False, "error": "用户名或密码错误"}, status=401)


@routes.get("/api/status")
async def api_status(request):
    """系统状态"""
    mem = psutil.virtual_memory()
    cpu = psutil.cpu_percent(interval=0.5)
    stats = load_json(STATS_FILE)
    return web.json_response({
        "status": "running",
        "memory_percent": mem.percent,
        "memory_used_mb": round(mem.used / 1024 / 1024, 1),
        "memory_total_mb": round(mem.total / 1024 / 1024, 1),
        "cpu_percent": cpu,
        "workers_total": len(workers),
        "workers_connected": sum(1 for w in workers.values() if w._connected),
        "total_sends": stats.get("total_sends", 0),
        "today_sends": stats.get("today_sends", 0),
        "uptime": time.time() - stats.get("start_time", time.time())
    })


# --- 水军管理 ---
@routes.get("/api/workers")
async def api_workers_list(request):
    """获取水军列表"""
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    # 附加运行时状态
    for w in worker_list:
        wid = w["id"]
        if wid in workers:
            w["runtime_status"] = workers[wid].status
            w["connected"] = workers[wid]._connected
            w["daily_sends"] = workers[wid].daily_sends
            w["last_error"] = workers[wid].last_error
            w["is_restricted"] = workers[wid].is_restricted
            w["is_dead"] = getattr(workers[wid], 'is_dead', False)
            w["restricted_at"] = workers[wid].restricted_at or ""
            w["restricted_reason"] = workers[wid].restricted_reason
            w["rate_limit_count"] = getattr(workers[wid], 'rate_limit_count', 0)
            w["has_been_banned_24h"] = getattr(workers[wid], 'has_been_banned_24h', False)
            w["in_cooldown"] = workers[wid].is_in_cooldown()
            w["cooldown_remaining"] = max(0, int(workers[wid].cooldown_until - time.time())) if workers[wid].is_in_cooldown() else 0
        else:
            w["runtime_status"] = "offline"
            w["connected"] = False
            w["is_restricted"] = False
            w["is_dead"] = False
            w["rate_limit_count"] = 0
            w["has_been_banned_24h"] = False
            w["in_cooldown"] = False
            w["cooldown_remaining"] = 0
    # 附加限制记录
    for w in worker_list:
        # 只显示水军自身的限制次数（rate_limit_count）
        w["restriction_count"] = w.get("rate_limit_count", 0)
    return web.json_response(worker_list)


@routes.post("/api/workers/add")
async def api_worker_add(request):
    """添加水军号"""
    body = await request.json()
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])

    new_worker = {
        "id": f"w_{int(time.time())}_{random.randint(100,999)}",
        "phone": body["phone"],
        "bot_token": body.get("bot_token", ""),
        "bot_username": body.get("bot_username", ""),
        "session_name": body["phone"].replace("+", ""),
        "proxy": None,
        "status": "pending_login",
        "created_at": datetime.now().isoformat()
    }
    worker_list.append(new_worker)
    save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
    return web.json_response({"ok": True, "worker": new_worker})


@routes.post("/api/workers/{worker_id}/connect")
async def api_worker_connect(request):
    """连接水军号"""
    worker_id = request.match_info["worker_id"]
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    wconfig = next((w for w in worker_list if w["id"] == worker_id), None)

    if not wconfig:
        return web.json_response({"ok": False, "error": "Worker不存在"}, status=404)

    bot_config = load_json(BOT_CONFIG_FILE)
    api_id = bot_config.get("api_id")
    api_hash = bot_config.get("api_hash")

    if not api_id or not api_hash:
        return web.json_response({"ok": False, "error": "请先配置API ID和API Hash"}, status=400)

    # 分配代理
    proxy = proxy_pool.get_proxy_for_worker(worker_id)
    if proxy:
        wconfig["proxy"] = {
            "type": proxy["type"],
            "host": proxy["host"],
            "port": proxy["port"],
            "username": proxy.get("username", ""),
            "password": proxy.get("password", "")
        }

    worker = ShareWorker(wconfig, api_id, api_hash)
    worker._on_rate_limit_changed = _persist_worker_rate_limit
    success = await worker.connect()

    if success:
        workers[worker_id] = worker
        return web.json_response({"ok": True, "message": "连接成功"})
    else:
        return web.json_response({"ok": False, "error": worker.last_error}, status=400)



@routes.post("/api/workers/connect-all")
async def api_workers_connect_all(request):
    """一键连接所有水军号"""
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    bot_config = load_json(BOT_CONFIG_FILE)
    api_id = bot_config.get("api_id")
    api_hash = bot_config.get("api_hash")
    if not api_id or not api_hash:
        return web.json_response({"ok": False, "error": "请先配置API ID和API Hash"}, status=400)
    
    results = []
    success_count = 0
    fail_count = 0
    
    for wconfig in worker_list:
        worker_id = wconfig["id"]
        # 如果已经连接，跳过
        if worker_id in workers and workers[worker_id]._connected:
            results.append({"id": worker_id, "phone": wconfig["phone"], "status": "already_connected"})
            success_count += 1
            continue
        
        # 分配代理
        proxy = proxy_pool.get_proxy_for_worker(worker_id)
        if proxy:
            wconfig["proxy"] = {
                "type": proxy["type"],
                "host": proxy["host"],
                "port": proxy["port"],
                "username": proxy.get("username", ""),
                "password": proxy.get("password", "")
            }
        
        worker = ShareWorker(wconfig, api_id, api_hash)
        worker._on_rate_limit_changed = _persist_worker_rate_limit
        worker._on_dead_detected = _on_dead_detected
        try:
            success = await worker.connect()
            if success:
                workers[worker_id] = worker
                results.append({"id": worker_id, "phone": wconfig["phone"], "status": "connected"})
                success_count += 1
            else:
                results.append({"id": worker_id, "phone": wconfig["phone"], "status": "failed", "error": worker.last_error})
                fail_count += 1
        except Exception as e:
            results.append({"id": worker_id, "phone": wconfig["phone"], "status": "failed", "error": str(e)})
            fail_count += 1
    
    return web.json_response({
        "ok": True,
        "message": f"连接完成: {success_count}成功, {fail_count}失败",
        "success_count": success_count,
        "fail_count": fail_count,
        "results": results
    })

@routes.post("/api/workers/{worker_id}/login")
async def api_worker_login(request):
    """水军号登录（发送验证码）"""
    worker_id = request.match_info["worker_id"]
    body = await request.json()
    phone = body.get("phone", "")
    code = body.get("code", "")
    password = body.get("password", "")

    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    wconfig = next((w for w in worker_list if w["id"] == worker_id), None)

    if not wconfig:
        return web.json_response({"ok": False, "error": "Worker不存在"}, status=404)

    bot_config = load_json(BOT_CONFIG_FILE)
    api_id = bot_config.get("api_id")
    api_hash = bot_config.get("api_hash")

    if not api_id or not api_hash:
        return web.json_response({"ok": False, "error": "请先配置API ID和API Hash"}, status=400)

    from telethon import TelegramClient
    session_path = os.path.join(str(SESSIONS_DIR), wconfig["session_name"])

    # 分配代理
    proxy_config = None
    proxy = proxy_pool.get_proxy_for_worker(worker_id)
    if proxy:
        try:
            import python_socks
            proxy_type_str = proxy.get("type", "socks5")
            if proxy_type_str == "socks5":
                p_type = python_socks.ProxyType.SOCKS5
            elif proxy_type_str == "socks4":
                p_type = python_socks.ProxyType.SOCKS4
            else:
                p_type = python_socks.ProxyType.HTTP
            proxy_config = {
                'proxy_type': p_type,
                'addr': proxy["host"],
                'port': int(proxy["port"]),
            }
            if proxy.get("username"):
                proxy_config['username'] = proxy["username"]
                proxy_config['password'] = proxy.get("password", "")
        except ImportError:
            import socks
            proxy_type = socks.SOCKS5
            proxy_config = (proxy_type, proxy["host"], int(proxy["port"]))
            if proxy.get("username"):
                proxy_config = (proxy_type, proxy["host"], int(proxy["port"]),
                              True, proxy["username"], proxy.get("password", ""))

    client = TelegramClient(session_path, api_id, api_hash, proxy=proxy_config)
    await client.connect()

    if not code:
        # 发送验证码
        try:
            result = await client.send_code_request(phone or wconfig["phone"])
            # 保存phone_code_hash
            wconfig["_phone_code_hash"] = result.phone_code_hash
            save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
            await client.disconnect()
            return web.json_response({"ok": True, "step": "code_sent", "message": "验证码已发送"})
        except Exception as e:
            await client.disconnect()
            return web.json_response({"ok": False, "error": str(e)}, status=400)
    else:
        # 验证码登录
        try:
            phone_code_hash = wconfig.get("_phone_code_hash", "")
            await client.sign_in(
                phone=phone or wconfig["phone"],
                code=code,
                phone_code_hash=phone_code_hash
            )
            wconfig["status"] = "active"
            if "_phone_code_hash" in wconfig:
                del wconfig["_phone_code_hash"]
            save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
            await client.disconnect()
            return web.json_response({"ok": True, "step": "logged_in", "message": "登录成功"})
        except Exception as e:
            err_str = str(e)
            if "password" in err_str.lower() or "2fa" in err_str.lower():
                if password:
                    try:
                        await client.sign_in(password=password)
                        wconfig["status"] = "active"
                        save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
                        await client.disconnect()
                        return web.json_response({"ok": True, "step": "logged_in", "message": "登录成功"})
                    except Exception as e2:
                        await client.disconnect()
                        return web.json_response({"ok": False, "error": str(e2)}, status=400)
                else:
                    await client.disconnect()
                    return web.json_response({"ok": True, "step": "need_2fa", "message": "需要两步验证密码"})
            await client.disconnect()
            return web.json_response({"ok": False, "error": err_str}, status=400)


# --- 广告管理 ---
@routes.get("/api/ads")
async def api_ads_list(request):
    ads = load_ads()
    return web.json_response(ads)


@routes.post("/api/ads")
async def api_ads_add(request):
    body = await request.json()
    ads = load_ads()
    ad = {
        "id": f"ad_{int(time.time())}",
        "name": body.get("name", f"广告{len(ads)+1}"),
        "message": body.get("message", ""),
        "image_url": body.get("image_url", ""),
        "image_file_id": body.get("image_file_id", ""),
        "url_buttons": body.get("url_buttons", []),
        "created_at": datetime.now().isoformat()
    }
    ads.append(ad)
    save_ads(ads)
    return web.json_response({"ok": True, "ad": ad})


@routes.delete("/api/ads/{ad_id}")
async def api_ads_delete(request):
    ad_id = request.match_info["ad_id"]
    ads = load_ads()
    ads = [a for a in ads if a.get("id") != ad_id]
    save_ads(ads)
    return web.json_response({"ok": True})


# --- 目标用户管理 ---
@routes.get("/api/targets")
async def api_targets_list(request):
    data = load_json(TARGETS_FILE)
    return web.json_response(data.get("targets", []))


@routes.post("/api/targets")
async def api_targets_add(request):
    body = await request.json()
    data = load_json(TARGETS_FILE)
    targets = data.get("targets", [])
    new_targets = body.get("usernames", [])
    for username in new_targets:
        clean = username.strip().lstrip("@")
        if clean and clean not in [t["username"] for t in targets]:
            targets.append({
                "username": clean,
                "status": "pending",
                "sent_at": None,
                "result": None
            })
    save_json(TARGETS_FILE, {"targets": targets})
    return web.json_response({"ok": True, "count": len(targets)})


@routes.delete("/api/targets/{username}")
async def api_targets_delete(request):
    username = request.match_info["username"]
    data = load_json(TARGETS_FILE)
    targets = data.get("targets", [])
    targets = [t for t in targets if t["username"] != username]
    save_json(TARGETS_FILE, {"targets": targets})
    return web.json_response({"ok": True})


# --- IP代理池管理 ---
@routes.get("/api/proxies")
async def api_proxies_list(request):
    return web.json_response(proxy_pool.get_all())


@routes.post("/api/proxies")
async def api_proxies_add(request):
    body = await request.json()
    proxy = proxy_pool.add_proxy(
        host=body["host"],
        port=body["port"],
        proxy_type=body.get("type", "socks5"),
        username=body.get("username", ""),
        password=body.get("password", "")
    )
    return web.json_response({"ok": True, "proxy": proxy})


@routes.delete("/api/proxies/{proxy_id}")
async def api_proxies_delete(request):
    proxy_id = request.match_info["proxy_id"]
    proxy_pool.remove_proxy(proxy_id)
    return web.json_response({"ok": True})


@routes.post("/api/proxies/auto-assign")
async def api_proxies_auto_assign(request):
    """一键分配IP：每条IP分配5个水军 + 40个Bot"""
    body = await request.json()
    workers_per_proxy = body.get("workers_per_proxy", 5)
    bots_per_proxy = body.get("bots_per_proxy", 40)
    # 兼容旧参数
    if "per_proxy" in body:
        workers_per_proxy = body["per_proxy"]
    data = load_json(WORKERS_CONFIG_FILE)
    worker_ids = [w["id"] for w in data.get("workers", [])]
    # 获取Bot列表
    bots_data = load_json(BOTS_CONFIG_FILE)
    bot_ids = [b.get("username", b.get("name", "")) for b in bots_data.get("bots", [])]
    result = proxy_pool.auto_assign(worker_ids, bot_ids, workers_per_proxy, bots_per_proxy)
    return web.json_response({"ok": True, **result})


@routes.post("/api/proxies/batch")
async def api_proxies_batch_import(request):
    """批量导入代理 - 支持格式: host:port:username:password"""
    body = await request.json()
    lines = body.get("proxies", "").strip().split("\n")
    proxy_type = body.get("type", "http")
    added = 0
    errors = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parts = line.split(":")
        if len(parts) == 4:
            host, port, username, password = parts
        elif len(parts) == 2:
            host, port = parts
            username, password = "", ""
        else:
            errors.append(f"格式错误: {line}")
            continue
        try:
            proxy_pool.add_proxy(host=host, port=int(port), proxy_type=proxy_type, username=username, password=password)
            added += 1
        except Exception as e:
            errors.append(f"{line}: {str(e)}")
    return web.json_response({"ok": True, "added": added, "errors": errors})


# --- 系统设置 ---

# === Bot管理 ===
BOTS_CONFIG_FILE = DATA_DIR / "bots.json"
RESTRICTIONS_FILE = DATA_DIR / "restrictions.json"

def load_restrictions():
    """加载水军+Bot组合的限制记录"""
    data = load_json(RESTRICTIONS_FILE)
    if not data:
        data = {"records": {}}
    return data

def save_restrictions(data):
    """保存限制记录"""
    save_json(RESTRICTIONS_FILE, data)


def auto_delete_dead_worker(worker_phone):
    """当水军被限制达到10次时，自动从系统中删除"""
    import shutil
    logger.warning(f"[自动删除] 水军号 {worker_phone} 被限制达到10次，自动删除")
    # 从workers_config中删除
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    data["workers"] = [w for w in worker_list if w.get("phone") != worker_phone]
    save_json(WORKERS_CONFIG_FILE, data)
    # 删除session文件
    session_name = worker_phone.replace("+", "")
    session_path = SESSIONS_DIR / f"{session_name}.session"
    journal_path = SESSIONS_DIR / f"{session_name}.session-journal"
    if session_path.exists():
        session_path.unlink()
        logger.info(f"[自动删除] 已删除session: {session_path}")
    if journal_path.exists():
        journal_path.unlink()
    logger.info(f"[自动删除] 水军号 {worker_phone} 已从系统中完全删除")


def _on_dead_detected(worker_id, phone, reason):
    """当检测到水军号死亡时自动删除"""
    logger.warning(f"[自动删除-死亡检测] 水军号 {phone} 被TG平台标记死亡: {reason}")
    auto_delete_dead_worker(phone)
    # 从内存中的workers字典删除
    if worker_id in workers:
        del workers[worker_id]
    logger.info(f"[自动删除-死亡检测] 水军号 {phone} 已从系统完全移除")

def record_worker_bot_failure(worker_phone, bot_username, error_msg):
    """记录水军+Bot组合的失败，超过3次标记为禁止"""
    data = load_restrictions()
    records = data.get("records", {})
    key = f"{worker_phone}|{bot_username}"
    if key not in records:
        records[key] = {"worker_phone": worker_phone, "bot_username": bot_username, "fail_count": 0, "banned": False, "errors": [], "last_fail": ""}
    records[key]["fail_count"] += 1
    records[key]["last_fail"] = __import__("datetime").datetime.now().isoformat()
    records[key]["errors"].append(error_msg[:100])
    # 只保留最近5条错误
    records[key]["errors"] = records[key]["errors"][-5:]
    # 超过3次标记为禁止
    if records[key]["fail_count"] >= 3:
        records[key]["banned"] = True
    data["records"] = records
    save_restrictions(data)
    return records[key]["banned"]

def is_worker_bot_banned(worker_phone, bot_username):
    """检查水军+Bot组合是否被禁止"""
    data = load_restrictions()
    key = f"{worker_phone}|{bot_username}"
    record = data.get("records", {}).get(key, {})
    return record.get("banned", False)

def get_worker_restrictions(worker_phone):
    """获取某个水军号的所有限制记录"""
    data = load_restrictions()
    records = data.get("records", {})
    result = []
    for key, record in records.items():
        if record.get("worker_phone") == worker_phone:
            result.append(record)
    return result

@routes.get("/api/bots")
async def api_bots_list(request):
    data = load_json(BOTS_CONFIG_FILE)
    bot_list = data.get("bots", [])
    bot_list.sort(key=lambda b: b.get("number", 0))
    # Bot状态只显示平台级别限制（inline disabled/restricted）
    for bot in bot_list:
        if bot.get("is_restricted"):
            bot["restriction_status"] = "platform_banned"
            bot["restriction_reason"] = bot.get("restricted_reason", "被平台检测禁止使用")
        else:
            bot["restriction_status"] = "normal"
    return web.json_response(bot_list)

@routes.post("/api/bots/batch")
async def api_bots_batch_import(request):
    body = await request.json()
    tokens_text = body.get("tokens", "")
    data = load_json(BOTS_CONFIG_FILE)
    bot_list = data.get("bots", [])
    existing_tokens = {b["token"] for b in bot_list}
    added = 0
    errors = []
    lines = [l.strip() for l in tokens_text.strip().split("\n") if l.strip()]
    for line in lines:
        token = line
        if token in existing_tokens:
            continue
        if ":" not in token:
            errors.append("格式错误: " + line[:30])
            continue
        bot_id_str = token.split(":")[0]
        existing_numbers = [b.get("number", 0) for b in bot_list]
        next_number = max(existing_numbers) + 1 if existing_numbers else 1
        new_bot = {
            "id": "bot_" + bot_id_str,
            "token": token,
            "bot_id": bot_id_str,
            "username": "",
            "number": next_number,
            "status": "pending",
            "enabled": True,
            "total_sends": 0,
            "success_sends": 0,
            "fail_sends": 0
        }
        bot_list.append(new_bot)
        existing_tokens.add(token)
        added += 1
    save_json(BOTS_CONFIG_FILE, {"bots": bot_list})
    return web.json_response({"ok": True, "added": added, "total": len(bot_list), "errors": errors})

@routes.post("/api/bots/{bot_id}/verify")
async def api_bot_verify(request):
    bot_id = request.match_info["bot_id"]
    data = load_json(BOTS_CONFIG_FILE)
    bot_list = data.get("bots", [])
    bot = next((b for b in bot_list if b["id"] == bot_id), None)
    if not bot:
        return web.json_response({"ok": False, "error": "Bot不存在"}, status=404)
    import aiohttp
    async with aiohttp.ClientSession() as session:
        try:
            async with session.get(f"https://api.telegram.org/bot{bot['token']}/getMe", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                result = await resp.json()
                if result.get("ok"):
                    bot_info = result["result"]
                    bot["username"] = bot_info.get("username", "")
                    bot["status"] = "active"
                    bot["enabled"] = True
                    save_json(BOTS_CONFIG_FILE, {"bots": bot_list})
                    return web.json_response({"ok": True, "username": bot["username"]})
                else:
                    bot["status"] = "invalid"
                    save_json(BOTS_CONFIG_FILE, {"bots": bot_list})
                    return web.json_response({"ok": False, "error": result.get("description", "Token无效")})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)})

@routes.post("/api/bots/verify-all")
async def api_bots_verify_all(request):
    data = load_json(BOTS_CONFIG_FILE)
    bot_list = data.get("bots", [])
    import aiohttp
    results = {"active": 0, "invalid": 0, "error": 0}
    async with aiohttp.ClientSession() as session:
        for bot in bot_list:
            try:
                async with session.get(f"https://api.telegram.org/bot{bot['token']}/getMe", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    result = await resp.json()
                    if result.get("ok"):
                        bot["username"] = result["result"].get("username", "")
                        bot["status"] = "active"
                        bot["enabled"] = True
                        results["active"] += 1
                    else:
                        bot["status"] = "invalid"
                        bot["enabled"] = False
                        results["invalid"] += 1
            except:
                bot["status"] = "error"
                results["error"] += 1
    save_json(BOTS_CONFIG_FILE, {"bots": bot_list})
    return web.json_response({"ok": True, "results": results})

@routes.delete("/api/bots/{bot_id}")
async def api_bot_delete(request):
    bot_id = request.match_info["bot_id"]
    data = load_json(BOTS_CONFIG_FILE)
    bot_list = data.get("bots", [])
    bot_list = [b for b in bot_list if b["id"] != bot_id]
    save_json(BOTS_CONFIG_FILE, {"bots": bot_list})
    return web.json_response({"ok": True})

@routes.delete("/api/workers/{worker_id}")
async def api_worker_delete(request):
    """删除水军号"""
    worker_id = request.match_info["worker_id"]
    data = load_json(WORKERS_CONFIG_FILE)
    worker_list = data.get("workers", [])
    original_len = len(worker_list)
    worker_list = [w for w in worker_list if w["id"] != worker_id]
    if len(worker_list) == original_len:
        return web.json_response({"ok": False, "error": "Worker不存在"}, status=404)
    save_json(WORKERS_CONFIG_FILE, {"workers": worker_list})
    # 删除session文件
    import glob
    for f in glob.glob(str(SESSIONS_DIR / "*")):
        if worker_id.split("_")[1] in f or worker_id.split("_")[2] in f:
            try:
                os.remove(f)
            except:
                pass
    # 从workers字典中移除
    if worker_id in workers:
        try:
            await workers[worker_id].disconnect()
        except:
            pass
        del workers[worker_id]
    return web.json_response({"ok": True, "message": "删除成功"})

@routes.get("/api/restrictions")
async def api_restrictions_list(request):
    """获取所有限制记录"""
    data = load_restrictions()
    records = list(data.get("records", {}).values())
    # 按fail_count降序排列
    records.sort(key=lambda r: r.get("fail_count", 0), reverse=True)
    return web.json_response(records)


@routes.post("/api/workers/clear-errors")
async def api_workers_clear_errors(request):
    """清除所有水军的last_error"""
    cleared = 0
    for wid, w in workers.items():
        if w.last_error:
            w.last_error = ""
            cleared += 1
    return web.json_response({"ok": True, "message": f"已清除 {cleared} 个水军的错误信息"})

@routes.post("/api/restrictions/reset")
async def api_restrictions_reset(request):
    """重置限制记录"""
    body = await request.json()
    worker_phone = body.get("worker_phone", "")
    bot_username = body.get("bot_username", "")
    data = load_restrictions()
    if worker_phone and bot_username:
        key = f"{worker_phone}|{bot_username}"
        if key in data.get("records", {}):
            del data["records"][key]
    elif worker_phone:
        # 重置某个水军号的所有记录
        data["records"] = {k: v for k, v in data.get("records", {}).items() if v.get("worker_phone") != worker_phone}
    else:
        # 重置所有
        data["records"] = {}
    save_restrictions(data)
    return web.json_response({"ok": True})

@routes.post("/api/workers/assign-bots")
async def api_workers_assign_bots(request):
    """[Deprecated] 现在使用全局Bot池轮换，不再固定分配"""
    return web.json_response({"ok": True, "message": "当前使用全局Bot池轮换机制，无需固定分配"})

@routes.post("/api/workers/assign-bot")
async def api_workers_assign_bot(request):
    """[Deprecated] 现在使用全局Bot池轮换，不再固定分配"""
    return web.json_response({"ok": True, "message": "当前使用全局Bot池轮换机制，无需固定分配"})

@routes.get("/api/config")
async def api_config_get(request):
    config = load_json(BOT_CONFIG_FILE)
    # 不返回敏感信息
    safe_config = {
        "api_id": config.get("api_id", ""),
        "api_hash": "***" if config.get("api_hash") else "",
        "bot_token": "***" if config.get("bot_token") else "",
        "bot_username": config.get("bot_username", ""),
        "daily_limit": config.get("daily_limit", 30),
        "send_interval_min": config.get("send_interval_min", 180),
        "send_interval_max": config.get("send_interval_max", 300),
    }
    return web.json_response(safe_config)


@routes.post("/api/config")
async def api_config_update(request):
    body = await request.json()
    config = load_json(BOT_CONFIG_FILE)
    for key in ["api_id", "api_hash", "bot_token", "bot_username",
                "daily_limit", "send_interval_min", "send_interval_max"]:
        if key in body:
            config[key] = body[key]
    save_json(BOT_CONFIG_FILE, config)
    return web.json_response({"ok": True})


# --- 发送任务 ---
@routes.post("/api/send/start")
async def api_send_start(request):
    """开始发送任务"""
    global scheduler_task
    if scheduler_task and not scheduler_task.done():
        return web.json_response({"ok": False, "error": "任务已在运行中"})

    scheduler_task = asyncio.create_task(run_send_scheduler())
    return web.json_response({"ok": True, "message": "发送任务已启动"})


@routes.post("/api/send/stop")
async def api_send_stop(request):
    """停止发送任务"""
    global scheduler_task
    if scheduler_task and not scheduler_task.done():
        scheduler_task.cancel()
        scheduler_task = None
    return web.json_response({"ok": True, "message": "发送任务已停止"})


@routes.get("/api/send/status")
async def api_send_status(request):
    """发送任务状态 - 包含工作流信息"""
    running = scheduler_task is not None and not scheduler_task.done()
    # 统计在线水军
    connected_workers = [(wid, w) for wid, w in workers.items() if w._connected]
    cooldown_workers = [(wid, w) for wid, w in workers.items() if w.is_in_cooldown()]
    # 加载统计
    stats = load_json(STATS_FILE)
    return web.json_response({
        "running": running,
        "connected_workers": len(connected_workers),
        "cooldown_workers": len(cooldown_workers),
        "today_sends": stats.get("today_sends", 0),
        "total_sends": stats.get("total_sends", 0),
        "current_activity": current_activity,
        "recent_logs": activity_log[-20:]  # 最近20条
    })

@routes.get("/api/send/activity")
async def api_send_activity(request):
    """获取完整活动日志"""
    return web.json_response({
        "current": current_activity,
        "logs": activity_log[-50:],
        "connected_workers": [(wid, workers[wid].phone if hasattr(workers[wid], 'phone') else wid) for wid, w in workers.items() if w._connected]
    })


# ============ 发送调度器 ============
async def run_send_scheduler():
    """发送调度器 - 按需连接水军号，逐个发送（带异常保护）"""
    try:
        await _run_send_scheduler_inner()
    except Exception as e:
        logger.error(f"=== 发送调度器异常退出: {e} ===")
        import traceback
        logger.error(traceback.format_exc())

async def _run_send_scheduler_inner():
    """发送调度器内部实现"""
    global current_activity
    current_activity = {"status": "启动中", "worker": "", "target": "", "step": "初始化"}
    log_activity("调度器启动", "发送调度器开始运行", status="info")
    logger.info("=== 发送调度器启动 ===")

    config = load_json(BOT_CONFIG_FILE)
    api_id = config.get("api_id")
    api_hash = config.get("api_hash")
    daily_limit = config.get("daily_limit", 30)
    interval_min = config.get("send_interval_min", 180)
    interval_max = config.get("send_interval_max", 300)

    if not api_id or not api_hash:
        logger.error("未配置 API ID/Hash")
        return

    # 加载目标用户
    targets_data = load_json(TARGETS_FILE)
    targets = [t for t in targets_data.get("targets", []) if t["status"] == "pending"]

    if not targets:
        logger.info("没有待发送的目标用户")
        return

    # 加载水军列表
    workers_data = load_json(WORKERS_CONFIG_FILE)
    worker_configs = [w for w in workers_data.get("workers", []) if w.get("status") == "active"]

    if not worker_configs:
        logger.info("没有可用的水军号")
        return

    # 统计
    stats = load_json(STATS_FILE)
    stats["start_time"] = stats.get("start_time", time.time())

    worker_idx = 0
    sent_count = 0

    for target in targets:
        if sent_count >= daily_limit:
            logger.info(f"达到每日限制 {daily_limit}")
            break

        # === 辅助函数: 获取可用水军号 ===
        async def get_available_worker(skip_ids=None):
            """获取一个可用的水军号，跳过指定ID列表"""
            nonlocal worker_idx
            if skip_ids is None:
                skip_ids = set()
            tried = 0
            while tried < len(worker_configs):
                wc = worker_configs[worker_idx % len(worker_configs)]
                worker_idx += 1
                tried += 1
                wid_try = wc["id"]
                if wid_try in skip_ids:
                    continue
                # 跳过被限制或冷却中的
                if wid_try in workers:
                    if workers[wid_try].is_restricted:
                        continue
                    if workers[wid_try].is_in_cooldown():
                        continue
                    if getattr(workers[wid_try], 'is_dead', False):
                        continue
                    if getattr(workers[wid_try], 'needs_disconnect', False):
                        # 如果水军号被限制达到10次，自动删除
                        if getattr(workers[wid_try], 'is_dead', False):
                            auto_delete_dead_worker(workers[wid_try].phone)
                            del workers[wid_try]
                        else:
                            await release_banned_worker(wid_try)
                            workers[wid_try].needs_disconnect = False
                        continue
                # 跳过banned组合过多的水军号（全局Bot池模式：超过80%的Bot被banned才跳过）
                worker_phone_check = wc.get("phone", "")
                if worker_phone_check:
                    restrictions_data = load_restrictions()
                    banned_count = sum(1 for k, r in restrictions_data.get("records", {}).items() 
                                      if r.get("banned") and r.get("worker_phone") == worker_phone_check)
                    bots_data_check = load_json(BOTS_CONFIG_FILE)
                    total_bots = len([b for b in bots_data_check.get("bots", []) if b.get("enabled", True)])
                    if total_bots > 0 and banned_count >= total_bots * 0.8:
                        continue
                # 按需连接
                if wid_try not in workers or not workers[wid_try]._connected:
                    connected_count = sum(1 for w in workers.values() if w._connected)
                    if connected_count >= MAX_CONCURRENT_CONNECTIONS:
                        await disconnect_oldest_worker()
                    proxy = proxy_pool.get_proxy_for_worker(wid_try)
                    if proxy:
                        wc["proxy"] = {
                            "type": proxy["type"],
                            "host": proxy["host"],
                            "port": proxy["port"],
                            "username": proxy.get("username", ""),
                            "password": proxy.get("password", "")
                        }
                    w = ShareWorker(wc, api_id, api_hash)
                    w._on_rate_limit_changed = _persist_worker_rate_limit
                    w._on_dead_detected = _on_dead_detected
                    try:
                        ok = await w.connect()
                    except Exception:
                        continue
                    if not ok:
                        continue
                    workers[wid_try] = w
                    await asyncio.sleep(CONNECTION_INTERVAL)
                w = workers[wid_try]
                try:
                    if not await w.ensure_connected():
                        continue
                except Exception:
                    w._connected = False
                    continue
                return wc, w
            return None, None

        # === 检查是否所有水军号都不可用 ===
        all_unavailable = True
        for wc in worker_configs:
            wid_check = wc["id"]
            if wid_check in workers:
                if not workers[wid_check].is_restricted and not workers[wid_check].is_in_cooldown():
                    all_unavailable = False
                    break
            else:
                all_unavailable = False
                break
        if all_unavailable and workers:
            min_remaining = 600
            for w in workers.values():
                if w.is_in_cooldown():
                    remaining = w._cooldown_until - time.time()
                    if remaining < min_remaining:
                        min_remaining = remaining
            wait_cd = max(int(min_remaining) + 5, 30)
            current_activity = {"status": "等待冷却", "worker": "", "target": target['username'], "step": f"所有水军号冷却中，等待 {wait_cd} 秒"}
            log_activity("等待冷却", f"所有水军号冷却中，等待 {wait_cd}s", target=target['username'], status="warning")
            logger.info(f"[调度] 所有水军号冷却中，等待 {wait_cd} 秒...")
            await asyncio.sleep(wait_cd)

        # 检查内存
        mem = psutil.virtual_memory()
        if mem.percent > MEMORY_THRESHOLD:
            logger.warning(f"内存使用 {mem.percent}%，暂停并清理连接")
            await cleanup_connections()
            await asyncio.sleep(30)

        # === Step 1: 先用一个水军号测试目标用户是否可达 ===
        test_wconfig, test_worker = await get_available_worker()
        if not test_worker:
            logger.warning(f"[调度] 没有可用水军号，等待30秒...")
            await asyncio.sleep(30)
            continue

        current_activity = {"status": "测试目标", "worker": test_wconfig['phone'], "target": target['username'], "step": "验证用户是否存在"}
        log_activity("测试目标", f"验证 @{target['username']} 是否存在", worker_phone=test_wconfig['phone'], target=target['username'])
        logger.info(f"[调度] 测试目标 @{target['username']}（使用 {test_wconfig['phone']}）")
        try:
            user_entity, matched = await test_worker.search_user(target["username"])
        except Exception as e:
            logger.warning(f"[调度] 测试搜索异常: {e}")
            user_entity, matched = None, False

        # 如果目标用户不存在或不匹配，直接标记失败，不浪费发送额度
        if not user_entity:
            target["status"] = "failed"
            target["sent_at"] = datetime.now().isoformat()
            target["result"] = f"目标用户 @{target['username']} 不存在"
            target["bot_username"] = ""
            save_json(TARGETS_FILE, {"targets": targets_data["targets"]})
            logger.info(f"[调度] ❌ 目标 @{target['username']} 不存在，跳过")
            await asyncio.sleep(2)
            continue
        if not matched:
            target["status"] = "failed"
            target["sent_at"] = datetime.now().isoformat()
            target["result"] = "用户名不匹配"
            target["bot_username"] = ""
            save_json(TARGETS_FILE, {"targets": targets_data["targets"]})
            logger.info(f"[调度] ❌ 目标 @{target['username']} 用户名不匹配，跳过")
            await asyncio.sleep(2)
            continue

        current_activity = {"status": "准备发送", "worker": test_wconfig['phone'], "target": target['username'], "step": "目标确认存在，准备分享"}
        log_activity("目标确认", f"@{target['username']} 存在，准备发送", worker_phone=test_wconfig['phone'], target=target['username'], status="success")
        logger.info(f"[调度] ✅ 目标 @{target['username']} 确认存在，开始发送...")

        # === Step 2: 目标可达，正式发送（最多尝试3个不同水军号）===
        max_retry_workers = 3
        tried_worker_ids = set()
        tried_worker_ids.add(test_wconfig["id"])  # 测试用的水军号也可以用来发送
        tried_worker_ids.discard(test_wconfig["id"])  # 先不排除测试号，让它也参与发送
        send_success = False
        send_msg = ""
        send_worker = None

        for attempt in range(max_retry_workers):
            wconfig_send, worker_send = await get_available_worker(skip_ids=tried_worker_ids)
            if not worker_send:
                logger.warning(f"[调度] 没有更多可用水军号")
                break
            tried_worker_ids.add(wconfig_send["id"])
            send_worker = worker_send

            logger.info(f"[调度] 水军 {wconfig_send['phone']} → @{target['username']}（第{attempt+1}次）")
            try:
                success, msg = await worker_send.execute_share_task(target["username"], ad_index=0)
            except Exception as task_err:
                logger.error(f"[调度] 执行任务异常: {task_err}")
                success, msg = False, f"任务异常: {task_err}"
                worker_send._connected = False

            if success:
                send_success = True
                send_msg = msg
                break
            else:
                send_msg = msg
                # 判断失败是否可重试
                is_rate_limit = ("too many" in msg.lower() or "flood" in msg.lower()
                                or "冷却" in msg.lower() or "cooldown" in msg.lower())
                is_target_issue = ("不存在" in msg or "不匹配" in msg or "blocked" in msg.lower()
                                  or "隐私" in msg or "privacy" in msg.lower()
                                  or "forbidden" in msg.lower() or "无法向该用户" in msg)
                if is_target_issue:
                    logger.info(f"[调度] 目标问题({msg})，不再重试")
                    break
                elif is_rate_limit:
                    # 记录水军+Bot组合的失败
                    bot_name = worker_send.current_bot_username if worker_send else ""
                    if bot_name:
                        banned = record_worker_bot_failure(wconfig_send["phone"], bot_name, send_msg)
                        if banned:
                            logger.warning(f"[调度] ⚠️ 水军 {wconfig_send['phone']} + Bot @{bot_name} 已被多次限制，标记为禁止")
                    logger.info(f"[调度] 频率限制，换下一个水军号...")
                    await asyncio.sleep(3)
                    continue
                else:
                    logger.info(f"[调度] 发送失败({msg})，尝试换水军号...")
                    await asyncio.sleep(3)
                    continue

        # === Step 3: 更新目标状态 ===
        # 记录失败到restrictions（Too many requests等）
        if not send_success and send_worker and send_worker.current_bot_username:
            if "too many" in send_msg.lower() or "flood" in send_msg.lower():
                record_worker_bot_failure(
                    wconfig_send["phone"] if wconfig_send else "",
                    send_worker.current_bot_username,
                    send_msg
                )
        if not send_success and send_worker and send_worker.current_bot_username:
            if "Bot" in send_msg or "bot" in send_msg or "inline" in send_msg.lower():
                if "限制" in send_msg or "禁用" in send_msg or "restricted" in send_msg.lower() or "disabled" in send_msg.lower():
                    bot_name = send_worker.current_bot_username
                    bots_data = load_json(BOTS_FILE)
                    for bot in bots_data.get("bots", []):
                        if bot.get("username", "").lstrip("@") == bot_name:
                            bot["is_restricted"] = True
                            bot["restricted_at"] = datetime.now().isoformat()
                            bot["restricted_reason"] = send_msg
                            break
                    save_json(BOTS_FILE, bots_data)
                    logger.warning(f"[调度] Bot @{bot_name} 被标记为限制")

        target["status"] = "sent" if send_success else "failed"
        target["sent_at"] = datetime.now().isoformat()
        target["result"] = send_msg
        target["bot_username"] = send_worker.current_bot_username if send_worker and send_worker.current_bot_username else ""
        save_json(TARGETS_FILE, {"targets": targets_data["targets"]})

        if send_success:
            sent_count += 1
            stats["total_sends"] = stats.get("total_sends", 0) + 1
            stats["today_sends"] = stats.get("today_sends", 0) + 1
            save_json(STATS_FILE, stats)

        # 发送间隔
        if send_success:
            wait_time = random.randint(interval_min, interval_max)
            current_activity = {"status": "等待中", "worker": wconfig_send['phone'] if wconfig_send else "", "target": target['username'], "step": f"发送成功，等待 {wait_time} 秒"}
            log_activity("发送成功", f"成功发送给 @{target['username']}，等待 {wait_time}s", worker_phone=wconfig_send['phone'] if wconfig_send else "", target=target['username'], status="success")
            logger.info(f"[调度] ✅ 发送成功，等待 {wait_time} 秒后继续...")
            await asyncio.sleep(wait_time)
        else:
            await asyncio.sleep(5)
    current_activity = {"status": "已完成", "worker": "", "target": "", "step": f"共发送 {sent_count} 条"}
    log_activity("调度器完成", f"共发送 {sent_count} 条", status="info")
    logger.info(f"=== 发送调度器完成，共发送 {sent_count} 条 ===")


async def cleanup_connections():
    """清理空闲连接"""
    for wid in list(workers.keys()):
        if workers[wid].status == "idle" and workers[wid]._connected:
            await workers[wid].disconnect()
            logger.info(f"清理连接: {wid}")



async def release_banned_worker(wid):
    """释放被禁止/已死水军号的资源（断开连接、释放代理IP）"""
    if wid in workers:
        w = workers[wid]
        if w._connected:
            await w.disconnect()
            logger.info(f"已断开被禁止/已死水军号 {wid}，释放IP和资源")
        # 从proxy_pool中移除该worker的分配（释放IP占用）
        for proxy in proxy_pool.proxies:
            if wid in proxy.get("assigned_bots", []):
                proxy["assigned_bots"].remove(wid)
                logger.info(f"已释放水军号 {wid} 的代理IP {proxy['host']}:{proxy['port']}")
        proxy_pool.save()

async def disconnect_oldest_worker():
    """断开最久未使用的worker"""
    idle_workers = [(wid, w) for wid, w in workers.items() if w._connected and w.status == "idle"]
    if idle_workers:
        wid, worker = idle_workers[0]
        await worker.disconnect()
        logger.info(f"断开最久未使用: {wid}")


# ============ 启动 ============
async def start_bot():
    """启动 Telegram Bot（polling模式）"""
    global bot_app
    config = load_json(BOT_CONFIG_FILE)
    bot_token = config.get("bot_token", "")

    if not bot_token:
        logger.warning("未配置 Bot Token，Bot 功能不可用")
        return

    bot_app = Application.builder().token(bot_token).build()
    bot_app.add_handler(CommandHandler("start", bot_start_command))
    bot_app.add_handler(MessageHandler(
        filters.TEXT & filters.Regex(r".*预览.*"),
        bot_preview_text
    ))
    bot_app.add_handler(CallbackQueryHandler(bot_callback))
    bot_app.add_handler(InlineQueryHandler(bot_inline_query))

    # 使用非阻塞方式启动
    await bot_app.initialize()
    await bot_app.start()
    await bot_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram Bot 已启动 (polling)")


async def stop_bot():
    """停止Bot"""
    global bot_app
    if bot_app:
        await bot_app.updater.stop()
        await bot_app.stop()
        await bot_app.shutdown()


async def on_startup(app):
    """Web应用启动时"""
    # 初始化数据目录
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    # 初始化鉴权 (首次启动生成随机管理员密码, 仅显示一次)
    _cfg, _plain = auth.load_or_init_auth()
    if _plain:
        logger.warning("=" * 56)
        logger.warning("  首次启动 - 已生成 Web 管理面板管理员账号")
        logger.warning("  用户名: admin")
        logger.warning(f"  密码:   {_plain}")
        logger.warning("  请妥善保存! 此密码仅在本次启动显示一次。")
        logger.warning("=" * 56)

    # 初始化统计
    stats = load_json(STATS_FILE)
    stats["start_time"] = time.time()
    save_json(STATS_FILE, stats)

    # Bot polling 已禁用 - 避免与旧服务器冲突
    # 如需启用，取消下面注释:
    # try:
    #     await start_bot()
    # except Exception as e:
    #     logger.error(f"Bot启动失败: {e}")
    logger.info("分享工作模式 - Bot polling 已禁用，由其他服务器处理")


async def on_cleanup(app):
    """Web应用关闭时"""
    # 断开所有worker
    for wid, worker in workers.items():
        try:
            await worker.disconnect()
        except:
            pass

    # 停止Bot
    try:
        await stop_bot()
    except:
        pass


def create_app():
    """创建Web应用"""
    app = web.Application(middlewares=[auth_middleware])
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    register_batch_import_routes(routes)
    app.add_routes(routes)

    # 静态文件
    frontend_dir = Path("/root/tg_share_v2/frontend")
    if frontend_dir.exists():
        app.router.add_static("/static/", frontend_dir, show_index=False)
    avatars_dir = Path("/root/tg_share_v2/data/avatars")
    if avatars_dir.exists():
        app.router.add_static("/static/avatars/", avatars_dir, show_index=False)

    # CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=False,  # 使用 X-Auth-Token 请求头鉴权, 不依赖 cookie
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })
    for route in list(app.router.routes()):
        try:
            cors.add(route)
        except:
            pass

    return app


if __name__ == "__main__":
    app = create_app()
    web.run_app(app, host="0.0.0.0", port=8000)

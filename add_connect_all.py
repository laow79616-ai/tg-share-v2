#!/usr/bin/env python3
"""添加一键全部连接API路由到app.py"""

# 读取app.py
with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 要插入的新API路由代码
new_route = '''
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

'''

# 在 @routes.post("/api/workers/{worker_id}/login") 之前插入
target = '@routes.post("/api/workers/{worker_id}/login")'
if target in content and 'connect-all' not in content:
    content = content.replace(target, new_route + target)
    with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ API路由已添加")
elif 'connect-all' in content:
    print("⚠️ connect-all路由已存在，跳过")
else:
    print("❌ 未找到插入位置")

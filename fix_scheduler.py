#!/usr/bin/env python3
"""修改调度器逻辑：
1. 跳过被限制的水军号
2. 跳过冷却中的水军号
3. 记录Bot被限制的情况
"""

with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 在选择水军号后，添加跳过限制/冷却的逻辑
old_scheduler = '''        wconfig = worker_configs[worker_idx % len(worker_configs)]
        worker_idx += 1
        wid = wconfig["id"]
        # 按需连接
        if wid not in workers or not workers[wid]._connected:'''

new_scheduler = '''        wconfig = worker_configs[worker_idx % len(worker_configs)]
        worker_idx += 1
        wid = wconfig["id"]
        # 跳过被限制或冷却中的水军号
        if wid in workers:
            if workers[wid].is_restricted:
                logger.info(f"[调度] 水军 {wconfig['phone']} 已被限制，跳过")
                continue
            if workers[wid].is_in_cooldown():
                logger.info(f"[调度] 水军 {wconfig['phone']} 冷却中，跳过")
                continue
        # 按需连接
        if wid not in workers or not workers[wid]._connected:'''

if old_scheduler in content:
    content = content.replace(old_scheduler, new_scheduler)
    print("✅ 调度器已添加跳过限制/冷却水军的逻辑")
else:
    print("⚠️ 未找到精确匹配的调度器代码")

# 在execute_share_task后添加Bot限制标记逻辑
old_result = '''        success, msg = await worker.execute_share_task(target["username"], ad_index=0)
        # 更新目标状态
        target["status"] = "sent" if success else "failed"
        target["sent_at"] = datetime.now().isoformat()
        target["result"] = msg
        save_json(TARGETS_FILE, {"targets": targets_data["targets"]})'''

new_result = '''        success, msg = await worker.execute_share_task(target["username"], ad_index=0)
        # 检查Bot是否被限制
        if "Bot" in msg and ("限制" in msg or "禁用" in msg or "restricted" in msg.lower()):
            bot_name = worker.current_bot_username
            if bot_name:
                # 标记Bot被限制
                bots_data = load_json(BOTS_FILE)
                for bot in bots_data.get("bots", []):
                    if bot.get("username", "").lstrip("@") == bot_name:
                        bot["is_restricted"] = True
                        bot["restricted_at"] = datetime.now().isoformat()
                        bot["restricted_reason"] = msg
                        break
                save_json(BOTS_FILE, bots_data)
                logger.warning(f"[调度] ⚠️ Bot @{bot_name} 被标记为限制")
        # 更新目标状态
        target["status"] = "sent" if success else "failed"
        target["sent_at"] = datetime.now().isoformat()
        target["result"] = msg
        save_json(TARGETS_FILE, {"targets": targets_data["targets"]})'''

if old_result in content:
    content = content.replace(old_result, new_result)
    print("✅ 调度器已添加Bot限制标记逻辑")
else:
    print("⚠️ 未找到精确匹配的结果处理代码")

# 确保BOTS_FILE变量存在
if 'BOTS_FILE' not in content:
    # 查找其他FILE变量定义的位置
    if 'TARGETS_FILE' in content:
        content = content.replace(
            'TARGETS_FILE',
            'BOTS_FILE = DATA_DIR / "bots.json"\nTARGETS_FILE',
            1
        )
        print("✅ 添加了BOTS_FILE变量")

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ 调度器修改完成")

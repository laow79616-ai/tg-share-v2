#!/usr/bin/env python3
"""使用行号精确插入修改"""

with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 找到关键行
wid_line = None
connect_line = None
exec_line = None
update_line = None

for i, line in enumerate(lines):
    if '        wid = wconfig["id"]' in line and wid_line is None:
        # 找调度器中的那个（在worker_idx后面）
        if i > 0 and 'worker_idx' in lines[i-1]:
            wid_line = i
    if '        # 按需连接' in line and wid_line is not None and connect_line is None:
        if i > wid_line and i - wid_line < 5:
            connect_line = i
    if 'await worker.execute_share_task(target["username"]' in line:
        exec_line = i
    if '        # 更新目标状态' in line and exec_line is not None and update_line is None:
        if i > exec_line and i - exec_line < 3:
            update_line = i

print(f"wid_line={wid_line}, connect_line={connect_line}, exec_line={exec_line}, update_line={update_line}")

# 1. 在wid赋值后、按需连接前插入跳过逻辑
if connect_line is not None and '# 跳过被限制' not in ''.join(lines[wid_line:connect_line]):
    skip_lines = [
        '        # 跳过被限制或冷却中的水军号\n',
        '        if wid in workers:\n',
        '            if workers[wid].is_restricted:\n',
        '                logger.info(f"[调度] 水军 {wconfig[\'phone\']} 已被限制，跳过")\n',
        '                continue\n',
        '            if workers[wid].is_in_cooldown():\n',
        '                logger.info(f"[调度] 水军 {wconfig[\'phone\']} 冷却中，跳过")\n',
        '                continue\n',
    ]
    for idx, sl in enumerate(skip_lines):
        lines.insert(connect_line + idx, sl)
    print("✅ 已插入跳过限制/冷却逻辑")
    # 重新计算行号偏移
    offset = len(skip_lines)
else:
    offset = 0
    if connect_line is None:
        print("⚠️ 未找到插入位置")
    else:
        print("⚠️ 跳过逻辑已存在")

# 重新找exec和update行（因为插入了新行）
exec_line = None
update_line = None
for i, line in enumerate(lines):
    if 'await worker.execute_share_task(target["username"]' in line:
        exec_line = i
    if '        # 更新目标状态' in line and exec_line is not None and update_line is None:
        if i > exec_line and i - exec_line < 3:
            update_line = i

# 2. 在execute_share_task后、更新目标状态前插入Bot限制标记
if update_line is not None and '# 检查Bot是否被限制' not in ''.join(lines[exec_line:update_line]):
    bot_check_lines = [
        '        # 检查Bot是否被限制\n',
        '        if not success and worker.current_bot_username:\n',
        '            if "Bot" in msg or "bot" in msg or "inline" in msg.lower():\n',
        '                if "限制" in msg or "禁用" in msg or "restricted" in msg.lower() or "disabled" in msg.lower():\n',
        '                    bot_name = worker.current_bot_username\n',
        '                    bots_data = load_json(BOTS_FILE)\n',
        '                    for bot in bots_data.get("bots", []):\n',
        '                        if bot.get("username", "").lstrip("@") == bot_name:\n',
        '                            bot["is_restricted"] = True\n',
        '                            bot["restricted_at"] = datetime.now().isoformat()\n',
        '                            bot["restricted_reason"] = msg\n',
        '                            break\n',
        '                    save_json(BOTS_FILE, bots_data)\n',
        '                    logger.warning(f"[调度] Bot @{bot_name} 被标记为限制")\n',
    ]
    for idx, bl in enumerate(bot_check_lines):
        lines.insert(update_line + idx, bl)
    print("✅ 已插入Bot限制标记逻辑")
else:
    if update_line is None:
        print("⚠️ 未找到更新目标状态的位置")
    else:
        print("⚠️ Bot限制标记已存在")

# 确保BOTS_FILE已定义
content_str = ''.join(lines)
if 'BOTS_FILE' not in content_str:
    # 在TARGETS_FILE前面添加
    for i, line in enumerate(lines):
        if 'TARGETS_FILE' in line and '=' in line and 'DATA_DIR' in line:
            lines.insert(i, 'BOTS_FILE = DATA_DIR / "bots.json"\n')
            print("✅ 添加了BOTS_FILE定义")
            break

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

print("\n✅ 调度器修改完成")

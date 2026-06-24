#!/usr/bin/env python3
"""修改调度器逻辑 - 精确匹配"""

with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

content = ''.join(lines)

# 1. 在 "wid = wconfig[\"id\"]" 后面，"# 按需连接" 前面插入跳过逻辑
insert_after = '        wid = wconfig["id"]\n'
insert_before = '        # 按需连接\n'
skip_logic = '''        # 跳过被限制或冷却中的水军号
        if wid in workers:
            if workers[wid].is_restricted:
                logger.info(f"[调度] 水军 {wconfig[\'phone\']} 已被限制，跳过")
                continue
            if workers[wid].is_in_cooldown():
                logger.info(f"[调度] 水军 {wconfig[\'phone\']} 冷却中，跳过")
                continue
'''

target_str = insert_after + insert_before
if target_str in content and 'is_restricted' not in content.split(target_str)[0].split(target_str)[-1]:
    # 检查是否已经添加过
    if '# 跳过被限制或冷却中的水军号' not in content:
        content = content.replace(target_str, insert_after + skip_logic + insert_before)
        print("✅ 已添加跳过限制/冷却水军的逻辑")
    else:
        print("⚠️ 已存在跳过逻辑")
else:
    print("⚠️ 未找到插入位置")

# 2. 在 execute_share_task 后添加Bot限制标记
old_exec = '        success, msg = await worker.execute_share_task(target["username"], ad_index=0)\n        # 更新目标状态'
new_exec = '''        success, msg = await worker.execute_share_task(target["username"], ad_index=0)
        # 检查Bot是否被限制
        if not success and ("Bot" in msg or "bot" in msg) and ("限制" in msg or "禁用" in msg or "restricted" in msg.lower() or "disabled" in msg.lower()):
            bot_name = worker.current_bot_username
            if bot_name:
                bots_data = load_json(BOTS_FILE)
                for bot in bots_data.get("bots", []):
                    if bot.get("username", "").lstrip("@") == bot_name:
                        bot["is_restricted"] = True
                        bot["restricted_at"] = datetime.now().isoformat()
                        bot["restricted_reason"] = msg
                        break
                save_json(BOTS_FILE, bots_data)
                logger.warning(f"[调度] Bot @{bot_name} 被标记为限制")
        # 更新目标状态'''

if old_exec in content:
    content = content.replace(old_exec, new_exec)
    print("✅ 已添加Bot限制标记逻辑")
else:
    print("⚠️ 未找到execute_share_task结果处理代码")

# 3. 确保BOTS_FILE变量已定义
if "BOTS_FILE" not in content or content.count("BOTS_FILE") < 2:
    # 找到其他FILE定义的位置
    import re
    match = re.search(r'(TARGETS_FILE\s*=\s*DATA_DIR\s*/\s*"targets\.json")', content)
    if match:
        if 'BOTS_FILE = DATA_DIR / "bots.json"' not in content:
            content = content.replace(match.group(1), 'BOTS_FILE = DATA_DIR / "bots.json"\n' + match.group(1))
            print("✅ 添加了BOTS_FILE变量定义")
    else:
        # 尝试另一种方式
        if 'BOTS_FILE' not in content:
            content = content.replace('DATA_DIR = ', 'BOTS_FILE_DEFINED = True\nDATA_DIR = ', 1)
            print("⚠️ 需要手动确认BOTS_FILE")

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ 完成")

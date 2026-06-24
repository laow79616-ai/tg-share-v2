#!/usr/bin/env python3
"""修改app.py的workers API，返回限制/冷却状态信息"""

html_path = '/root/tg_share_v2/app.py'
with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 替换workers列表API中的运行时状态附加逻辑
old_status = '''    # 附加运行时状态
    for w in worker_list:
        wid = w["id"]
        if wid in workers:
            w["runtime_status"] = workers[wid].status
            w["connected"] = workers[wid]._connected
            w["daily_sends"] = workers[wid].daily_sends
            w["last_error"] = workers[wid].last_error
        else:
            w["runtime_status"] = "offline"
            w["connected"] = False'''

new_status = '''    # 附加运行时状态
    for w in worker_list:
        wid = w["id"]
        if wid in workers:
            w["runtime_status"] = workers[wid].status
            w["connected"] = workers[wid]._connected
            w["daily_sends"] = workers[wid].daily_sends
            w["last_error"] = workers[wid].last_error
            w["is_restricted"] = workers[wid].is_restricted
            w["restricted_at"] = workers[wid].restricted_at or ""
            w["restricted_reason"] = workers[wid].restricted_reason
            w["in_cooldown"] = workers[wid].is_in_cooldown()
            w["cooldown_remaining"] = max(0, int(workers[wid].cooldown_until - time.time())) if workers[wid].is_in_cooldown() else 0
        else:
            w["runtime_status"] = "offline"
            w["connected"] = False
            w["is_restricted"] = False
            w["in_cooldown"] = False
            w["cooldown_remaining"] = 0'''

if old_status in content:
    content = content.replace(old_status, new_status)
    print("✅ workers API 已添加限制/冷却状态字段")
else:
    print("⚠️ 未找到精确匹配，尝试部分替换...")
    if 'w["last_error"] = workers[wid].last_error' in content and 'w["is_restricted"]' not in content:
        content = content.replace(
            'w["last_error"] = workers[wid].last_error\n        else:\n            w["runtime_status"] = "offline"\n            w["connected"] = False',
            'w["last_error"] = workers[wid].last_error\n            w["is_restricted"] = workers[wid].is_restricted\n            w["restricted_at"] = workers[wid].restricted_at or ""\n            w["restricted_reason"] = workers[wid].restricted_reason\n            w["in_cooldown"] = workers[wid].is_in_cooldown()\n            w["cooldown_remaining"] = max(0, int(workers[wid].cooldown_until - time.time())) if workers[wid].is_in_cooldown() else 0\n        else:\n            w["runtime_status"] = "offline"\n            w["connected"] = False\n            w["is_restricted"] = False\n            w["in_cooldown"] = False\n            w["cooldown_remaining"] = 0'
        )
        print("✅ 已通过部分匹配添加")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("✅ 完成")

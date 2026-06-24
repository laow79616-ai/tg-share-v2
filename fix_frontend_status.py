#!/usr/bin/env python3
"""
修改前端：
1. 水军列表显示限制状态、冷却状态
2. Bot列表显示限制状态
"""
import re

html_path = '/root/tg_share_v2/frontend/index.html'
with open(html_path, 'r', encoding='utf-8') as f:
    content = f.read()

# 1. 修改水军列表渲染，添加限制/冷却状态显示
old_worker_render = '''const html = workers.map(w => `
                <div class="card">
                    <div class="flex-between">
                        <div>
                            <strong>${w.phone}</strong>
                            <span class="badge ${w.connected ? 'badge-green' : 'badge-red'}">${w.connected ? '在线' : '离线'}</span>
                            <span class="badge badge-blue">${w.runtime_status || w.status}</span>
                            ${w.bot_username ? `<span class="text-gray"> → @${w.bot_username}</span>` : ''}
                        </div>
                        <div class="flex">
                            <button class="btn btn-sm btn-primary" onclick="connectWorker('${w.id}')">连接</button>
                            <button class="btn btn-sm btn-success" onclick="sendCode('${w.id}', '${w.phone}')">发送验证码</button>
                            <button class="btn btn-sm btn-warning" onclick="inputCode('${w.id}', '${w.phone}')">输入验证码</button>
                            <button class="btn btn-sm btn-danger" onclick="deleteWorker('${w.id}')">删除</button>
                        </div>
                    </div>
                    ${w.last_error ? `<div class="text-red" style="margin-top:6px;font-size:12px">错误: ${w.last_error}</div>` : ''}
                    ${w.daily_sends ? `<div class="text-gray" style="margin-top:4px;font-size:12px">今日发送: ${w.daily_sends}</div>` : ''}
                </div>
            `).join('');'''

new_worker_render = '''const html = workers.map(w => `
                <div class="card" style="${w.is_restricted ? 'border-left:3px solid #ff4444;' : w.in_cooldown ? 'border-left:3px solid #ffaa00;' : ''}">
                    <div class="flex-between">
                        <div>
                            <strong>${w.phone}</strong>
                            <span class="badge ${w.connected ? 'badge-green' : 'badge-red'}">${w.connected ? '在线' : '离线'}</span>
                            <span class="badge badge-blue">${w.runtime_status || w.status}</span>
                            ${w.is_restricted ? '<span class="badge" style="background:#ff4444;color:#fff">⚠️已限制</span>' : ''}
                            ${w.in_cooldown ? '<span class="badge" style="background:#ffaa00;color:#000">⏸冷却' + w.cooldown_remaining + 's</span>' : ''}
                            ${w.bot_username ? `<span class="text-gray"> → @${w.bot_username}</span>` : ''}
                        </div>
                        <div class="flex">
                            <button class="btn btn-sm btn-primary" onclick="connectWorker('${w.id}')">连接</button>
                            <button class="btn btn-sm btn-success" onclick="sendCode('${w.id}', '${w.phone}')">发送验证码</button>
                            <button class="btn btn-sm btn-warning" onclick="inputCode('${w.id}', '${w.phone}')">输入验证码</button>
                            <button class="btn btn-sm btn-danger" onclick="deleteWorker('${w.id}')">删除</button>
                        </div>
                    </div>
                    ${w.is_restricted ? `<div class="text-red" style="margin-top:6px;font-size:12px">⚠️ 限制原因: ${w.restricted_reason} (${w.restricted_at})</div>` : ''}
                    ${w.last_error ? `<div class="text-red" style="margin-top:6px;font-size:12px">错误: ${w.last_error}</div>` : ''}
                    ${w.daily_sends ? `<div class="text-gray" style="margin-top:4px;font-size:12px">今日发送: ${w.daily_sends}</div>` : ''}
                </div>
            `).join('');'''

if old_worker_render in content:
    content = content.replace(old_worker_render, new_worker_render)
    print("✅ 水军列表限制/冷却状态显示已添加")
else:
    print("⚠️ 未找到水军列表渲染代码，尝试模糊匹配...")
    # 尝试部分匹配
    if 'w.runtime_status || w.status' in content and 'w.is_restricted' not in content:
        # 在status badge后面添加限制标记
        content = content.replace(
            '${w.bot_username ? `<span class="text-gray"> → @${w.bot_username}</span>` : \'\'}',
            '${w.is_restricted ? \'<span class="badge" style="background:#ff4444;color:#fff">⚠️已限制</span>\' : \'\'}\n                            ${w.in_cooldown ? \'<span class="badge" style="background:#ffaa00;color:#000">⏸冷却\' + w.cooldown_remaining + \'s</span>\' : \'\'}\n                            ${w.bot_username ? `<span class="text-gray"> → @${w.bot_username}</span>` : \'\'}'
        )
        # 在last_error前面添加限制原因
        if '${w.last_error' in content and 'w.restricted_reason' not in content:
            content = content.replace(
                '${w.last_error ?',
                '${w.is_restricted ? `<div class="text-red" style="margin-top:6px;font-size:12px">⚠️ 限制原因: ${w.restricted_reason}</div>` : \'\'}\n                    ${w.last_error ?'
            )
        print("✅ 已通过模糊匹配添加限制状态显示")

with open(html_path, 'w', encoding='utf-8') as f:
    f.write(content)

print("\n✅ 前端修改完成")

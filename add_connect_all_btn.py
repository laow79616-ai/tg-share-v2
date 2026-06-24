#!/usr/bin/env python3
"""在前端添加一键全部连接按钮"""

with open('/root/tg_share_v2/frontend/index.html', 'r', encoding='utf-8') as f:
    content = f.read()

# 在"批量分配Bot"按钮前面添加"一键全部连接"按钮
old_btns = '<button class="btn btn-warning" onclick="showBatchAssignBot()">&#x1F916; 批量分配Bot</button>'
new_btns = '<button class="btn btn-success" onclick="connectAllWorkers()">&#x1F517; 一键全部连接</button> <button class="btn btn-warning" onclick="showBatchAssignBot()">&#x1F916; 批量分配Bot</button>'

if 'connectAllWorkers' not in content:
    content = content.replace(old_btns, new_btns)
    
    # 在connectWorker函数之前添加connectAllWorkers函数
    connect_all_func = '''
        async function connectAllWorkers() {
            if (!confirm('确认一键连接所有水军号？')) return;
            const btn = event.target;
            btn.disabled = true;
            btn.textContent = '⏳ 连接中...';
            try {
                const r = await fetch(API + '/api/workers/connect-all', {method: 'POST'});
                const d = await r.json();
                if (d.ok) {
                    alert(d.message);
                } else {
                    alert('连接失败: ' + d.error);
                }
            } catch(e) {
                alert('请求失败: ' + e.message);
            }
            btn.disabled = false;
            btn.textContent = '🔗 一键全部连接';
            loadWorkers();
        }
'''
    # 在 connectWorker 函数前插入
    content = content.replace('        async function connectWorker(id) {', connect_all_func + '        async function connectWorker(id) {')
    
    with open('/root/tg_share_v2/frontend/index.html', 'w', encoding='utf-8') as f:
        f.write(content)
    print("✅ 前端一键连接按钮已添加")
else:
    print("⚠️ 按钮已存在，跳过")

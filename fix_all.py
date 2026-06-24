#!/usr/bin/env python3
"""
修复所有问题：
1. worker.py: 分享方式改为转发广告消息（不用inline query）
2. worker.py: 发送后验证已读状态，未送达暂停10分钟
3. worker.py: 被限制时标记水军号
4. app.py: login路由代理格式修复
"""

import os

# ============ 修复 app.py 中 login 路由的代理格式 ============
print("=== 修复 app.py login 路由代理格式 ===")
with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    app_content = f.read()

# 替换login路由中的旧代理格式
old_proxy_code = '''    proxy_config = None
    proxy = proxy_pool.get_proxy_for_worker(worker_id)
    if proxy:
        import socks
        proxy_type = socks.SOCKS5
        proxy_config = (proxy_type, proxy["host"], proxy["port"])
        if proxy.get("username"):
            proxy_config = (proxy_type, proxy["host"], proxy["port"],
                          True, proxy["username"], proxy.get("password", ""))
    client = TelegramClient(session_path, api_id, api_hash, proxy=proxy_config)'''

new_proxy_code = '''    proxy_config = None
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
    client = TelegramClient(session_path, api_id, api_hash, proxy=proxy_config)'''

if old_proxy_code in app_content:
    app_content = app_content.replace(old_proxy_code, new_proxy_code)
    print("✅ login路由代理格式已修复")
else:
    print("⚠️ 未找到旧代理代码，可能已修复或格式不同")
    # 尝试更宽松的匹配
    if 'import socks\n        proxy_type = socks.SOCKS5' in app_content:
        print("  尝试替换...")
        app_content = app_content.replace(
            'import socks\n        proxy_type = socks.SOCKS5\n        proxy_config = (proxy_type, proxy["host"], proxy["port"])\n        if proxy.get("username"):\n            proxy_config = (proxy_type, proxy["host"], proxy["port"],\n                          True, proxy["username"], proxy.get("password", ""))',
            '''try:
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
                              True, proxy["username"], proxy.get("password", ""))'''
        )
        print("  ✅ 已替换")

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.write(app_content)

print("\n=== 完成 ===")

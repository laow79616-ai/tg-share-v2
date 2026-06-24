#!/usr/bin/env python3
"""修复app.py中f-string的引号冲突"""

with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    # 修复 f"... {wconfig["phone"]} ..." 这种引号冲突
    if 'wconfig["phone"]' in line and 'logger.info' in line:
        lines[i] = line.replace('wconfig["phone"]', "wconfig['phone']")
        print(f"修复行 {i+1}: {lines[i].rstrip()}")

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 验证语法
import subprocess
result = subprocess.run(['python3', '-c', 'import py_compile; py_compile.compile("/root/tg_share_v2/app.py", doraise=True)'], 
                      capture_output=True, text=True)
if result.returncode == 0:
    print("\n✅ 语法检查通过")
else:
    print(f"\n❌ 语法错误: {result.stderr}")

#!/usr/bin/env python3
"""修复第1041行的语法错误"""

with open('/root/tg_share_v2/app.py', 'r', encoding='utf-8') as f:
    lines = f.readlines()

# 第1041行（0-indexed: 1040）
for i, line in enumerate(lines):
    if "chr(39)+chr(39)" in line:
        lines[i] = "                logger.info(f\"[调度] 水军 {wconfig['phone']} 已被限制，跳过\")\n"
        print(f"修复行 {i+1}")

with open('/root/tg_share_v2/app.py', 'w', encoding='utf-8') as f:
    f.writelines(lines)

# 验证语法
import subprocess
result = subprocess.run(['python3', '-c', 'import py_compile; py_compile.compile("/root/tg_share_v2/app.py", doraise=True)'], 
                      capture_output=True, text=True)
if result.returncode == 0:
    print("✅ 语法检查通过")
else:
    print(f"❌ 语法错误: {result.stderr[:300]}")

import os
import re

def refactor_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    original = content

    # 1. async to_thread replacement
    content = re.sub(r'await\s+asyncio\.to_thread\(\s*db\.([a-zA-Z0-9_]+)(.*?)\)', r'await db.\1(\2)', content)
    def fix_args(match):
        func = match.group(1)
        args = match.group(2)
        if args.startswith(','): args = args[1:].strip()
        return f"await db.{func}({args})"
    content = re.sub(r'await\s+db\.([a-zA-Z0-9_]+)\((.*?)\)', fix_args, content)

    # 2. to_thread replacement (no await)
    content = re.sub(r'asyncio\.to_thread\(\s*db\.([a-zA-Z0-9_]+)(.*?)\)', r'db.\1(\2)', content)
    def fix_args_no_await(match):
        func = match.group(1)
        args = match.group(2)
        if args.startswith(','): args = args[1:].strip()
        return f"db.{func}({args})"
    content = re.sub(r'(?<!await\s)db\.([a-zA-Z0-9_]+)\((.*?)\)', fix_args_no_await, content)

    # 3. Connection and cursor asyncification
    content = re.sub(r'\bwith\s+db\.get_connection\(\)\s+as\s+(\w+):', r'async with db.get_connection() as \1:', content)
    content = re.sub(r'\bwith\s+get_connection\(\)\s+as\s+(\w+):', r'async with get_connection() as \1:', content)
    
    # We must ensure we don't add multiple awaits if we run script multiple times
    content = re.sub(r'(?<!await\s)conn\.commit\(\)', r'await conn.commit()', content)
    content = re.sub(r'(?<!await\s)cursor\.execute\(', r'await cursor.execute(', content)
    content = re.sub(r'(?<!await\s)cursor\.fetchone\(\)', r'await cursor.fetchone()', content)
    content = re.sub(r'(?<!await\s)cursor\.fetchall\(\)', r'await cursor.fetchall()', content)
    content = re.sub(r'(?<!await\s)cursor\.close\(\)', r'await cursor.close()', content)

    if content != original:
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(content)
        print(f"Refactored {filepath}")

for root, _, files in os.walk('/root/Bot-telegram-'):
    for file in files:
        if file.endswith('.py') and file not in ('database.py', 'refactor_v2.py'):
            refactor_file(os.path.join(root, file))

print("Other files refactored.")

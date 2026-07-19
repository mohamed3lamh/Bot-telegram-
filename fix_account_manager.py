import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    funcs = ["_fetch", "_disable", "_enable"]
    for func in funcs:
        content = content.replace(f"def {func}():\n", f"async def {func}():\n")
        content = content.replace(f"def {func}():\r\n", f"async def {func}():\r\n")
        content = content.replace(f"await asyncio.to_thread({func})", f"await {func}()")

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file('/root/Bot-telegram-/telegram_checker/account_manager.py')

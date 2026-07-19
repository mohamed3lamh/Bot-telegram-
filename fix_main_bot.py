import re

def fix_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # def _delete_user(): -> async def _delete_user():
    content = content.replace("def _delete_user():\n", "async def _delete_user():\n")
    # await asyncio.to_thread(_delete_user) -> await _delete_user()
    content = content.replace("await asyncio.to_thread(_delete_user)", "await _delete_user()")

    # def _get_ids(): -> async def _get_ids():
    content = content.replace("def _get_ids():\n", "async def _get_ids():\n")
    # await asyncio.to_thread(_get_ids) -> await _get_ids()
    content = content.replace("await asyncio.to_thread(_get_ids)", "await _get_ids()")
    
    # line 202: await asyncio.to_thread( ? Let's see what is there.
    # It might be multiline.
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

fix_file('/root/Bot-telegram-/main_bot.py')

import re

with open('telegram_checker/checker.py', 'r', encoding='utf-8') as f:
    content = f.read()

pattern = r"checker_bot = await asyncio\.to_thread\(db\.get_setting, \"checker_bot_username\"\)\n\s*if checker_bot:\n\s*logger\.info\(f\"\[Layer 4: ExternalBot\] Checking \{phone\} via @\{checker_bot\}\.\.\.\"\)\n\s*ext_result = await self\._check_via_external_bot\(client, phone, checker_bot\)"

replacement = """checker_bot = await asyncio.to_thread(db.get_setting, "checker_bot_username")
            if checker_bot:
                logger.info(f"[Layer 4: ExternalBot] Checking {phone} via @{checker_bot}...")
                
                manager_account_id = await asyncio.to_thread(db.get_setting, "external_checker_account_id")
                ext_client = client
                
                if manager_account_id and str(manager_account_id).isdigit():
                    manager_account_id = int(manager_account_id)
                    if account["id"] != manager_account_id:
                        accounts = await account_manager.get_all_accounts()
                        manager_account = next((acc for acc in accounts if acc["id"] == manager_account_id), None)
                        if manager_account and manager_account.get("status") == "active":
                            try:
                                ext_client = await telegram_client_manager.get_client(manager_account)
                                logger.info(f"[Layer 4: ExternalBot] Handed off to Manager Account ID: {manager_account_id}")
                            except Exception as e:
                                logger.error(f"[Layer 4: ExternalBot] Failed to get manager client: {e}. Falling back to current worker.")
                        else:
                            logger.warning(f"[Layer 4: ExternalBot] Manager account {manager_account_id} not found or inactive. Falling back to current worker.")
                
                ext_result = await self._check_via_external_bot(ext_client, phone, checker_bot)"""

if pattern in content or re.search(pattern, content):
    content = re.sub(pattern, replacement, content)
    with open('telegram_checker/checker.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print("checker.py manager logic updated.")
else:
    print("Could not find the pattern in checker.py!")


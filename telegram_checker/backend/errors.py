class BackendError(Exception):
    """الخطأ الأساسي لأي استثناء يخص واجهة Telegram Backend"""
    pass

class BackendFloodWaitError(BackendError):
    def __init__(self, seconds: int, message: str = "Flood wait error"):
        self.seconds = seconds
        super().__init__(f"{message} (wait {seconds}s)")

class BackendPhoneBannedError(BackendError):
    pass

class BackendPrivacyError(BackendError):
    pass

class BackendPhoneUnoccupiedError(BackendError):
    pass

class BackendPhoneInvalidError(BackendError):
    pass

class BackendSessionPasswordNeededError(BackendError):
    pass

class BackendPhoneMigrateError(BackendError):
    def __init__(self, new_dc: int, message: str = "Phone migrate error"):
        self.new_dc = new_dc
        super().__init__(f"{message} (migrate to DC {new_dc})")

class BackendSessionUnauthorizedError(BackendError):
    pass

class BackendCodeExpiredError(BackendError):
    pass

class BackendCodeInvalidError(BackendError):
    pass

class BackendApiIdInvalidError(BackendError):
    pass

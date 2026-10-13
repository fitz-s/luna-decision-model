"""Safe, typed failures shared by admission, transport and rendering."""
class Reject(Exception):
    def __init__(self, status, code, message, *, retryable=False):
        super().__init__(message)
        self.status, self.code, self.message = status, code, message
        self.retryable = retryable
        self.auth_recoverable = status == 401 and code in ("upstream_failure", "token_expired", "invalid_token", "invalid_access_token", "expired_token")
        self.headers = {}
        self.usage = {}


def bad(message):
    return Reject(422, "invalid_request", message)

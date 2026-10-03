"""Temporary provider failures that the router may retry within its call budget."""


class TemporaryProviderError(RuntimeError):
    def __init__(self, message, status, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after

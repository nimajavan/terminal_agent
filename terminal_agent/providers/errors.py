"""Temporary provider failures that the router may retry within its call budget."""


class TemporaryProviderError(RuntimeError):
    def __init__(self, message, status, retry_after=None):
        super().__init__(message)
        self.status = status
        self.retry_after = retry_after


class InvalidModelPlan(ValueError):
    """An invalid model response may receive one accounted schema-correction call."""
    def __init__(self, message, raw_output):
        super().__init__(message)
        self.raw_output = raw_output

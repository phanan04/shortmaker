class NeedsInput(Exception):
    """User action or missing local dependency, not an automatic retry."""


class ProviderError(Exception):
    """Retryable remote/local service failure."""


class Interrupted(Exception):
    pass

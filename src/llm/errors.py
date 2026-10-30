class LLMError(Exception):
    pass


class ProviderResolutionError(LLMError):
    pass


class ProviderUnsupportedError(LLMError):
    pass

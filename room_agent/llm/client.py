"""The Anthropic client, created on first use."""

_client = None


def client():
    global _client
    if _client is None:
        from anthropic import Anthropic

        _client = Anthropic()
    return _client

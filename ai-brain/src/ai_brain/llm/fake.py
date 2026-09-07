"""A scripted provider, so chain and agent behaviour can be tested offline.

The script is consumed in order: a ``Reply`` is returned, a ``ProviderError``
is raised. Running past the end is a test bug, not a runtime condition, so it
fails loudly with ``AssertionError`` rather than inventing an answer.
"""

from __future__ import annotations

from ai_brain.llm import Message, Provider, ProviderError, Reply, ToolSpec


class FakeProvider(Provider):
    def __init__(self, key: str, script: list[Reply | ProviderError]):
        self.key = key
        self.script = list(script)
        self.calls: list[tuple[list[Message], list[ToolSpec]]] = []

    async def complete(
        self, messages: list[Message], tools: list[ToolSpec], max_tokens: int
    ) -> Reply:
        self.calls.append((messages, tools))
        if not self.script:
            raise AssertionError("FakeProvider script exhausted")
        item = self.script.pop(0)
        if isinstance(item, ProviderError):
            raise item
        return item

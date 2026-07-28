"""An in-memory double for llm.client.LLMClient. Returns canned responses
from a queue the test controls, so retry-loop and error-path behavior can
be exercised without any network calls.
"""

from __future__ import annotations


class FakeLLMClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self.calls: list[str] = []

    def research(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of canned responses")
        return self._responses.pop(0)

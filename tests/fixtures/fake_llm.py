"""An in-memory double for llm.client.LLMClient. Returns canned responses
from a queue the test controls, so retry-loop and error-path behavior can
be exercised without any network calls.
"""

from __future__ import annotations


class FakeLLMClient:
    def __init__(
        self, responses: list[str], extract_responses: list[str] | None = None
    ) -> None:
        self._responses = list(responses)
        self._extract_responses = list(extract_responses or [])
        self.calls: list[str] = []
        self.extract_calls: list[tuple[str, str]] = []

    def research(self, prompt: str) -> str:
        self.calls.append(prompt)
        if not self._responses:
            raise AssertionError("FakeLLMClient ran out of canned responses")
        return self._responses.pop(0)

    def extract(self, prompt: str, page_text: str) -> str:
        self.extract_calls.append((prompt, page_text))
        if not self._extract_responses:
            raise AssertionError("FakeLLMClient ran out of canned extract responses")
        return self._extract_responses.pop(0)

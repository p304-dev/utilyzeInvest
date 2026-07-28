"""Anthropic client wrapper: a single research call with the server-side
web_search tool enabled. Returns raw text; framework/validation.py handles
parsing and schema validation.
"""

from __future__ import annotations

import anthropic

_MAX_PAUSE_RESUMES = 2


class LLMRefusalError(Exception):
    """Raised when the model declines the request (stop_reason == 'refusal')."""

    def __init__(self, category: str | None, explanation: str | None) -> None:
        super().__init__(f"Request refused (category={category}): {explanation}")
        self.category = category
        self.explanation = explanation


class LLMClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        effort: str,
        max_tokens: int,
        web_search_max_uses: int,
    ) -> None:
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._effort = effort
        self._max_tokens = max_tokens
        self._web_search_max_uses = web_search_max_uses

    def research(self, prompt: str) -> str:
        """Single research call with web search enabled. Returns the
        concatenated text of the final response's text blocks. Transparently
        resumes on stop_reason == 'pause_turn' (the server-side web-search
        loop hit its internal iteration cap), per the Anthropic resume
        contract: re-send [user_msg, assistant_response] unchanged."""
        messages = [{"role": "user", "content": prompt}]
        tools = [
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": self._web_search_max_uses,
            }
        ]

        response = self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            output_config={"effort": self._effort},
            tools=tools,
            messages=messages,
        )

        resumes = 0
        while response.stop_reason == "pause_turn" and resumes < _MAX_PAUSE_RESUMES:
            messages = [
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": response.content},
            ]
            response = self._client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                output_config={"effort": self._effort},
                tools=tools,
                messages=messages,
            )
            resumes += 1

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None) if response.stop_details else None
            explanation = getattr(response.stop_details, "explanation", None) if response.stop_details else None
            raise LLMRefusalError(category, explanation)

        return "".join(block.text for block in response.content if block.type == "text")

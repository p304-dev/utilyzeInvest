"""Parse + validate LLM JSON responses against a worker's pydantic schema.

Pure and network-free by design: the retry *loop* (call LLM, validate,
build a nudge prompt, call again) lives in framework/runner.py. Keeping
this module free of network calls means it's testable with plain strings
and a toy pydantic model, no mocks required.
"""

from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

_FENCE_RE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class SchemaValidationError(Exception):
    """Raised when raw LLM text is not valid JSON, or doesn't satisfy the
    target schema. Carries the raw text so callers can log/nudge on it."""

    def __init__(self, message: str, raw_text: str) -> None:
        super().__init__(message)
        self.raw_text = raw_text


def extract_json_block(text: str) -> str:
    """Strip a ```json ... ``` (or bare ``` ... ```) fence if present.
    Returns the text unchanged if it isn't fenced."""
    stripped = text.strip()
    match = _FENCE_RE.match(stripped)
    if match:
        return match.group(1).strip()
    return stripped


def parse_and_validate(raw_text: str, schema: type[BaseModel]) -> BaseModel:
    """json.loads + schema.model_validate. Raises SchemaValidationError on
    either a JSON parse failure or a pydantic validation failure."""
    candidate = extract_json_block(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SchemaValidationError(f"Invalid JSON: {exc}", raw_text) from exc

    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise SchemaValidationError(f"Schema validation failed: {exc}", raw_text) from exc


def build_retry_prompt(original_prompt: str, error: SchemaValidationError) -> str:
    """Append a correction nudge to the original prompt for a retry call."""
    return (
        f"{original_prompt}\n\n"
        "---\n"
        "Your previous response was not valid JSON matching the required "
        f"schema. Error: {error}\n"
        "Return ONLY valid JSON matching the schema above — no markdown "
        "fences, no commentary, no explanation."
    )

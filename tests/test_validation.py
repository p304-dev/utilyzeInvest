from __future__ import annotations

import pytest
from pydantic import BaseModel

from framework.validation import (
    SchemaValidationError,
    build_retry_prompt,
    extract_json_block,
    parse_and_validate,
)


class ToySchema(BaseModel):
    name: str
    confidence: float
    has_contact_form: bool


VALID_JSON = '{"name": "Acme Ventures", "confidence": 0.8, "has_contact_form": true}'


def test_parse_and_validate_plain_json():
    result = parse_and_validate(VALID_JSON, ToySchema)
    assert result.name == "Acme Ventures"
    assert result.confidence == 0.8


def test_parse_and_validate_strips_json_fence():
    fenced = f"```json\n{VALID_JSON}\n```"
    result = parse_and_validate(fenced, ToySchema)
    assert result.name == "Acme Ventures"


def test_parse_and_validate_strips_bare_fence():
    fenced = f"```\n{VALID_JSON}\n```"
    result = parse_and_validate(fenced, ToySchema)
    assert result.name == "Acme Ventures"


def test_extract_json_block_unfenced_passthrough():
    assert extract_json_block(VALID_JSON) == VALID_JSON


def test_malformed_json_raises():
    with pytest.raises(SchemaValidationError):
        parse_and_validate("{not valid json", ToySchema)


def test_missing_required_field_raises():
    # has_contact_form is missing entirely
    bad = '{"name": "Acme Ventures", "confidence": 0.8}'
    with pytest.raises(SchemaValidationError):
        parse_and_validate(bad, ToySchema)


def test_build_retry_prompt_includes_original_error():
    try:
        parse_and_validate("{not valid json", ToySchema)
    except SchemaValidationError as exc:
        retry_prompt = build_retry_prompt("ORIGINAL PROMPT", exc)
        assert "ORIGINAL PROMPT" in retry_prompt
        assert "valid JSON" in retry_prompt
        assert "Invalid JSON" in retry_prompt
    else:
        pytest.fail("expected SchemaValidationError")

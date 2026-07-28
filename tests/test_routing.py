from __future__ import annotations

from workers.vc_research.schema import VCResearchResult
from workers.vc_research.worker import (
    CHANNEL_APPLY,
    CHANNEL_CONTACT_BOX,
    CHANNEL_EMAIL,
    CHANNEL_LINKEDIN,
    VCResearchWorker,
)


def _result(**overrides) -> VCResearchResult:
    base = dict(
        investor_name="Acme Ventures",
        website="https://acme.vc",
        application_link=None,
        email=None,
        contact_first_name=None,
        contact_last_name=None,
        has_contact_form=False,
        draft_subject="Hi",
        draft_body="Hello",
        confidence=0.9,
        source_urls=["https://acme.vc"],
    )
    base.update(overrides)
    return VCResearchResult(**base)


def test_application_link_wins_even_with_email_and_contact_form():
    worker = VCResearchWorker()
    result = _result(
        application_link="https://apply.acme.vc",
        email="partner@acme.vc",
        contact_first_name="Jane",
        has_contact_form=True,
    )
    assert worker.route(result) == CHANNEL_APPLY


def test_named_partner_with_email_routes_to_email():
    worker = VCResearchWorker()
    result = _result(email="jane@acme.vc", contact_first_name="Jane")
    assert worker.route(result) == CHANNEL_EMAIL


def test_contact_form_without_usable_email_routes_to_contact_box():
    worker = VCResearchWorker()
    result = _result(has_contact_form=True, email=None)
    assert worker.route(result) == CHANNEL_CONTACT_BOX


def test_no_signals_falls_back_to_linkedin():
    worker = VCResearchWorker()
    result = _result()
    assert worker.route(result) == CHANNEL_LINKEDIN


def test_email_without_partner_name_falls_back_to_linkedin():
    # Email alone (no named partner) doesn't satisfy the "named partner +
    # official email" rule, and an existing email makes it not a usable
    # Contact Box case either.
    worker = VCResearchWorker()
    result = _result(email="info@acme.vc", contact_first_name=None)
    assert worker.route(result) == CHANNEL_LINKEDIN

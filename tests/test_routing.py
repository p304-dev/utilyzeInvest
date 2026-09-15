from __future__ import annotations

from workers.vc_research.schema import VCResearchResult
from workers.vc_research.worker import (
    CHANNEL_APPLY,
    CHANNEL_CONTACT_BOX,
    CHANNEL_EMAIL,
    CHANNEL_LINKEDIN,
    CHANNEL_MANUAL_REVIEW,
    CHANNEL_RESEARCH_FAILED,
    CHANNEL_TWITTER,
    VCResearchWorker,
)


def _result(**overrides) -> VCResearchResult:
    base = dict(
        investor_name="Acme Ventures",
        website=None,
        industry_focus=None,
        stage=None,
        application_link=None,
        email=None,
        linkedin_url=None,
        twitter_url=None,
        newsletter=None,
        has_contact_form=False,
        confidence=0.9,
        source_urls=["https://acme.vc"],
    )
    base.update(overrides)
    return VCResearchResult(**base)


def test_application_link_outranks_every_other_channel():
    worker = VCResearchWorker()
    result = _result(
        application_link="https://apply.acme.vc",
        email="partner@acme.vc",
        has_contact_form=True,
        linkedin_url="https://linkedin.com/company/acme",
        twitter_url="https://x.com/acme",
    )
    assert worker.route(result) == CHANNEL_APPLY


def test_any_usable_email_routes_to_email():
    # The sheet no longer carries partner names, so a firm-level address
    # is enough — the old "named partner required" rule is gone.
    worker = VCResearchWorker()
    assert worker.route(_result(email="info@acme.vc")) == CHANNEL_EMAIL


def test_contact_form_without_email_routes_to_contact_box():
    worker = VCResearchWorker()
    assert worker.route(_result(has_contact_form=True)) == CHANNEL_CONTACT_BOX


def test_linkedin_is_used_when_no_direct_channel_exists():
    worker = VCResearchWorker()
    result = _result(linkedin_url="https://linkedin.com/company/acme")
    assert worker.route(result) == CHANNEL_LINKEDIN


def test_twitter_is_the_last_outreach_channel():
    worker = VCResearchWorker()
    assert worker.route(_result(twitter_url="https://x.com/acme")) == CHANNEL_TWITTER


def test_partial_data_without_a_channel_routes_to_manual_review():
    worker = VCResearchWorker()
    result = _result(website="https://acme.vc", industry_focus="Climate")
    assert worker.route(result) == CHANNEL_MANUAL_REVIEW


def test_nothing_usable_routes_to_research_failed():
    worker = VCResearchWorker()
    assert worker.route(_result()) == CHANNEL_RESEARCH_FAILED

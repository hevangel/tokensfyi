"""Focused contracts for shared semantic presentation primitives."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.test import Client


@pytest.fixture
def client() -> Client:
    return Client()


@pytest.mark.django_db
def test_home_has_unique_title_primary_heading_skip_link_and_descriptive_links(
    client: Client,
) -> None:
    response = client.get("/")
    html = response.content.decode()

    assert response.status_code == 200
    assert html.count("<title>") == 1
    assert html.count("<h1") == 1
    assert 'href="#main-content"' in html
    assert "Browse published token-budget benchmarks" in html
    assert ">here<" not in html.lower()


@pytest.mark.django_db
def test_journey_uses_native_labeled_control_linked_error_and_table_instructions(
    client: Client,
) -> None:
    _create_public_record()
    response = client.get("/browse/")
    html = response.content.decode()

    assert response.status_code == 200
    assert '<label for="journey-query">' in html
    assert '<input id="journey-query"' in html
    assert '<table id="journey-example">' in html
    assert "may scroll horizontally" in html
    assert 'role="region"' in html
    assert 'aria-live="polite"' in html


def _create_public_record() -> None:
    """Seed one publicly eligible record so the browse table renders (idempotent)."""
    from datetime import UTC, date, datetime
    from decimal import Decimal

    from token_budget_platform.adapters.django.models import (
        BudgetScope,
        ControlledValue,
        MethodologyVersion,
        Organization,
        PublicationState,
        TokenBudgetRecord,
        VerificationStatus,
    )

    moment = datetime(2026, 1, 1, tzinfo=UTC)

    def value(kind, code, label):
        existing = ControlledValue.objects.filter(kind=kind, code=code).first()
        if existing:
            return existing
        return ControlledValue.objects.create(
            kind=kind,
            code=code,
            label=label,
            normalized_label=label.lower(),
            active_from=moment,
            vocabulary_version=1,
        )

    methodology = MethodologyVersion.objects.filter(is_active=True).first()
    if methodology is None:
        methodology = MethodologyVersion.objects.create(
            version=1, name="Test methodology", rules={}, is_active=True, activated_at=moment
        )
    organization = Organization.objects.filter(slug="accessibility-test-org").first()
    if organization is None:
        organization = Organization.objects.create(
            name="Accessibility Test Org",
            normalized_name="accessibility test org",
            slug="accessibility-test-org",
            industry="Technology",
            size_band=value("SIZE_BAND", "SMALL", "1-50"),
            region=value("REGION", "EU", "Europe"),
        )
    if TokenBudgetRecord.objects.filter(organization=organization).exists():
        return
    TokenBudgetRecord.objects.create(
        organization=organization,
        budget_scope=BudgetScope.TEAM,
        model_provider=value("MODEL_PROVIDER", "OPENAI", "OpenAI"),
        ai_product=value("AI_PRODUCT", "GPT4O", "GPT-4o"),
        workload_category=value("WORKLOAD_CATEGORY", "RESEARCH", "Research"),
        region=value("REGION", "EU", "Europe"),
        organization_search=organization.normalized_name,
        provider_search="openai",
        product_search="gpt-4o",
        token_budget=Decimal("500000"),
        reporting_start=date(2026, 1, 1),
        reporting_end=date(2026, 6, 30),
        source_date=date(2026, 7, 1),
        publication_state=PublicationState.APPROVED,
        verification_status=VerificationStatus.VERIFIED,
        methodology_version=methodology,
        published_at=moment,
    )


def test_styles_cover_focus_reflow_motion_forced_colors_and_text_spacing() -> None:
    css = Path("static/css/app.css").read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert "forced-colors: active" in css
    assert "text-spacing-safe" in css
    assert "overflow-x: auto" in css

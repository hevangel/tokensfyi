"""Focused persistence boundary tests."""

from __future__ import annotations

import uuid

import pytest

from token_budget_platform.adapters.django.models import uuid7


def test_uuid7_has_uuid7_version_and_rfc_variant() -> None:
    identifier = uuid7()

    assert isinstance(identifier, uuid.UUID)
    assert identifier.version == 7
    assert identifier.variant == uuid.RFC_4122


@pytest.mark.django_db
def test_public_repository_injects_eligibility_predicate() -> None:
    from token_budget_platform.adapters.postgresql.repositories import DjangoPublicRecordRepository

    query = DjangoPublicRecordRepository().eligible()

    assert "publication_state" in str(query.query)
    assert "suppressed_at" in str(query.query)
    assert "withdrawn_at" in str(query.query)

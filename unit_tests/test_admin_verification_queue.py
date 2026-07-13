from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.agencies import list_pending_verification_agencies


def _fake_agency(**overrides):
    defaults = dict(
        id="agency-1", name="Test Agency", slug="test-agency", logo_url=None, description=None,
        verification_status="under_review", verification_rejection_reason=None,
        gstin="07AAICS1234A1Z9", pan="AAICS1234A", tourism_license=None, address=None, postal_code=None,
        phone=None, email=None, city=None, state=None, specializations=None, destinations=None,
        avg_rating=0.0, review_count=0, total_trips=0,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_list_pending_verification_returns_under_review_agencies():
    agencies = [_fake_agency(id="agency-1"), _fake_agency(id="agency-2")]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(scalars=MagicMock(return_value=MagicMock(all=MagicMock(return_value=agencies)))))

    result = await list_pending_verification_agencies(db)

    assert [a.id for a in result] == ["agency-1", "agency-2"]
    assert result[0].verification == "under_review"
    assert result[0].gstin == "07AAICS1234A1Z9"  # admin-only view — full AgencySummary, not the public one

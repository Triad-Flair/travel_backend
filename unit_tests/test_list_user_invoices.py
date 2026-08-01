"""list_user_invoices — must only surface CAPTURED payments. A
PENDING/AUTHORIZED/FAILED payment was never actually charged, so it has no
business showing up as an "invoice" in a traveler's invoice list.
"""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.invoices import list_user_invoices


@pytest.mark.asyncio
async def test_list_user_invoices_query_filters_to_captured_status():
    db = AsyncMock()
    captured = {}

    async def fake_execute(stmt):
        captured["stmt"] = stmt
        result = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    db.execute = fake_execute

    items = await list_user_invoices(db, "user-1")

    assert items == []
    compiled = str(captured["stmt"].compile(compile_kwargs={"literal_binds": True}))
    assert "payments.status" in compiled
    assert "'CAPTURED'" in compiled
    assert "payments.\"userId\" = 'user-1'" in compiled or "user-1" in compiled

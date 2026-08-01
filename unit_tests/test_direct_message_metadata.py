"""Regression test: _direct_message_response used to hardcode metadata=None
even though SendDirectMessageRequest already accepted a metadata field —
nothing ever stored or returned it, so a "shared post" card (or anything
else needing structured DM payloads) could never render. DirectMessage now
has real messageType/metadata columns; this locks in that the response
builder actually surfaces them instead of dropping them again.
"""
from datetime import UTC, datetime
from types import SimpleNamespace

from app.services.chat import _direct_message_response, _message_type_to_db


def _fake_message(**overrides):
    defaults = dict(
        id="msg-1",
        conversation_id="conv-1",
        sender_id="user-1",
        message_type="shared_post",
        content="Shared a post",
        extra_data={"sharedPost": {"id": "post-1", "caption": "Spiti sunrise"}},
        created_at=datetime.now(UTC),
        flagged=False,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


def test_direct_message_response_surfaces_metadata_and_message_type():
    payload = _direct_message_response(_fake_message(), sender=None)

    assert payload.message_type == "shared_post"
    assert payload.metadata == {"sharedPost": {"id": "post-1", "caption": "Spiti sunrise"}}


def test_direct_message_response_defaults_when_message_type_missing():
    payload = _direct_message_response(_fake_message(message_type=None, extra_data=None), sender=None)

    assert payload.message_type == "text"
    assert payload.metadata is None


def test_group_message_type_allow_list_includes_shared_post():
    assert _message_type_to_db("shared_post") == "SHARED_POST"
    assert _message_type_to_db("garbage") == "TEXT"

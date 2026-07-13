import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.config import settings
from app.core.cache import CacheKeys, TTL_SHORT, get_cached, invalidate, set_cached
from app.exceptions import BadRequestError, ForbiddenError, NotFoundError
from app.models.chat import (
    ChatMessage,
    ChatModerationKeyword,
    DirectConversation,
    DirectConversationParticipant,
    DirectMessage,
)
from app.models.agency import Agency
from app.models.group import Group, GroupMember
from app.models.offer import Offer
from app.models.package import Package
from app.models.plan import Plan
from app.models.social import Notification
from app.models.user import User
from app.schemas.chat import (
    ChatMessageResponse,
    ConversationInboxItem,
    ConversationResponse,
    CreateModerationKeywordRequest,
    CreatePollRequest,
    DirectMessageResponse,
    LastMessageSummary,
    MessageAuditResponse,
    ModerationKeywordResponse,
    PollResponse,
    SendDirectMessageRequest,
    SendMessageRequest,
    UpdateModerationKeywordRequest,
)
from app.schemas.common import UserSummary
from app.services.chat_moderation import ModerationResult, moderate
from app.websockets.socketio_server import emit_to_conversation, emit_to_group, emit_to_user

ACTIVE_CHAT_STATUSES = {"APPROVED", "COMMITTED"}
ACTIVE_DIRECT_CONTEXT_STATUSES = {"INTERESTED", "APPROVED", "COMMITTED"}

# Fire one compliance-reminder Notification (reusing the existing
# notification system — see app/services/social.py for the same pattern)
# the moment a sender's lifetime flagged-message count hits this number.
# Exactly-equal (not >=) so it fires once, not on every message after.
FLAGGED_MESSAGE_NOTIFICATION_THRESHOLD = 3


async def _get_active_moderation_keywords(db: AsyncSession) -> list[str]:
    cache_key = CacheKeys.chat_moderation_keywords()
    cached = await get_cached(cache_key)
    if cached is not None:
        return cached
    result = await db.execute(
        select(ChatModerationKeyword.keyword).where(ChatModerationKeyword.is_active == True)
    )
    keywords = [row[0] for row in result.all()]
    await set_cached(cache_key, keywords, TTL_SHORT)
    return keywords


async def _moderate_message(db: AsyncSession, content: str | None) -> ModerationResult:
    keywords = await _get_active_moderation_keywords(db)
    return moderate(content, keywords)


async def _notify_if_repeated_leakage_attempts(db: AsyncSession, user_id: str) -> None:
    group_count = await db.scalar(
        select(func.count(ChatMessage.id)).where(ChatMessage.sender_id == user_id, ChatMessage.flagged == True)
    ) or 0
    direct_count = await db.scalar(
        select(func.count(DirectMessage.id)).where(DirectMessage.sender_id == user_id, DirectMessage.flagged == True)
    ) or 0
    if int(group_count) + int(direct_count) != FLAGGED_MESSAGE_NOTIFICATION_THRESHOLD:
        return
    db.add(
        Notification(
            id=str(uuid.uuid4()),
            user_id=user_id,
            type="compliance_reminder",
            title="Keep communication on-platform",
            body=(
                f"For your security, keep all communication, payments, and bookings on {settings.app_name}. "
                "We've hidden some contact details you tried to share — this keeps you covered by our "
                "trust and safety guarantees."
            ),
        )
    )
    await db.flush()


def _to_iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _user_summary(user: User | None) -> UserSummary | None:
    if not user:
        return None
    return UserSummary(
        id=user.id,
        full_name=user.display_name or user.username or "",
        username=user.username,
        avatar_url=user.avatar_url,
        verification=user.verification_tier,
        gender=user.gender,
        city=user.location,
        avg_rating=user.avg_rating,
        completed_trips=user.completed_trips,
    )


def _message_type_to_api(raw: str) -> str:
    return raw.lower()


def _message_type_to_db(raw: str) -> str:
    mapped = raw.upper()
    if mapped == "DOCUMENT":
        return "TEXT"
    if mapped not in {"TEXT", "SYSTEM", "POLL", "IMAGE", "OFFER"}:
        return "TEXT"
    return mapped


def _parse_metadata(raw: object) -> dict | None:
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw if isinstance(raw, str) else str(raw))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _chat_message_response(message: ChatMessage, sender: User | None) -> ChatMessageResponse:
    return ChatMessageResponse(
        id=message.id,
        group_id=message.group_id,
        sender_id=message.sender_id,
        sender=_user_summary(sender),
        message_type=_message_type_to_api(message.message_type),
        content=message.content,
        metadata=_parse_metadata(message.extra_data),
        created_at=message.created_at.isoformat(),
        flagged=bool(message.flagged),
    )


def _direct_message_response(message: DirectMessage, sender: User | None) -> DirectMessageResponse:
    return DirectMessageResponse(
        id=message.id,
        conversation_id=message.conversation_id,
        sender_id=message.sender_id,
        sender=_user_summary(sender),
        content=message.content,
        metadata=None,
        created_at=message.created_at.isoformat(),
        flagged=bool(message.flagged),
    )


async def _assert_group_chat_access(db: AsyncSession, group_id: str, user_id: str):
    member = await db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.user_id == user_id,
            GroupMember.status.in_(ACTIVE_CHAT_STATUSES),
        )
    )
    if member:
        return

    group = await db.scalar(select(Group).where(Group.id == group_id))
    if not group:
        raise NotFoundError("Group")

    agency = await db.scalar(select(Agency).where(Agency.owner_id == user_id))
    if not agency:
        raise ForbiddenError("Only approved travelers or active bidding agencies can access group chat")

    if group.plan_id:
        plan = await db.scalar(select(Plan).where(Plan.id == group.plan_id))
        if plan and plan.status in {"CONFIRMING", "CONFIRMED"}:
            accepted_offer = await db.scalar(
                select(Offer.id).where(
                    Offer.plan_id == group.plan_id,
                    Offer.agency_id == agency.id,
                    Offer.status == "ACCEPTED",
                )
            )
            if accepted_offer:
                return

    if group.package_id:
        package = await db.scalar(select(Package).where(Package.id == group.package_id))
        if package and package.status in {"CONFIRMING", "CONFIRMED"} and package.agency_id == agency.id:
            return

    raise ForbiddenError("Only approved travelers or active bidding agencies can access group chat")


async def _assert_direct_user_eligibility(db: AsyncSession, user_id: str) -> Agency | None:
    user = await db.get(User, user_id)
    if not user:
        raise NotFoundError("User")

    agency = await db.scalar(select(Agency).where(Agency.owner_id == user_id))
    has_membership = await db.scalar(
        select(GroupMember.id).where(
            GroupMember.user_id == user_id,
            GroupMember.status.in_(ACTIVE_DIRECT_CONTEXT_STATUSES),
        )
    )
    has_created_plan = await db.scalar(
        select(Plan.id).where(Plan.creator_id == user_id).limit(1)
    )
    if not agency and not has_membership and not has_created_plan:
        raise ForbiddenError("Direct messages unlock after joining a trip or operating an agency with live offers")
    return agency


async def _assert_direct_conversation_eligibility(db: AsyncSession, left_user_id: str, right_user_id: str):
    left_agency = await _assert_direct_user_eligibility(db, left_user_id)
    right_agency = await _assert_direct_user_eligibility(db, right_user_id)
    left_is_agency = left_agency is not None
    right_is_agency = right_agency is not None

    if left_is_agency and right_is_agency:
        raise ForbiddenError("Direct messaging between two agencies is not supported")

    if left_is_agency or right_is_agency:
        agency_owner_id = left_user_id if left_is_agency else right_user_id
        traveler_user_id = right_user_id if left_is_agency else left_user_id

        related_offer = await db.scalar(
            select(Offer.id)
            .join(Plan, Plan.id == Offer.plan_id)
            .join(Agency, Agency.id == Offer.agency_id)
            .where(
                Plan.creator_id == traveler_user_id,
                Agency.owner_id == agency_owner_id,
            )
            .limit(1)
        )
        if related_offer:
            return

        package_context = await db.scalar(
            select(GroupMember.id)
            .join(Group, Group.id == GroupMember.group_id)
            .join(Package, Package.id == Group.package_id)
            .join(Agency, Agency.id == Package.agency_id)
            .where(
                GroupMember.user_id == traveler_user_id,
                GroupMember.status.in_(ACTIVE_DIRECT_CONTEXT_STATUSES),
                Agency.owner_id == agency_owner_id,
            )
            .limit(1)
        )
        if package_context:
            return

        raise ForbiddenError(
            "Agency and traveler direct chat starts only after an offer exists or traveler joins an agency package"
        )

    left_member = aliased(GroupMember)
    right_member = aliased(GroupMember)
    shared_trip = await db.scalar(
        select(left_member.id)
        .join(
            right_member,
            and_(
                left_member.group_id == right_member.group_id,
                right_member.user_id == right_user_id,
                right_member.status.in_(ACTIVE_CHAT_STATUSES),
            ),
        )
        .where(
            left_member.user_id == left_user_id,
            left_member.status.in_(ACTIVE_CHAT_STATUSES),
        )
        .limit(1)
    )
    if shared_trip:
        return

    raise ForbiddenError("Traveler direct chat unlocks only after both users share an approved or committed trip")


async def _assert_conversation_participant(
    db: AsyncSession,
    conversation_id: str,
    user_id: str,
) -> DirectConversationParticipant:
    participant = await db.scalar(
        select(DirectConversationParticipant).where(
            DirectConversationParticipant.conversation_id == conversation_id,
            DirectConversationParticipant.user_id == user_id,
        )
    )
    if not participant:
        raise ForbiddenError("You cannot access this conversation")
    return participant


async def get_group_messages(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    cursor: str | None,
    limit: int,
) -> list[ChatMessageResponse]:
    await _assert_group_chat_access(db, group_id, user_id)

    query = (
        select(ChatMessage)
        .where(ChatMessage.group_id == group_id)
        .order_by(ChatMessage.created_at.desc())
        .limit(limit)
    )

    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
            query = query.where(ChatMessage.created_at < cursor_dt)
        except Exception:
            pass

    rows = await db.execute(query)
    messages = list(reversed(rows.scalars().all()))

    sender_ids = [msg.sender_id for msg in messages if msg.sender_id]
    users: dict[str, User] = {}
    if sender_ids:
        user_rows = await db.execute(select(User).where(User.id.in_(sender_ids)))
        users = {user.id: user for user in user_rows.scalars().all()}

    return [_chat_message_response(msg, users.get(msg.sender_id or "")) for msg in messages]


async def send_group_message(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    req: SendMessageRequest,
) -> ChatMessageResponse:
    await _assert_group_chat_access(db, group_id, user_id)

    moderation = await _moderate_message(db, req.content)

    message = ChatMessage(
        id=str(uuid.uuid4()),
        group_id=group_id,
        sender_id=user_id,
        message_type=_message_type_to_db(req.message_type),
        content=moderation.redacted_text if moderation.flagged else req.content,
        extra_data=req.metadata if req.metadata else None,
        original_content=req.content if moderation.flagged else None,
        flagged=moderation.flagged,
        flagged_categories=moderation.categories if moderation.flagged else None,
    )
    db.add(message)
    await db.flush()

    if moderation.flagged:
        await _notify_if_repeated_leakage_attempts(db, user_id)

    sender = await db.get(User, user_id)
    payload = _chat_message_response(message, sender)
    await emit_to_group(group_id, "chat:message_created", payload.model_dump(by_alias=True))
    return payload


async def create_poll(
    db: AsyncSession,
    group_id: str,
    user_id: str,
    req: CreatePollRequest,
) -> PollResponse:
    await _assert_group_chat_access(db, group_id, user_id)

    options = [
        {
            "id": str(uuid.uuid4()),
            "label": option,
            "votes": [],
        }
        for option in req.options
    ]

    message = ChatMessage(
        id=str(uuid.uuid4()),
        group_id=group_id,
        sender_id=user_id,
        message_type="POLL",
        content=req.question,
        extra_data={"question": req.question, "options": options},
    )
    db.add(message)
    await db.flush()

    sender = await db.get(User, user_id)
    emitted = _chat_message_response(message, sender)
    await emit_to_group(group_id, "chat:message_created", emitted.model_dump(by_alias=True))

    return PollResponse(
        id=message.id,
        question=req.question,
        options=req.options,
        vote_counts=[0] * len(req.options),
        user_vote=None,
        is_closed=False,
        closes_at=req.closes_at,
        total_votes=0,
    )


async def vote_on_poll(
    db: AsyncSession,
    message_id: str,
    user_id: str,
    option_id: str,
) -> PollResponse:
    message = await db.get(ChatMessage, message_id)
    if not message:
        raise NotFoundError("Chat message")
    await _assert_group_chat_access(db, message.group_id, user_id)

    if message.message_type != "POLL":
        raise BadRequestError("Only poll messages can be voted on")

    metadata = _parse_metadata(message.extra_data)
    question = (metadata or {}).get("question") if isinstance(metadata, dict) else None
    raw_options = (metadata or {}).get("options") if isinstance(metadata, dict) else None
    if not isinstance(raw_options, list) or not raw_options:
        raise BadRequestError("Invalid poll options")

    normalized_options: list[dict] = []
    option_index: int | None = None
    for idx, raw_option in enumerate(raw_options):
        if not isinstance(raw_option, dict):
            continue
        option_key = str(raw_option.get("id") or f"option_{idx + 1}")
        label = str(raw_option.get("label") or "").strip()
        votes = raw_option.get("votes") if isinstance(raw_option.get("votes"), list) else []
        vote_ids = [str(v) for v in votes if isinstance(v, str)]
        normalized_options.append(
            {
                "id": option_key,
                "label": label,
                "votes": vote_ids,
            }
        )
        if option_key == option_id:
            option_index = len(normalized_options) - 1

    if option_index is None and option_id.isdigit():
        idx = int(option_id)
        if 0 <= idx < len(normalized_options):
            option_index = idx

    if option_index is None:
        raise BadRequestError("Selected poll option does not exist")

    updated_options: list[dict] = []
    for idx, option in enumerate(normalized_options):
        current_votes = [vote for vote in option["votes"] if vote != user_id]
        if idx == option_index:
            current_votes.append(user_id)
        updated_options.append(
            {
                "id": option["id"],
                "label": option["label"],
                "votes": current_votes,
            }
        )

    message.extra_data = {
        "question": question or message.content or "",
        "options": updated_options,
    }
    await db.flush()

    sender = await db.get(User, message.sender_id) if message.sender_id else None
    payload = _chat_message_response(message, sender)
    await emit_to_group(message.group_id, "chat:message_updated", payload.model_dump(by_alias=True))

    vote_counts = [len(option["votes"]) for option in updated_options]
    total_votes = len({vote for option in updated_options for vote in option["votes"]})
    return PollResponse(
        id=message.id,
        question=(question or message.content or "").strip(),
        options=[option["label"] or option["id"] for option in updated_options],
        vote_counts=vote_counts,
        user_vote=option_index,
        is_closed=False,
        closes_at=None,
        total_votes=total_votes,
    )


async def _conversation_payload(
    db: AsyncSession,
    conversation: DirectConversation,
    user_id: str,
) -> ConversationResponse:
    participants_rows = await db.execute(
        select(DirectConversationParticipant)
        .where(DirectConversationParticipant.conversation_id == conversation.id)
    )
    participants = participants_rows.scalars().all()

    me = next((p for p in participants if p.user_id == user_id), None)
    counterpart_participant = next((p for p in participants if p.user_id != user_id), None)

    counterpart_user = None
    if counterpart_participant:
        counterpart_user = await db.get(User, counterpart_participant.user_id)

    last_message = await db.scalar(
        select(DirectMessage)
        .where(DirectMessage.conversation_id == conversation.id)
        .order_by(DirectMessage.created_at.desc())
    )

    last_message_payload = None
    if last_message:
        sender = await db.get(User, last_message.sender_id)
        last_message_payload = _direct_message_response(last_message, sender)

    unread_query = select(func.count(DirectMessage.id)).where(
        DirectMessage.conversation_id == conversation.id,
        DirectMessage.sender_id != user_id,
    )
    if me and me.last_read_at:
        unread_query = unread_query.where(DirectMessage.created_at > me.last_read_at)
    unread_count = await db.scalar(unread_query) or 0

    return ConversationResponse(
        id=conversation.id,
        created_at=conversation.created_at.isoformat(),
        updated_at=conversation.updated_at.isoformat() if conversation.updated_at else conversation.created_at.isoformat(),
        counterpart=_user_summary(counterpart_user),
        unread_count=int(unread_count),
        last_read_at=_to_iso(me.last_read_at) if me else None,
        last_message=last_message_payload,
    )


async def create_or_get_conversation(
    db: AsyncSession,
    user_id: str,
    other_user_id: str,
) -> ConversationResponse:
    if user_id == other_user_id:
        raise BadRequestError("Cannot create conversation with yourself")

    other = await db.get(User, other_user_id)
    if not other:
        raise NotFoundError("User")

    key = ":".join(sorted([user_id, other_user_id]))
    conversation = await db.scalar(select(DirectConversation).where(DirectConversation.key == key))

    if not conversation:
        await _assert_direct_conversation_eligibility(db, user_id, other_user_id)

        conversation = DirectConversation(id=str(uuid.uuid4()), key=key)
        db.add(conversation)
        await db.flush()

        db.add(
            DirectConversationParticipant(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                user_id=user_id,
                last_read_at=datetime.now(UTC),
            )
        )
        db.add(
            DirectConversationParticipant(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                user_id=other_user_id,
                last_read_at=datetime.now(UTC),
            )
        )
        await db.flush()

    return await _conversation_payload(db, conversation, user_id)


async def get_conversations(
    db: AsyncSession,
    user_id: str,
    page: int,
    page_size: int,
) -> list[ConversationResponse]:
    # 1. My participant rows — JOIN to DirectConversation so we can ORDER BY the
    # conversation's updatedAt (last message time), not the participant's createdAt
    # (join time). Without this, new messages don't float conversations to the top.
    my_part_rows = await db.execute(
        select(DirectConversationParticipant)
        .join(
            DirectConversation,
            DirectConversation.id == DirectConversationParticipant.conversation_id,
        )
        .where(DirectConversationParticipant.user_id == user_id)
        .order_by(
            DirectConversation.updated_at.desc().nullslast(),
            DirectConversation.created_at.desc(),
        )
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    my_participants = my_part_rows.scalars().all()
    if not my_participants:
        return []

    conv_ids = [p.conversation_id for p in my_participants]
    my_part_by_conv = {p.conversation_id: p for p in my_participants}

    # 2. Conversations
    conv_rows = await db.execute(
        select(DirectConversation).where(DirectConversation.id.in_(conv_ids))
    )
    conversations = {c.id: c for c in conv_rows.scalars().all()}

    # 3. All participants for these conversations (to find counterparts)
    all_part_rows = await db.execute(
        select(DirectConversationParticipant)
        .where(DirectConversationParticipant.conversation_id.in_(conv_ids))
    )
    counterpart_by_conv: dict[str, DirectConversationParticipant] = {}
    for p in all_part_rows.scalars().all():
        if p.user_id != user_id:
            counterpart_by_conv[p.conversation_id] = p

    # 4. Counterpart users (batch)
    counterpart_user_ids = list({p.user_id for p in counterpart_by_conv.values()})
    counterpart_users: dict[str, User] = {}
    if counterpart_user_ids:
        cu_rows = await db.execute(select(User).where(User.id.in_(counterpart_user_ids)))
        counterpart_users = {u.id: u for u in cu_rows.scalars().all()}

    # 5. Last message per conversation — DISTINCT ON keeps only the newest row per conv
    last_msg_rows = await db.execute(
        select(DirectMessage)
        .where(DirectMessage.conversation_id.in_(conv_ids))
        .order_by(DirectMessage.conversation_id, DirectMessage.created_at.desc())
        .distinct(DirectMessage.conversation_id)
    )
    last_msg_by_conv: dict[str, DirectMessage] = {
        m.conversation_id: m for m in last_msg_rows.scalars().all()
    }

    # 6. Unread counts — single JOIN query respecting each conversation's last_read_at.
    # Use explicit .label("conv_id") so the result row key is "conv_id" not the DB
    # column name "conversationId" (SQLAlchemy exports the DB name, not the attr name).
    unread_rows = await db.execute(
        select(
            DirectMessage.conversation_id.label("conv_id"),
            func.count(DirectMessage.id).label("cnt"),
        )
        .join(
            DirectConversationParticipant,
            and_(
                DirectConversationParticipant.conversation_id == DirectMessage.conversation_id,
                DirectConversationParticipant.user_id == user_id,
                DirectMessage.sender_id != user_id,
                or_(
                    DirectConversationParticipant.last_read_at.is_(None),
                    DirectMessage.created_at > DirectConversationParticipant.last_read_at,
                ),
            ),
        )
        .where(DirectMessage.conversation_id.in_(conv_ids))
        .group_by(DirectMessage.conversation_id)
    )
    unread_by_conv: dict[str, int] = {row.conv_id: row.cnt for row in unread_rows}

    # 6b. Last message senders (batch) — needed for ConversationResponse.lastMessage.sender
    sender_ids = list({m.sender_id for m in last_msg_by_conv.values() if m.sender_id})
    msg_senders: dict[str, User] = {}
    if sender_ids:
        sr = await db.execute(select(User).where(User.id.in_(sender_ids)))
        msg_senders = {u.id: u for u in sr.scalars().all()}

    # Assemble full ConversationResponse (shape the frontend expects)
    items: list[ConversationResponse] = []
    for conv_id in conv_ids:
        conv = conversations.get(conv_id)
        if not conv:
            continue
        my_part = my_part_by_conv[conv_id]
        counterpart_part = counterpart_by_conv.get(conv_id)
        counterpart_user = counterpart_users.get(counterpart_part.user_id) if counterpart_part else None
        last_msg = last_msg_by_conv.get(conv_id)
        last_msg_payload = (
            _direct_message_response(last_msg, msg_senders.get(last_msg.sender_id or ""))
            if last_msg else None
        )
        items.append(
            ConversationResponse(
                id=conv.id,
                created_at=conv.created_at.isoformat(),
                updated_at=(conv.updated_at or conv.created_at).isoformat(),
                counterpart=_user_summary(counterpart_user),
                unread_count=int(unread_by_conv.get(conv_id, 0)),
                last_read_at=_to_iso(my_part.last_read_at),
                last_message=last_msg_payload,
            )
        )

    items.sort(key=lambda item: item.updated_at, reverse=True)
    return items


async def get_direct_messages(
    db: AsyncSession,
    conv_id: str,
    user_id: str,
    cursor: str | None,
    limit: int,
) -> list[DirectMessageResponse]:
    await _assert_conversation_participant(db, conv_id, user_id)

    query = (
        select(DirectMessage)
        .where(DirectMessage.conversation_id == conv_id)
        .order_by(DirectMessage.created_at.desc())
        .limit(limit)
    )
    if cursor:
        try:
            cursor_dt = datetime.fromisoformat(cursor)
            query = query.where(DirectMessage.created_at < cursor_dt)
        except Exception:
            pass

    rows = await db.execute(query)
    messages = list(reversed(rows.scalars().all()))

    sender_ids = [msg.sender_id for msg in messages]
    user_rows = await db.execute(select(User).where(User.id.in_(sender_ids)))
    users = {user.id: user for user in user_rows.scalars().all()}

    return [_direct_message_response(msg, users.get(msg.sender_id)) for msg in messages]


async def send_direct_message(
    db: AsyncSession,
    conv_id: str,
    user_id: str,
    req: SendDirectMessageRequest,
) -> DirectMessageResponse:
    await _assert_conversation_participant(db, conv_id, user_id)

    conversation = await db.get(DirectConversation, conv_id)
    if not conversation:
        raise NotFoundError("Conversation")

    moderation = await _moderate_message(db, req.content)

    message = DirectMessage(
        id=str(uuid.uuid4()),
        conversation_id=conv_id,
        sender_id=user_id,
        content=moderation.redacted_text if moderation.flagged else req.content,
        original_content=req.content if moderation.flagged else None,
        flagged=moderation.flagged,
        flagged_categories=moderation.categories if moderation.flagged else None,
    )
    db.add(message)

    conversation.updated_at = datetime.now(UTC)
    await db.flush()

    if moderation.flagged:
        await _notify_if_repeated_leakage_attempts(db, user_id)

    sender = await db.get(User, user_id)
    payload = _direct_message_response(message, sender)

    other_participant = await db.scalar(
        select(DirectConversationParticipant).where(
            DirectConversationParticipant.conversation_id == conv_id,
            DirectConversationParticipant.user_id != user_id,
        )
    )
    if other_participant:
        await emit_to_user(
            other_participant.user_id,
            "direct:message_created",
            payload.model_dump(by_alias=True),
        )
        # Fire push/WhatsApp notification off the hot path — non-blocking.
        # Uses message.content (redacted when flagged), never req.content —
        # otherwise a redacted phone/email would leak straight back out
        # through the WhatsApp preview.
        from app.workers.tasks import notify_direct_message
        notify_direct_message.delay(
            recipient_id=other_participant.user_id,
            sender_name=sender.display_name or sender.username if sender else "Someone",
            content_preview=(message.content or "")[:60],
        )
    await emit_to_conversation(
        conv_id,
        "direct:message_created",
        payload.model_dump(by_alias=True),
    )
    return payload


async def mark_conversation_read(db: AsyncSession, conv_id: str, user_id: str) -> dict:
    participant = await _assert_conversation_participant(db, conv_id, user_id)
    participant.last_read_at = datetime.now(UTC)
    await db.flush()
    return {"ok": True}


# ── Moderation audit + keyword management (admin only, see app/api/v1/chat.py) ──

async def get_message_audit(db: AsyncSession, message_id: str) -> MessageAuditResponse:
    """Only reachable via an admin-gated route. original_content is never
    exposed by the normal chat endpoints."""
    message = await db.get(ChatMessage, message_id)
    if not message:
        message = await db.get(DirectMessage, message_id)
    if not message:
        raise NotFoundError("Message")

    return MessageAuditResponse(
        id=message.id,
        sender_id=message.sender_id,
        flagged=bool(message.flagged),
        categories=message.flagged_categories or [],
        redacted_content=message.content,
        original_content=message.original_content,
        created_at=message.created_at.isoformat(),
    )


async def list_moderation_keywords(db: AsyncSession) -> list[ModerationKeywordResponse]:
    result = await db.execute(select(ChatModerationKeyword).order_by(ChatModerationKeyword.keyword.asc()))
    return [
        ModerationKeywordResponse(
            id=k.id, keyword=k.keyword, is_active=k.is_active, created_at=k.created_at.isoformat()
        )
        for k in result.scalars().all()
    ]


async def create_moderation_keyword(
    db: AsyncSession, req: CreateModerationKeywordRequest
) -> ModerationKeywordResponse:
    cleaned = req.keyword.strip().lower()
    existing = await db.scalar(select(ChatModerationKeyword).where(ChatModerationKeyword.keyword == cleaned))
    if existing:
        raise BadRequestError("This keyword is already in the list")

    keyword = ChatModerationKeyword(id=str(uuid.uuid4()), keyword=cleaned, is_active=True)
    db.add(keyword)
    await db.flush()
    await invalidate(CacheKeys.chat_moderation_keywords())
    return ModerationKeywordResponse(
        id=keyword.id, keyword=keyword.keyword, is_active=keyword.is_active,
        created_at=keyword.created_at.isoformat(),
    )


async def update_moderation_keyword(
    db: AsyncSession, keyword_id: str, req: UpdateModerationKeywordRequest
) -> ModerationKeywordResponse:
    keyword = await db.get(ChatModerationKeyword, keyword_id)
    if not keyword:
        raise NotFoundError("Keyword")

    keyword.is_active = req.is_active
    await db.flush()
    await invalidate(CacheKeys.chat_moderation_keywords())
    return ModerationKeywordResponse(
        id=keyword.id, keyword=keyword.keyword, is_active=keyword.is_active,
        created_at=keyword.created_at.isoformat(),
    )

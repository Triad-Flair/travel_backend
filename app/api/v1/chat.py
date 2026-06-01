from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import CurrentUser, get_current_user
from app.schemas.chat import (
    ChatMessageResponse,
    ConversationInboxItem,
    ConversationResponse,
    CreateConversationRequest,
    CreatePollRequest,
    DirectMessageResponse,
    PollResponse,
    SendDirectMessageRequest,
    SendMessageRequest,
    VotePollRequest,
)
from app.services import chat as chat_svc

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/health")
async def health():
    return {"ok": True, "module": "chat"}


@router.get("/groups/{group_id}/messages", response_model=list[ChatMessageResponse])
async def get_group_messages(
    group_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.get_group_messages(db, group_id, current_user.user_id, cursor, limit)


@router.post("/groups/{group_id}/messages", response_model=ChatMessageResponse, status_code=201)
async def send_group_message(
    group_id: str,
    req: SendMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.send_group_message(db, group_id, current_user.user_id, req)


@router.post("/groups/{group_id}/polls", response_model=PollResponse, status_code=201)
async def create_poll(
    group_id: str,
    req: CreatePollRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.create_poll(db, group_id, current_user.user_id, req)


@router.post("/messages/{message_id}/vote", response_model=PollResponse)
async def vote_on_poll(
    message_id: str,
    req: VotePollRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.vote_on_poll(db, message_id, current_user.user_id, req.option_id)


@router.post("/direct/conversations", response_model=ConversationResponse, status_code=201)
async def create_conversation(
    req: CreateConversationRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.create_or_get_conversation(db, current_user.user_id, req.target_user_id)


@router.get("/direct/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.get_conversations(db, current_user.user_id, page, page_size)


@router.get("/direct/conversations/{conv_id}/messages", response_model=list[DirectMessageResponse])
async def get_direct_messages(
    conv_id: str,
    cursor: str | None = Query(default=None),
    limit: int = Query(default=30, ge=1, le=100),
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.get_direct_messages(db, conv_id, current_user.user_id, cursor, limit)


@router.post("/direct/conversations/{conv_id}/messages", response_model=DirectMessageResponse, status_code=201)
async def send_direct_message(
    conv_id: str,
    req: SendDirectMessageRequest,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.send_direct_message(db, conv_id, current_user.user_id, req)


@router.post("/direct/conversations/{conv_id}/read")
async def mark_read(
    conv_id: str,
    current_user: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    return await chat_svc.mark_conversation_read(db, conv_id, current_user.user_id)


# Real-time handled by Socket.IO server mounted at /socket.io in main.py

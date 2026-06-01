from datetime import datetime

from pydantic import BaseModel, Field

from app.models.enums import LoyaltyAction


class LoyaltyBalanceResponse(BaseModel):
    balance: int
    lifetime_earned: int
    lifetime_redeemed: int


class LoyaltyLedgerEntry(BaseModel):
    id: str
    action: LoyaltyAction
    points: int
    balance_after: int
    description: str | None
    expires_at: datetime | None
    created_at: datetime


class AdminAdjustRequest(BaseModel):
    user_id: str
    points: int
    description: str


class ReferralLinkResponse(BaseModel):
    code: str
    link: str
    total_clicks: int
    total_signups: int
    total_conversions: int


class ReferralStatsResponse(BaseModel):
    total_referrals: int
    total_conversions: int
    total_bonus_earned_paise: int
    conversion_rate: float
    recent_referrals: list[dict]


class WalletBalanceResponse(BaseModel):
    balance_paise: int
    total_earned_paise: int
    total_spent_paise: int


class WalletTransactionResponse(BaseModel):
    id: str
    type: str
    amount_paise: int
    balance_after_paise: int
    description: str | None
    reference_id: str | None
    created_at: datetime

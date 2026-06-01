from fastapi import APIRouter

from app.api.v1 import (
    agencies,
    auth,
    chat,
    discover,
    groups,
    invoices,
    notifications,
    offers,
    packages,
    payments,
    plans,
    reviews,
    social,
    users,
)
from app.api.v1.loyalty import router as loyalty_router
from app.api.v1.loyalty import router_referrals, router_wallet

v1_router = APIRouter(prefix="/api/v1")

v1_router.include_router(auth.router)
v1_router.include_router(users.router)
v1_router.include_router(plans.router)
v1_router.include_router(packages.router)
v1_router.include_router(groups.router)
v1_router.include_router(offers.router)
v1_router.include_router(payments.router)
v1_router.include_router(chat.router)
v1_router.include_router(agencies.router)
v1_router.include_router(reviews.router)
v1_router.include_router(discover.router)
v1_router.include_router(notifications.router)
v1_router.include_router(social.router)
v1_router.include_router(invoices.router)
v1_router.include_router(loyalty_router)
v1_router.include_router(router_referrals)
v1_router.include_router(router_wallet)

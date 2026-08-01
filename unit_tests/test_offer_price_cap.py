"""SubmitOfferRequest/CounterOfferRequest must reject absurd per-person
prices — an agency fat-fingering a price (e.g. typing 876545678 instead of
8765) previously round-tripped straight through to a displayed
"₹87,65,45,678/person" with nothing catching it.
"""
import pytest
from pydantic import ValidationError

from app.schemas.offers import MAX_OFFER_PRICE, CounterOfferRequest, SubmitOfferRequest


def test_submit_offer_rejects_price_above_cap():
    with pytest.raises(ValidationError):
        SubmitOfferRequest(plan_id="plan-1", price_per_person=MAX_OFFER_PRICE + 1)


def test_submit_offer_accepts_price_at_cap():
    req = SubmitOfferRequest(plan_id="plan-1", price_per_person=MAX_OFFER_PRICE)
    assert req.price_per_person == MAX_OFFER_PRICE


def test_counter_offer_rejects_price_above_cap():
    with pytest.raises(ValidationError):
        CounterOfferRequest(price=MAX_OFFER_PRICE + 1)


def test_counter_offer_accepts_reasonable_price():
    req = CounterOfferRequest(price=18000)
    assert req.price == 18000

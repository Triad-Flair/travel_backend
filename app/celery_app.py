from celery import Celery
from celery.schedules import crontab

from app.config import settings

celery_app = Celery(
    "tripsync",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Kolkata",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    beat_schedule={
        "check-expired-plans": {
            "task": "app.workers.tasks.check_expired_plans",
            "schedule": crontab(minute="*/15"),
        },
        "check-expired-offers": {
            "task": "app.workers.tasks.check_expired_offers",
            "schedule": crontab(minute="*/30"),
        },
        "expire-loyalty-points": {
            "task": "app.workers.tasks.expire_loyalty_points",
            "schedule": crontab(hour=2, minute=0),
        },
        "check-payment-windows": {
            "task": "app.workers.tasks.check_payment_windows",
            "schedule": crontab(minute="*/5"),
        },
        "check-package-expiry-warnings": {
            "task": "app.workers.tasks.check_package_expiry_warnings",
            "schedule": crontab(hour=9, minute=0),
        },
        "check-completed-trips": {
            "task": "app.workers.tasks.check_completed_trips",
            "schedule": crontab(minute="*/30"),
        },
        "reconcile-stuck-group-confirmations": {
            "task": "app.workers.tasks.reconcile_stuck_group_confirmations",
            "schedule": crontab(minute="*/15"),
        },
        # Package view counters live in Redis — no periodic DB flush needed
        # (views are read directly from Redis via GET pkg:views:{id}).
        # Add a flush task here if you later add a `view_count` DB column.
    },
)

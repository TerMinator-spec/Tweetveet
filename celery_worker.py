"""Celery worker entry point.

Usage:
    celery -A celery_worker.celery_app worker --loglevel=info
    celery -A celery_worker.celery_app beat --loglevel=info
"""

from app.scheduler.tasks import celery_app  # noqa: F401

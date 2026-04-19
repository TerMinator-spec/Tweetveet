# ================================================
# TweetVeet — AI Cricket Twitter Bot
# Multi-stage Dockerfile
# ================================================

FROM python:3.12-slim AS base

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# ----------------------------------------
# API Server Target
# ----------------------------------------
FROM base AS api

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]

# ----------------------------------------
# Celery Worker Target
# ----------------------------------------
FROM base AS worker

CMD ["celery", "-A", "celery_worker.celery_app", "worker", "--loglevel=info", "--concurrency=2"]

# ----------------------------------------
# Celery Beat Scheduler Target
# ----------------------------------------
FROM base AS beat

CMD ["celery", "-A", "celery_worker.celery_app", "beat", "--loglevel=info"]

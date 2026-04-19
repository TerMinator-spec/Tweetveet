"""FastAPI application entrypoint with lifespan management."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.database import engine, Base
from app.config import settings
from app.utils.logger import setup_logger

logger = setup_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan: create tables on startup, cleanup on shutdown."""
    logger.info("Starting TweetVeet Bot", extra={"version": "1.0.0"})

    # Create all tables (use Alembic in production)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables ready")

    yield

    # Cleanup
    await engine.dispose()
    logger.info("TweetVeet Bot shutting down")


app = FastAPI(
    title="TweetVeet — AI Cricket Twitter Bot",
    description=(
        "Automated system that collects real-time cricket news, "
        "generates engaging tweets using AI, and posts them to Twitter/X."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(router)


@app.get("/health")
async def health_check():
    """Quick health check endpoint."""
    return {"status": "ok", "service": "tweetveet"}

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, calls, dashboard, observability, profile, webhooks
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.services.seed import seed_data
from app.storage import store

settings = get_settings()
setup_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("startup", env=settings.app_env, ghost_enabled=settings.ghost_enabled)
    try:
        await store.initialize()
        if settings.is_dev:
            await seed_data(store)
            log.info("seed_data_loaded", storage=store.backend.__class__.__name__)
    except Exception as exc:
        if settings.ghost_enabled and not store.is_memory:
            log.warning("ghost_startup_failed_falling_back_to_memory", error=str(exc))
            await store.use_memory_fallback(str(exc))
            if settings.is_dev:
                await seed_data(store)
                log.info("seed_data_loaded", storage=store.backend.__class__.__name__)
        else:
            raise
    yield
    await store.close()
    log.info("shutdown")


app = FastAPI(
    title="Penny Customer Care API",
    description="Voice-driven financial literacy support and parent approval pipeline",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3100",
        "http://127.0.0.1:3100",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(profile.router)
app.include_router(dashboard.router)
app.include_router(calls.router)
app.include_router(webhooks.router)
app.include_router(observability.router)


@app.get("/")
async def root():
    return {
        "name": "Penny Customer Care API",
        "version": "0.2.0",
        "docs": "/docs",
    }

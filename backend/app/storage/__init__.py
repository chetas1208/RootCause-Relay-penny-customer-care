from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger

from .ghost_store import GhostStore
from .memory_store import MemoryStore

log = get_logger("storage")


class StoreProxy:
    def __init__(self, backend):
        self._backend = backend

    @property
    def backend(self):
        return self._backend

    @property
    def is_memory(self) -> bool:
        return isinstance(self._backend, MemoryStore)

    async def initialize(self) -> None:
        await self._backend.initialize()

    async def close(self) -> None:
        await self._backend.close()

    async def use_memory_fallback(self, reason: str | None = None) -> None:
        if self.is_memory:
            return
        await self._backend.close()
        self._backend = MemoryStore()
        await self._backend.initialize()
        log.warning("storage_fallback_to_memory", reason=reason or "unknown")

    def __getattr__(self, item):
        return getattr(self._backend, item)


def create_store():
    settings = get_settings()
    if settings.ghost_enabled:
        try:
            backend = GhostStore(settings.ghost_database_url)
        except Exception as exc:
            log.warning("ghost_store_create_failed", error=str(exc))
            backend = MemoryStore()
        return StoreProxy(backend)
    return StoreProxy(MemoryStore())


store = create_store()

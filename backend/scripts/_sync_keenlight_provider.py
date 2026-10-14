"""One-off: sync keenlight provider base URL from env and rebuild model cache."""
import asyncio
import os

from yuxi.models.providers.cache import model_cache
from yuxi.models.providers.service import ensure_builtin_model_providers_in_db, get_all_model_providers
from yuxi.storage.postgres.manager import pg_manager


async def main() -> None:
    async with pg_manager.get_async_session_context() as session:
        await ensure_builtin_model_providers_in_db(session)
        await session.commit()
        providers = await get_all_model_providers(session)
    model_cache.rebuild(providers)
    for info in model_cache.get_all_specs("chat"):
        if info.provider_id == "keenlight":
            print(info.spec, info.base_url)


if __name__ == "__main__":
    asyncio.run(main())

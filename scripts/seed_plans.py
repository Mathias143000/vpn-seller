from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db.session import create_engine, create_session_factory, init_models
from app.repositories.plans import PlansRepository
from app.services.plans import PlansService


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    await init_models(engine)
    async with session_factory() as session:
        async with session.begin():
            await PlansService(PlansRepository(session), settings).seed_defaults()
    await engine.dispose()
    print("Default plans seeded")


if __name__ == "__main__":
    asyncio.run(main())

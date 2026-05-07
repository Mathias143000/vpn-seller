from __future__ import annotations

from contextlib import asynccontextmanager


@asynccontextmanager
async def transactional(session):
    if session.in_transaction():
        async with session.begin_nested():
            yield
    else:
        async with session.begin():
            yield

from __future__ import annotations

import asyncio
import sys


def configure_runtime() -> None:
    if sys.platform != "win32":
        return
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return
    current_policy = asyncio.get_event_loop_policy()
    if isinstance(current_policy, policy_factory):
        return
    asyncio.set_event_loop_policy(policy_factory())

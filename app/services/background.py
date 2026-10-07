import asyncio
import logging
from collections.abc import Coroutine
from typing import Any

logger = logging.getLogger(__name__)

# Keep strong references so fire-and-forget tasks aren't garbage-collected mid-flight.
_tasks: set[asyncio.Task[Any]] = set()


def fire_and_forget(coro: Coroutine[Any, Any, Any]) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def drain(timeout: float = 10.0) -> None:
    """Wait for pending tasks on shutdown so CAPI/Sheets calls aren't lost on redeploys."""
    if _tasks:
        await asyncio.wait(list(_tasks), timeout=timeout)

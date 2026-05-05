"""Request deduplication guard for in-flight component translations.

If the same component_id is already being processed, callers await the
existing future rather than starting a duplicate pipeline.
"""

import asyncio
from typing import Any


_in_flight: dict[str, asyncio.Future] = {}
_lock = asyncio.Lock()


async def get_or_register(component_id: str) -> tuple[bool, asyncio.Future]:
    """Check if a component is already in-flight.

    Returns:
        (is_new, future) — if is_new is True, the caller owns the future
        and must set its result/exception when done. If False, the caller
        should await the future for the existing result.
    """
    async with _lock:
        if component_id in _in_flight:
            return False, _in_flight[component_id]
        fut = asyncio.get_event_loop().create_future()
        _in_flight[component_id] = fut
        return True, fut


async def complete(component_id: str, result: Any) -> None:
    """Mark a component as done and deliver result to waiters."""
    async with _lock:
        fut = _in_flight.pop(component_id, None)
    if fut and not fut.done():
        fut.set_result(result)


async def fail(component_id: str, exc: Exception) -> None:
    """Mark a component as failed."""
    async with _lock:
        fut = _in_flight.pop(component_id, None)
    if fut and not fut.done():
        fut.set_exception(exc)

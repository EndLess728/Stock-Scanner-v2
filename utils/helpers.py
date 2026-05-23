"""Generic helpers."""
from __future__ import annotations

import asyncio
import functools
from typing import Any, Awaitable, Callable, Iterable, TypeVar

T = TypeVar("T")


def chunked(iterable: Iterable[T], n: int) -> Iterable[list[T]]:
    """Yield chunks of size `n` from `iterable`."""
    chunk: list[T] = []
    for item in iterable:
        chunk.append(item)
        if len(chunk) == n:
            yield chunk
            chunk = []
    if chunk:
        yield chunk


def safe_float(value: Any, default: float = 0.0) -> float:
    """Float-coerce best-effort."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def fire_and_forget(coro: Awaitable[Any]) -> asyncio.Task[Any]:
    """Schedule a coroutine without awaiting it. Logs any exception."""
    from utils.logger import log

    task = asyncio.create_task(coro)

    def _log_exc(t: asyncio.Task[Any]) -> None:
        if t.cancelled():
            return
        exc = t.exception()
        if exc is not None:
            log.exception(f"Background task failed: {exc!r}")

    task.add_done_callback(_log_exc)
    return task


def async_retry(
    attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: tuple[type[BaseException], ...] = (Exception,),
) -> Callable[..., Any]:
    """Simple exponential-backoff retry decorator for async callables."""
    from utils.logger import log

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            delay = base_delay
            last_exc: BaseException | None = None
            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    log.warning(
                        f"{func.__name__} attempt {attempt}/{attempts} failed: {exc!r}"
                    )
                    if attempt == attempts:
                        break
                    await asyncio.sleep(min(delay, max_delay))
                    delay *= 2
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


__all__ = ["chunked", "safe_float", "safe_int", "fire_and_forget", "async_retry"]

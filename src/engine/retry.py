"""Decorators e utilitários para retry com exponential backoff.

Uso básico:

    @async_retry()
    async def minha_funcao():
        ...

    @sync_retry(max_attempts=5, base_delay=2.0)
    def minha_funcao_sync():
        ...

    # Uso direto (sem decorator):
    resultado = await async_retry()(minha_funcao)(*args, **kwargs)
"""
import asyncio
import logging
import random
import time
from functools import wraps
from typing import Awaitable, Callable, Optional, TypeVar, cast

from src.config import settings

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., object])


def _exponential_backoff(attempt: int, base_delay: float, max_delay: float) -> float:
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    jitter = random.uniform(0, delay * 0.1)
    return delay + jitter


def sync_retry(
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    exceptions: tuple = (Exception,),
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> Callable[[F], F]:
    if max_attempts is None:
        max_attempts = settings.retry_max_attempts
    if base_delay is None:
        base_delay = settings.retry_base_delay
    if max_delay is None:
        max_delay = settings.retry_max_delay

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if should_retry is not None and not should_retry(e):
                        raise
                    last_exc = e
                    if attempt < max_attempts:
                        delay = _exponential_backoff(attempt, base_delay, max_delay)
                        logger.warning(
                            "%s falhou (tentativa %d/%d): %s. Retentando em %.2fs...",
                            func.__name__,
                            attempt,
                            max_attempts,
                            e,
                            delay,
                        )
                        time.sleep(delay)
            logger.error(
                "%s falhou após %d tentativas: %s",
                func.__name__,
                max_attempts,
                last_exc,
            )
            if last_exc is not None:
                raise last_exc

        return cast(F, wrapper)

    return decorator


def async_retry(
    max_attempts: Optional[int] = None,
    base_delay: Optional[float] = None,
    max_delay: Optional[float] = None,
    exceptions: tuple = (Exception,),
    should_retry: Optional[Callable[[Exception], bool]] = None,
) -> Callable[[F], F]:
    if max_attempts is None:
        max_attempts = settings.retry_max_attempts
    if base_delay is None:
        base_delay = settings.retry_base_delay
    if max_delay is None:
        max_delay = settings.retry_max_delay

    def decorator(func: F) -> F:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if should_retry is not None and not should_retry(e):
                        raise
                    last_exc = e
                    if attempt < max_attempts:
                        delay = _exponential_backoff(attempt, base_delay, max_delay)
                        logger.warning(
                            "%s falhou (tentativa %d/%d): %s. Retentando em %.2fs...",
                            func.__name__,
                            attempt,
                            max_attempts,
                            e,
                            delay,
                        )
                        await asyncio.sleep(delay)
            logger.error(
                "%s falhou após %d tentativas: %s",
                func.__name__,
                max_attempts,
                last_exc,
                exc_info=True,
            )
            if last_exc is not None:
                raise last_exc

        return cast(F, wrapper)

    return decorator

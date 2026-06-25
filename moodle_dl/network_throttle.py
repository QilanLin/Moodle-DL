# -*- coding: utf-8 -*-
import asyncio
import logging
import random
import threading
import time
from typing import Callable, Optional


class NetworkThrottle:
    """Shared rate limiter for user-facing Moodle network operations."""

    def __init__(
            self,
            base_delay: float = 2.0,
            jitter: float = 1.0,
            *,
            delay_first: bool = True,
            sleep: Callable[[float], None] = time.sleep,
            async_sleep: Callable[[float], object] = asyncio.sleep,
            monotonic: Callable[[], float] = time.monotonic,
            random_func: Optional[Callable[[], float]] = None,
        ):
        self.base_delay = base_delay
        self.jitter = jitter
        self.delay_first = delay_first
        self._sleep = sleep
        self._async_sleep = async_sleep
        self._monotonic = monotonic
        self._random = random_func or random.random
        self._lock = threading.Lock()
        self._next_available_at = 0.0
        self._has_reserved = False
        self._async_lock: Optional[asyncio.Lock] = None
        self._async_lock_loop = None

    def _sample_delay(self) -> float:
        return self.base_delay + (self.jitter * self._random())

    def _reserve_delay(self) -> float:
        with self._lock:
            now = self._monotonic()
            should_delay = self.delay_first or self._has_reserved
            delay = self._sample_delay() if should_delay else 0.0
            start_at = max(now, self._next_available_at) + delay
            self._next_available_at = start_at
            self._has_reserved = True
            return max(0.0, start_at - now)

    @staticmethod
    def _log_delay(delay: float, reason: str) -> None:
        logging.debug('等待 %.2f 秒后继续下一个网络请求：%s', delay, reason)

    def _get_async_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._async_lock is None or self._async_lock_loop is not loop:
            self._async_lock = asyncio.Lock()
            self._async_lock_loop = loop
        return self._async_lock

    def wait(self, reason: str = 'network request') -> None:
        delay = self._reserve_delay()
        if delay <= 0:
            return
        self._log_delay(delay, reason)
        self._sleep(delay)

    async def async_wait(self, reason: str = 'network request') -> None:
        async with self._get_async_lock():
            delay = self._reserve_delay()
            if delay <= 0:
                return
            self._log_delay(delay, reason)
            await self._async_sleep(delay)

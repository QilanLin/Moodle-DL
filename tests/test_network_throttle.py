import asyncio
from unittest.mock import AsyncMock, Mock

import pytest

from moodle_dl.network_throttle import NetworkThrottle


def test_network_throttle_waits_before_first_and_queues_following_requests():
    sleep = Mock()
    throttle = NetworkThrottle(
        sleep=sleep,
        monotonic=lambda: 100.0,
        random_func=lambda: 0.0,
    )

    throttle.wait('first')
    throttle.wait('second')

    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]


def test_network_throttle_can_skip_the_first_delay():
    sleep = Mock()
    throttle = NetworkThrottle(
        delay_first=False,
        sleep=sleep,
        monotonic=lambda: 100.0,
        random_func=lambda: 0.0,
    )

    throttle.wait('first')
    throttle.wait('second')

    assert [call.args[0] for call in sleep.call_args_list] == [1.0]


@pytest.mark.asyncio
async def test_network_throttle_async_wait_uses_async_sleep():
    async_sleep = AsyncMock()
    throttle = NetworkThrottle(
        async_sleep=async_sleep,
        monotonic=lambda: 100.0,
        random_func=lambda: 1.0,
    )

    await throttle.async_wait('async request')

    async_sleep.assert_awaited_once_with(2.0)


@pytest.mark.asyncio
async def test_network_throttle_async_wait_does_not_prebook_concurrent_callers():
    clock = {'now': 100.0}
    sleep_delays = []
    sleep_waiters = []

    async def async_sleep(delay):
        sleep_delays.append(delay)
        waiter = asyncio.get_running_loop().create_future()
        sleep_waiters.append((waiter, delay))
        await waiter
        clock['now'] += delay

    throttle = NetworkThrottle(
        async_sleep=async_sleep,
        monotonic=lambda: clock['now'],
        random_func=lambda: 0.0,
    )

    tasks = [asyncio.create_task(throttle.async_wait(f'request {index}')) for index in range(3)]
    await asyncio.sleep(0)

    assert sleep_delays == [1.0]

    for expected_count in (2, 3):
        waiter, _delay = sleep_waiters.pop(0)
        waiter.set_result(None)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert sleep_delays == [1.0] * expected_count

    waiter, _delay = sleep_waiters.pop(0)
    waiter.set_result(None)
    await asyncio.gather(*tasks)

    assert sleep_delays == [1.0, 1.0, 1.0]

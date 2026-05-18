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

    assert [call.args[0] for call in sleep.call_args_list] == [2.0, 4.0]


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

    assert [call.args[0] for call in sleep.call_args_list] == [2.0]


@pytest.mark.asyncio
async def test_network_throttle_async_wait_uses_async_sleep():
    async_sleep = AsyncMock()
    throttle = NetworkThrottle(
        async_sleep=async_sleep,
        monotonic=lambda: 100.0,
        random_func=lambda: 1.0,
    )

    await throttle.async_wait('async request')

    async_sleep.assert_awaited_once_with(5.0)

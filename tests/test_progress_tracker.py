import pathlib
import sys

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from moodle_dl.downloader.progress_tracker import ProgressTracker, SimpleProgressBar


class FakeTime:
    def __init__(self, start: float = 0.0):
        self.value = start

    def time(self) -> float:
        return self.value

    def advance(self, seconds: float):
        self.value += seconds


@pytest.fixture
def fake_time(monkeypatch):
    clock = FakeTime()
    monkeypatch.setattr("moodle_dl.downloader.progress_tracker.time.time", clock.time)
    return clock


def test_speed_and_eta_progress(fake_time):
    tracker = ProgressTracker()
    tracker.WARMUP_SECONDS = 0

    total_bytes = 1000
    total_files = 10

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=100,
        total_bytes=total_bytes,
        completed=1,
        failed=0,
        total=total_files,
    )

    assert tracker.current_speed == pytest.approx(100.0)
    assert tracker.ema_speed == pytest.approx(100.0)

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=300,
        total_bytes=total_bytes,
        completed=3,
        failed=0,
        total=total_files,
    )

    assert tracker.current_speed == pytest.approx(200.0)
    assert tracker.ema_speed == pytest.approx(130.0)

    fake_time.advance(9)
    tracker.update(
        downloaded_bytes=500,
        total_bytes=total_bytes,
        completed=5,
        failed=0,
        total=total_files,
    )

    eta_seconds = tracker.get_eta_seconds()
    assert eta_seconds is not None
    assert eta_seconds > 0
    assert tracker.format_eta(eta_seconds).endswith("秒")


def test_handles_non_increasing_download_resets_speed(fake_time):
    tracker = ProgressTracker()
    tracker.WARMUP_SECONDS = 0

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=400,
        total_bytes=1000,
        completed=4,
        failed=0,
        total=10,
    )

    assert tracker.current_speed == pytest.approx(400.0)
    assert tracker.ema_speed == pytest.approx(400.0)

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=300,
        total_bytes=1000,
        completed=4,
        failed=1,
        total=10,
    )

    assert tracker.current_speed == 0.0
    assert tracker.ema_speed == 0.0

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=500,
        total_bytes=1000,
        completed=6,
        failed=1,
        total=10,
    )

    assert tracker.current_speed == pytest.approx(200.0)
    assert tracker.ema_speed == pytest.approx(200.0)
    assert tracker.get_eta_seconds() is not None


def test_progress_and_summary_output(fake_time):
    tracker = ProgressTracker()
    tracker.WARMUP_SECONDS = 0

    fake_time.advance(1)
    tracker.update(
        downloaded_bytes=512,
        total_bytes=1024,
        completed=2,
        failed=1,
        total=4,
        skipped=1,
    )

    percentage = tracker.get_percentage()
    assert percentage == 50

    progress_line = tracker.get_progress_line()
    assert "📥" in progress_line
    assert "50%" in progress_line

    stats_line = tracker.get_statistics_line()
    assert "✅" in stats_line
    assert "❌" in stats_line
    assert "⊘" in stats_line

    summary = tracker.get_summary()
    assert "总文件数" in summary

    bar = SimpleProgressBar(width=10).get_progress_with_bar(percentage)
    assert bar.startswith("[")
    assert "50%" in bar
    assert "█" in bar

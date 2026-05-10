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

    full_status = tracker.get_full_status()
    assert "\n   " in full_status

    summary = tracker.get_summary()
    assert "总文件数" in summary

    bar = SimpleProgressBar(width=10).get_progress_with_bar(percentage)
    assert bar.startswith("[")
    assert "50%" in bar
    assert "█" in bar


def test_percentage_and_eta_edge_cases(fake_time):
    tracker = ProgressTracker()

    tracker.total_bytes = 0
    assert tracker.get_percentage() is None

    tracker.total_bytes = 100
    tracker.downloaded_bytes = 150
    assert tracker.get_percentage() is None

    tracker.downloaded_bytes = 100
    tracker.ema_speed = 0
    assert tracker._get_eta_by_speed() == 0.0

    tracker.downloaded_bytes = 50
    tracker.ema_speed = 0
    assert tracker._get_eta_by_speed() is None

    tracker.total_files = 10
    tracker.completed_files = 4
    tracker.failed_files = 0
    tracker.skipped_files = 0
    assert tracker._get_eta_by_files() is None

    tracker.completed_files = 5
    tracker.start_time = fake_time.time()
    assert tracker._get_eta_by_files() is None

    tracker.completed_files = 8
    tracker.failed_files = 1
    tracker.skipped_files = 1
    assert tracker._get_eta_by_files() == 0.0


def test_eta_formatting_smoothing_and_limits(fake_time):
    tracker = ProgressTracker()

    assert tracker.format_eta(None) == "计算中..."
    assert tracker.get_eta_seconds() is None

    fake_time.advance(11)
    assert tracker.format_eta(None) == ">1天"
    assert tracker.format_eta(59) == "59秒"
    assert tracker.format_eta(61) == "1分1秒"
    assert tracker.format_eta(3661) == "1小时1分"

    tracker.WARMUP_SECONDS = 0
    tracker._get_eta_by_speed = lambda: 1000
    tracker._get_eta_by_files = lambda: None
    tracker.last_eta = 100
    assert tracker.get_eta_seconds() == 550

    tracker.last_eta = None
    tracker.MAX_ETA_SECONDS = 100
    assert tracker.get_eta_seconds() is None

    tracker.MAX_ETA_SECONDS = 86400
    tracker._get_eta_by_speed = lambda: -5
    tracker.last_eta = None
    assert tracker.get_eta_seconds() == 0

    tracker._get_eta_by_speed = lambda: None
    assert tracker.get_eta_seconds() is None


def test_empty_statistics_full_status_and_unknown_progress_bar(fake_time):
    tracker = ProgressTracker()
    tracker.WARMUP_SECONDS = 0
    tracker.format_eta = lambda eta_seconds: ''

    assert tracker.get_statistics_line() == ''

    full_status = tracker.get_full_status()
    assert '\n' not in full_status
    assert ' NA%' in full_status

    bar = SimpleProgressBar(width=5)
    assert bar.render(None) == '[     ]'
    assert bar.get_progress_with_bar(None).endswith(' NA%')

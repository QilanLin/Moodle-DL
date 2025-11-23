#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
测试增强的进度追踪器

运行: python test_progress_tracker.py
"""

import time
from moodle_dl.downloader.progress_tracker import ProgressTracker, SimpleProgressBar


def test_basic_progress():
    """测试基本进度显示"""
    print("=" * 80)
    print("测试 1: 基本进度显示")
    print("=" * 80)
    
    tracker = ProgressTracker()
    
    # 模拟下载过程
    total_bytes = 100 * 1024 * 1024  # 100 MB
    total_files = 50
    
    for i in range(10):
        # 模拟进度更新
        downloaded_bytes = int(total_bytes * (i + 1) / 10)
        completed_files = int(total_files * (i + 1) / 10)
        failed_files = i // 5  # 模拟少量失败
        
        tracker.update(
            downloaded_bytes=downloaded_bytes,
            total_bytes=total_bytes,
            completed=completed_files,
            failed=failed_files,
            total=total_files,
            skipped=2
        )
        
        print(tracker.get_progress_line())
        stats = tracker.get_statistics_line()
        if stats:
            print(f"   {stats}")
        print()
        
        time.sleep(0.5)  # 模拟下载延迟
    
    # 显示总结
    print("\n" + tracker.get_summary())


def test_progress_bar():
    """测试进度条显示"""
    print("\n" + "=" * 80)
    print("测试 2: 进度条显示")
    print("=" * 80)
    
    progress_bar = SimpleProgressBar(width=40)
    
    for percentage in range(0, 101, 10):
        bar = progress_bar.get_progress_with_bar(percentage)
        print(f"{bar} - {percentage}% 完成")
        time.sleep(0.2)


def test_eta_calculation():
    """测试 ETA 计算"""
    print("\n" + "=" * 80)
    print("测试 3: ETA 计算")
    print("=" * 80)
    
    tracker = ProgressTracker()
    
    total_bytes = 500 * 1024 * 1024  # 500 MB
    total_files = 100
    
    print("模拟不同速度的下载：\n")
    
    # 快速下载（5 MB/s）
    print("场景 1: 快速下载（5 MB/s）")
    tracker.start_time = time.time()
    for i in range(1, 6):
        time.sleep(0.2)
        downloaded = int(total_bytes * i / 5)
        tracker.update(
            downloaded_bytes=downloaded,
            total_bytes=total_bytes,
            completed=i * 20,
            failed=0,
            total=total_files
        )
        
        eta = tracker.get_eta_seconds()
        eta_str = tracker.format_eta(eta)
        print(f"  进度: {tracker.get_percentage()}% | ETA: {eta_str}")
    
    # 慢速下载（1 MB/s）
    print("\n场景 2: 慢速下载（1 MB/s）")
    tracker2 = ProgressTracker()
    tracker2.start_time = time.time()
    time.sleep(1)
    tracker2.update(
        downloaded_bytes=1 * 1024 * 1024,  # 1 MB
        total_bytes=total_bytes,
        completed=1,
        failed=0,
        total=total_files
    )
    eta = tracker2.get_eta_seconds()
    eta_str = tracker2.format_eta(eta)
    print(f"  进度: {tracker2.get_percentage()}% | ETA: {eta_str}")


def test_statistics_display():
    """测试统计信息显示"""
    print("\n" + "=" * 80)
    print("测试 4: 详细统计显示")
    print("=" * 80)
    
    tracker = ProgressTracker()
    
    # 模拟各种场景
    scenarios = [
        {
            "name": "全部成功",
            "downloaded": 100 * 1024 * 1024,
            "total": 100 * 1024 * 1024,
            "completed": 50,
            "failed": 0,
            "skipped": 0,
            "total_files": 50
        },
        {
            "name": "部分失败",
            "downloaded": 80 * 1024 * 1024,
            "total": 100 * 1024 * 1024,
            "completed": 40,
            "failed": 5,
            "skipped": 0,
            "total_files": 50
        },
        {
            "name": "有跳过",
            "downloaded": 70 * 1024 * 1024,
            "total": 100 * 1024 * 1024,
            "completed": 35,
            "failed": 3,
            "skipped": 10,
            "total_files": 50
        }
    ]
    
    for scenario in scenarios:
        print(f"\n场景: {scenario['name']}")
        print("-" * 40)
        
        tracker.update(
            downloaded_bytes=scenario['downloaded'],
            total_bytes=scenario['total'],
            completed=scenario['completed'],
            failed=scenario['failed'],
            total=scenario['total_files'],
            skipped=scenario['skipped']
        )
        
        print(tracker.get_progress_line())
        stats = tracker.get_statistics_line()
        if stats:
            print(f"   {stats}")


def main():
    """运行所有测试"""
    print("\n" + "=" * 80)
    print("增强的进度追踪器 - 功能测试")
    print("=" * 80 + "\n")
    
    try:
        test_basic_progress()
        test_progress_bar()
        test_eta_calculation()
        test_statistics_display()
        
        print("\n" + "=" * 80)
        print("✅ 所有测试完成！")
        print("=" * 80)
        
    except KeyboardInterrupt:
        print("\n\n用户中断测试")
    except Exception as e:
        print(f"\n\n❌ 测试出错: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()


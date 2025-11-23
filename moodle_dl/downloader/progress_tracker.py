# -*- coding: utf-8 -*-
"""
增强的下载进度追踪器

提供详细的下载统计信息，包括：
- 速度和 ETA
- 成功/失败/跳过文件数
- 清晰的进度显示
"""

import time
from typing import Optional
from moodle_dl.utils import format_bytes, format_speed, calc_speed


class ProgressTracker:
    """
    增强的进度追踪器，用于显示详细的下载统计
    
    功能：
    - 实时速度计算
    - 预计剩余时间（ETA）
    - 详细的文件统计（成功、失败、跳过）
    - 友好的格式化输出
    """
    
    def __init__(self):
        """初始化进度追踪器"""
        self.start_time = time.time()
        self.last_update_time = time.time()
        self.last_bytes_downloaded = 0
        
        # 文件统计
        self.total_files = 0
        self.completed_files = 0
        self.failed_files = 0
        self.skipped_files = 0
        
        # 字节统计
        self.total_bytes = 0
        self.downloaded_bytes = 0
        
        # 速度统计
        self.current_speed = 0.0  # bytes/sec
        self.average_speed = 0.0  # bytes/sec
        
    def update(self, downloaded_bytes: int, total_bytes: int,
               completed: int, failed: int, total: int, skipped: int = 0):
        """
        更新进度统计
        
        Args:
            downloaded_bytes: 已下载字节数
            total_bytes: 总字节数
            completed: 已完成文件数
            failed: 失败文件数
            total: 总文件数
            skipped: 跳过文件数
        """
        current_time = time.time()
        
        # 更新文件统计
        self.total_files = total
        self.completed_files = completed
        self.failed_files = failed
        self.skipped_files = skipped
        
        # 更新字节统计
        self.total_bytes = total_bytes
        self.downloaded_bytes = downloaded_bytes
        
        # 计算速度
        time_diff = current_time - self.last_update_time
        if time_diff > 0:
            bytes_diff = downloaded_bytes - self.last_bytes_downloaded
            self.current_speed = calc_speed(self.last_update_time, current_time, bytes_diff)
            
        # 计算平均速度
        total_time = current_time - self.start_time
        if total_time > 0:
            self.average_speed = downloaded_bytes / total_time
            
        # 更新上次记录
        self.last_update_time = current_time
        self.last_bytes_downloaded = downloaded_bytes
    
    def get_percentage(self) -> Optional[int]:
        """
        获取下载百分比
        
        Returns:
            百分比（0-100），如果无法计算则返回 None
        """
        if self.total_bytes == 0:
            return None
        
        percentage = int(self.downloaded_bytes * 100 / self.total_bytes)
        
        # 处理异常值
        if percentage < 0 or percentage > 100:
            return None
            
        return percentage
    
    def get_eta_seconds(self) -> Optional[int]:
        """
        获取预计剩余时间（秒）
        
        Returns:
            预计剩余秒数，如果无法计算则返回 None
        """
        if self.average_speed <= 0:
            return None
            
        remaining_bytes = self.total_bytes - self.downloaded_bytes
        if remaining_bytes <= 0:
            return 0
            
        eta_seconds = int(remaining_bytes / self.average_speed)
        
        # 限制最大显示时间（避免显示过大的值）
        if eta_seconds > 86400:  # 超过 24 小时
            return None
            
        return eta_seconds
    
    def format_eta(self, eta_seconds: Optional[int]) -> str:
        """
        格式化 ETA 时间
        
        Args:
            eta_seconds: 剩余秒数
            
        Returns:
            格式化的时间字符串
        """
        if eta_seconds is None:
            return "未知"
        
        if eta_seconds < 60:
            return f"{eta_seconds}秒"
        elif eta_seconds < 3600:
            minutes = eta_seconds // 60
            seconds = eta_seconds % 60
            return f"{minutes}分{seconds}秒"
        else:
            hours = eta_seconds // 3600
            minutes = (eta_seconds % 3600) // 60
            return f"{hours}小时{minutes}分"
    
    def get_progress_line(self) -> str:
        """
        获取进度显示行
        
        Returns:
            格式化的进度字符串
        """
        # 百分比
        percentage = self.get_percentage()
        if percentage is None:
            percentage_str = " NA%"
        else:
            percentage_str = f"{percentage:3}%"
        
        # 字节信息
        bytes_info = (
            f"{format_bytes(self.downloaded_bytes):>5} / "
            f"{format_bytes(self.total_bytes):<5}"
        )
        
        # 文件信息
        processed_files = self.completed_files + self.failed_files
        files_info = f"{processed_files:>5} / {self.total_files:<5}"
        
        # 速度
        speed_info = format_speed(self.current_speed)
        
        # 基础进度行
        progress_line = (
            f"📥 总计: {percentage_str} {bytes_info} | "
            f"完成: {files_info} | "
            f"速度: {speed_info}"
        )
        
        return progress_line
    
    def get_statistics_line(self) -> str:
        """
        获取详细统计行
        
        Returns:
            格式化的统计字符串
        """
        stats_parts = []
        
        # 成功
        if self.completed_files > 0:
            stats_parts.append(f"✅ 成功: {self.completed_files}")
        
        # 失败
        if self.failed_files > 0:
            stats_parts.append(f"❌ 失败: {self.failed_files}")
        
        # 跳过
        if self.skipped_files > 0:
            stats_parts.append(f"⊘ 跳过: {self.skipped_files}")
        
        # ETA
        eta_seconds = self.get_eta_seconds()
        if eta_seconds is not None and eta_seconds > 0:
            eta_str = self.format_eta(eta_seconds)
            stats_parts.append(f"⏱ 剩余: {eta_str}")
        
        if not stats_parts:
            return ""
        
        return " | ".join(stats_parts)
    
    def get_full_status(self) -> str:
        """
        获取完整的状态信息（进度 + 统计）
        
        Returns:
            完整的格式化状态字符串
        """
        progress = self.get_progress_line()
        statistics = self.get_statistics_line()
        
        if statistics:
            return f"{progress}\n   {statistics}"
        else:
            return progress
    
    def get_summary(self) -> str:
        """
        获取下载完成后的总结信息
        
        Returns:
            总结字符串
        """
        total_time = time.time() - self.start_time
        
        summary_parts = [
            "=" * 80,
            "下载完成统计",
            "=" * 80,
            f"总文件数: {self.total_files}",
            f"  ✅ 成功: {self.completed_files}",
            f"  ❌ 失败: {self.failed_files}",
            f"  ⊘ 跳过: {self.skipped_files}",
            "",
            f"总数据量: {format_bytes(self.total_bytes)}",
            f"已下载: {format_bytes(self.downloaded_bytes)}",
            "",
            f"总耗时: {self.format_eta(int(total_time))}",
            f"平均速度: {format_speed(self.average_speed)}",
            "=" * 80,
        ]
        
        return "\n".join(summary_parts)


class SimpleProgressBar:
    """
    简单的进度条显示器（可选）
    
    用于在终端显示可视化的进度条
    """
    
    def __init__(self, width: int = 40):
        """
        初始化进度条
        
        Args:
            width: 进度条宽度（字符数）
        """
        self.width = width
    
    def render(self, percentage: Optional[int]) -> str:
        """
        渲染进度条
        
        Args:
            percentage: 百分比（0-100）
            
        Returns:
            进度条字符串
        """
        if percentage is None:
            return "[" + " " * self.width + "]"
        
        filled = int(self.width * percentage / 100)
        bar = "█" * filled + "░" * (self.width - filled)
        
        return f"[{bar}]"
    
    def get_progress_with_bar(self, percentage: Optional[int]) -> str:
        """
        获取带进度条的百分比显示
        
        Args:
            percentage: 百分比（0-100）
            
        Returns:
            格式化的字符串
        """
        bar = self.render(percentage)
        percentage_str = f"{percentage:3}%" if percentage is not None else " NA%"
        
        return f"{bar} {percentage_str}"


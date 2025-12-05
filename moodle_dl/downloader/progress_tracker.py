# -*- coding: utf-8 -*-
"""
增强的下载进度追踪器

提供详细的下载统计信息，包括：
- 速度和 ETA（智能预测）
- 成功/失败/跳过文件数
- 清晰的进度显示

ETA 计算采用智能混合算法：
- 指数移动平均（EMA）平滑速度波动
- 文件数量辅助预测（后期更准确）
- 初期静默期避免不准确显示
- ETA 平滑处理避免剧烈波动
"""

import time
from typing import Optional
from moodle_dl.utils import format_bytes, format_speed, calc_speed


class ProgressTracker:
    """
    增强的进度追踪器，用于显示详细的下载统计
    
    功能：
    - 实时速度计算（EMA 平滑）
    - 智能预计剩余时间（ETA）
    - 详细的文件统计（成功、失败、跳过）
    - 友好的格式化输出
    
    ETA 算法：
    - 使用 EMA 平滑速度，快速反映趋势但不剧烈波动
    - 结合文件数量预测，提高后期准确性
    - 初期 10 秒静默期，避免连接建立期的不准确
    - ETA 变化平滑处理，避免用户困惑
    """
    
    # EMA 平滑因子（0.1-0.5，越大对当前速度越敏感）
    EMA_ALPHA = 0.3
    
    # 初期静默期（秒），这段时间内不显示 ETA
    WARMUP_SECONDS = 10
    
    # 最小完成文件数，达到此数量后才启用文件数量辅助预测
    MIN_FILES_FOR_FILE_BASED_ETA = 5
    
    # ETA 平滑因子（避免 ETA 剧烈波动）
    ETA_SMOOTH_ALPHA = 0.5
    
    # 最大显示时间（秒），超过此值显示为 ">1天"
    MAX_ETA_SECONDS = 86400
    
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
        self.current_speed = 0.0  # bytes/sec（瞬时速度）
        self.average_speed = 0.0  # bytes/sec（全局平均速度）
        self.ema_speed = 0.0      # bytes/sec（EMA 平滑速度，用于 ETA）
        
        # ETA 平滑
        self.last_eta = None  # 上次计算的 ETA（用于平滑处理）
        
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
        
        # 计算瞬时速度
        time_diff = current_time - self.last_update_time
        if time_diff > 0:
            bytes_diff = downloaded_bytes - self.last_bytes_downloaded
            if bytes_diff <= 0:
                # 下载进度未前进，重置速度以避免 ETA 停滞在旧值
                self.current_speed = 0.0
                self.ema_speed = 0.0
            else:
                self.current_speed = calc_speed(self.last_update_time, current_time, bytes_diff)

                # 更新 EMA 平滑速度
                if self.current_speed is not None and self.current_speed > 0:
                    if self.ema_speed == 0:
                        # 首次设置
                        self.ema_speed = self.current_speed
                    else:
                        # EMA 更新: new = α * current + (1-α) * old
                        self.ema_speed = (
                            self.EMA_ALPHA * self.current_speed +
                            (1 - self.EMA_ALPHA) * self.ema_speed
                        )
            
        # 计算全局平均速度（用于总结统计）
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
    
    def _get_elapsed_seconds(self) -> float:
        """获取已经过的秒数"""
        return time.time() - self.start_time
    
    def _get_eta_by_speed(self) -> Optional[float]:
        """
        基于 EMA 速度计算 ETA
        
        Returns:
            预计剩余秒数（浮点数），如果无法计算则返回 None
        """
        # 先检查是否已完成（优先级高于速度检查）
        remaining_bytes = self.total_bytes - self.downloaded_bytes
        if remaining_bytes <= 0:
            return 0.0
        
        # 速度为 0 时无法计算
        if self.ema_speed <= 0:
            return None
            
        return remaining_bytes / self.ema_speed
    
    def _get_eta_by_files(self) -> Optional[float]:
        """
        基于文件数量计算 ETA
        
        假设剩余文件的平均处理时间与已完成文件相同
        
        Returns:
            预计剩余秒数（浮点数），如果无法计算则返回 None
        """
        processed_files = self.completed_files + self.failed_files + self.skipped_files
        
        # 先检查是否已完成（优先级高于其他检查）
        remaining_files = self.total_files - processed_files
        if remaining_files <= 0:
            return 0.0
        
        # 文件数量不足时无法可靠预测
        if processed_files < self.MIN_FILES_FOR_FILE_BASED_ETA:
            return None
            
        elapsed = self._get_elapsed_seconds()
        if elapsed <= 0:
            return None
            
        avg_time_per_file = elapsed / processed_files
        return remaining_files * avg_time_per_file
    
    def get_eta_seconds(self) -> Optional[int]:
        """
        获取预计剩余时间（秒）
        
        使用智能混合算法：
        1. 初期静默期（前 10 秒）不显示 ETA
        2. 基于 EMA 速度计算 ETA
        3. 如果文件数量足够，混合文件数量预测
        4. 平滑处理避免剧烈波动
        
        Returns:
            预计剩余秒数，如果无法计算则返回 None
        """
        # 初期静默期，不显示 ETA
        elapsed = self._get_elapsed_seconds()
        if elapsed < self.WARMUP_SECONDS:
            return None
        
        # 计算基于速度的 ETA
        eta_by_speed = self._get_eta_by_speed()
        if eta_by_speed is None:
            return None
        
        # 计算最终 ETA
        final_eta = eta_by_speed
        
        # 如果文件数量足够，混合文件数量预测
        eta_by_files = self._get_eta_by_files()
        if eta_by_files is not None:
            # 动态权重：完成比例越高，文件数量权重越高
            processed_files = self.completed_files + self.failed_files + self.skipped_files
            progress = processed_files / self.total_files if self.total_files > 0 else 0
            
            # 权重范围：0.2 ~ 0.6（完成比例越高，文件数量权重越高）
            file_weight = min(0.6, 0.2 + progress * 0.6)
            speed_weight = 1 - file_weight
            
            final_eta = speed_weight * eta_by_speed + file_weight * eta_by_files
        
        # 平滑处理：避免 ETA 剧烈波动
        if self.last_eta is not None and self.last_eta > 0:
            # 如果变化超过 50%，平滑处理
            change_ratio = abs(final_eta - self.last_eta) / self.last_eta
            if change_ratio > 0.5:
                final_eta = (
                    self.ETA_SMOOTH_ALPHA * final_eta + 
                    (1 - self.ETA_SMOOTH_ALPHA) * self.last_eta
                )
        
        # 更新上次 ETA
        self.last_eta = final_eta
        
        # 转换为整数
        eta_seconds = int(final_eta)
        
        # 限制最大显示时间
        if eta_seconds > self.MAX_ETA_SECONDS:
            return None
        
        # 避免负值
        if eta_seconds < 0:
            return 0
            
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
            # 检查是否在初期静默期
            if self._get_elapsed_seconds() < self.WARMUP_SECONDS:
                return "计算中..."
            return ">1天"
        
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
        
        # 速度（使用 EMA 速度，更稳定）
        display_speed = self.ema_speed if self.ema_speed > 0 else self.current_speed
        speed_info = format_speed(display_speed)
        
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
        eta_str = self.format_eta(eta_seconds)
        if eta_str:
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

# -*- coding: utf-8 -*-
"""
课程验证模块

提供通过网页版 API 验证课程可访问性的功能。
用于 Fallback 策略中验证用户输入的课程 ID 是否真的可访问。
"""

import logging
from typing import Optional, Dict, Any

from moodle_dl.moodle.request_helper import RequestHelper
from moodle_dl.config import ConfigHelper
from moodle_dl.types import MoodleDlOpts


class CourseValidator:
    """通过网页版 API 验证课程可访问性"""
    
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts, request_helper: RequestHelper = None):
        """
        初始化验证器
        
        Args:
            config: 配置对象
            opts: Moodle-DL 选项对象
            request_helper: 可选的 RequestHelper 实例（如果未提供，会自动创建）
        """
        self.config = config
        self.opts = opts
        
        # 如果提供了 request_helper，直接使用
        if request_helper is not None:
            self.request_helper = request_helper
        else:
            # 尝试从 auth_session_manager 获取 RequestHelper
            auth_manager = config.get_auth_manager()
            if auth_manager and hasattr(auth_manager, 'get_request_helper'):
                self.request_helper = auth_manager.get_request_helper()
            else:
                # 如果都不可用，创建一个新的 RequestHelper
                try:
                    moodle_url = config.get_moodle_URL()
                    token = config.get_token()
                    if moodle_url and token:
                        self.request_helper = RequestHelper(config, opts, moodle_url, token)
                    else:
                        self.request_helper = None
                except Exception as e:
                    logging.warning(f"无法创建 RequestHelper: {e}")
                    self.request_helper = None
    
    def validate_course_exists_and_accessible(
        self,
        course_id: int
    ) -> Optional[Dict[str, Any]]:
        """
        验证课程是否存在且用户可以访问
        
        使用网页版 API core_course_get_courses 验证课程。
        这个验证检查：
        1. Context 检查：课程是否对用户可见
        2. Capability 检查：用户是否有 moodle/course:view 权限
        
        Args:
            course_id: 课程 ID
            
        Returns:
            Dict: 课程信息（如果可访问）
            None: 如果课程不可访问或不存在
        """
        try:
            # 确保有 request_helper
            if self.request_helper is None:
                logging.error("RequestHelper 未初始化")
                return None
            
            # 调用 core_course_get_courses API
            # 这是网页版 API，检查 context + capability
            # 根据官方实现，参数格式应该是: options.ids (数组)
            # 但 recursive_urlencode 不支持列表，所以使用字典格式: options[ids][0]
            # 使用 RequestHelper.post() 方法，它会自动处理 wsfunction 和 wstoken
            args = {
                'options': {
                    'ids': {
                        '0': course_id  # 使用字典而不是列表，因为 recursive_urlencode 不支持列表
                    }
                }
            }
            
            # 使用 post() 方法发送 POST 请求（会自动处理编码和错误检查）
            # 注意：post() 方法可能会抛出 MoodleAPIError 或 MoodleAuthError
            from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError
            try:
                response = self.request_helper.post('core_course_get_courses', args)
            except (MoodleAPIError, MoodleAuthError) as e:
                # RequestHelper 已经检测到顶层错误，直接抛出
                error_msg = str(e)
                logging.error(f"课程 {course_id}: {error_msg}")
                raise ValueError(error_msg) from e
            
            if not response:
                error_msg = "API 返回空响应"
                logging.error(f"课程 {course_id}: {error_msg}")
                raise ValueError(error_msg)
            
            # core_course_get_courses 返回列表，但可能包含异常
            if isinstance(response, list):
                if len(response) == 0:
                    error_msg = "API 返回空列表（课程可能不存在或不可访问）"
                    logging.error(f"课程 {course_id}: {error_msg}")
                    raise ValueError(error_msg)
                
                # 检查第一个元素是否是异常
                first_item = response[0]
                if isinstance(first_item, dict) and 'exception' in first_item:
                    error_msg = first_item.get('message', 'Unknown error')
                    error_code = first_item.get('errorcode', 'unknown')
                    
                    # 不同错误类型的日志
                    if 'not accessible' in error_msg.lower():
                        full_error = (
                            f"Context 检查失败 (not accessible) - "
                            f"可能是课程被隐藏/存档/删除或你没有权限"
                        )
                    else:
                        full_error = f"API 错误 ({error_code}) - {error_msg}"
                    
                    logging.error(f"课程 {course_id}: {full_error}")
                    raise ValueError(full_error)
                
                # 正常情况：返回课程信息
                course_info = first_item
                logging.debug(
                    f"课程 {course_id}: 验证成功 - {course_info.get('fullname', 'N/A')}"
                )
                return course_info
            
            # 如果返回的是字典（可能是异常）
            elif isinstance(response, dict):
                if 'exception' in response:
                    error_msg = response.get('message', 'Unknown error')
                    error_code = response.get('errorcode', 'unknown')
                    full_error = f"API 错误 ({error_code}) - {error_msg}"
                    logging.error(f"课程 {course_id}: {full_error}")
                    raise ValueError(full_error)
                else:
                    # 意外的字典格式
                    error_msg = f"API 返回意外的字典格式: {list(response.keys())[:5]}"
                    logging.error(f"课程 {course_id}: {error_msg}")
                    raise ValueError(error_msg)
            
            # 其他情况
            error_msg = (
                f"API 返回无效响应类型: {type(response).__name__}\n"
                f"响应内容: {str(response)[:500]}"
            )
            logging.error(f"课程 {course_id}: {error_msg}")
            raise ValueError(error_msg)
        
        except ValueError:
            # 重新抛出 ValueError，让调用方处理
            raise
        except Exception as e:
            error_msg = f"验证时出错 - {str(e)}"
            logging.error(f"课程 {course_id}: {error_msg}")
            raise RuntimeError(error_msg) from e
    
    def validate_course_has_content(self, course_id: int) -> bool:
        """
        验证课程是否有内容（section 和 module）
        
        Args:
            course_id: 课程 ID
            
        Returns:
            bool: 课程是否有内容
        """
        try:
            # 确保有 request_helper
            if self.request_helper is None:
                logging.error("RequestHelper 未初始化")
                return False
            
            # 使用 post() 方法调用 Moodle Web Service API
            # post() 方法会自动处理 wstoken、wsfunction 和 moodlewsrestformat
            data = {'courseid': course_id}
            
            # 注意：post() 方法可能会抛出 MoodleAPIError 或 MoodleAuthError
            from moodle_dl.exceptions import MoodleAPIError, MoodleAuthError
            try:
                response = self.request_helper.post('core_course_get_contents', data)
            except (MoodleAPIError, MoodleAuthError) as e:
                # RequestHelper 已经检测到顶层错误
                logging.debug(f"课程 {course_id}: API 调用失败 - {str(e)}")
                return False
            
            if not response or not isinstance(response, list):
                return False
            
            # 检查是否有异常
            if len(response) > 0 and isinstance(response[0], dict):
                if 'exception' in response[0]:
                    return False
            
            # 至少有一个 section
            return len(response) > 0
        
        except Exception as e:
            logging.debug(f"课程 {course_id}: 检查内容时出错 - {str(e)}")
            return False


def validate_course_with_web_api(
    config: ConfigHelper,
    opts: MoodleDlOpts,
    course_id: int,
    check_content: bool = False,
    request_helper: RequestHelper = None
) -> Optional[Dict[str, Any]]:
    """
    便利函数：验证课程通过网页版 API
    
    Args:
        config: 配置对象
        opts: Moodle-DL 选项对象
        course_id: 课程 ID
        check_content: 是否也检查课程是否有内容
        request_helper: 可选的 RequestHelper 实例
        
    Returns:
        Dict: 课程信息（如果可访问）
        None: 如果课程不可访问
    """
    validator = CourseValidator(config, opts, request_helper)
    
    # 检查课程是否可访问
    course_info = validator.validate_course_exists_and_accessible(course_id)
    
    if not course_info:
        return None
    
    # 可选：检查课程是否有内容
    if check_content:
        if not validator.validate_course_has_content(course_id):
            logging.warning(f"课程 {course_id}: 验证成功但没有内容")
            return None
    
    return course_info


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
    
    def __init__(self, config: ConfigHelper, opts: MoodleDlOpts):
        """
        初始化验证器
        
        Args:
            config: 配置对象
            opts: Moodle-DL 选项对象
        """
        self.config = config
        self.opts = opts
        
        # 从 auth_session_manager 获取 RequestHelper
        # 这是获取已认证 RequestHelper 的标准方式
        auth_manager = config.get_auth_manager()
        if auth_manager and hasattr(auth_manager, 'get_request_helper'):
            self.request_helper = auth_manager.get_request_helper()
        else:
            # 如果 auth_manager 不可用，延迟初始化 RequestHelper
            # 直到实际需要时，在方法中创建
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
            args = {
                'wstoken': self.config.get_token(),
                'wsfunction': 'core_course_get_courses',
                'options[ids][0]': course_id,
                'moodlewsrestformat': 'json'
            }
            
            response = self.request_helper.get_URL(
                'https://{moodle_domain}/webservice/rest/server.php'.format(
                    moodle_domain=self.config.get_moodle_domain()
                ),
                args
            )
            
            if not response:
                logging.warning(f"课程 {course_id}: API 返回空响应")
                return None
            
            # 检查是否有异常信息
            if isinstance(response, dict):
                if 'exception' in response:
                    error_msg = response.get('message', 'Unknown error')
                    error_code = response.get('errorcode', 'unknown')
                    
                    # 不同错误类型的日志
                    if 'not accessible' in error_msg.lower():
                        logging.debug(
                            f"课程 {course_id}: Context 检查失败 (not accessible) - "
                            f"可能是课程被隐藏/存档/删除或你没有权限"
                        )
                    else:
                        logging.debug(
                            f"课程 {course_id}: API 错误 ({error_code}) - {error_msg}"
                        )
                    return None
                
                # 检查是否是课程数组
                if isinstance(response, list) and len(response) > 0:
                    course_info = response[0]
                    logging.debug(
                        f"课程 {course_id}: 验证成功 - {course_info.get('fullname', 'N/A')}"
                    )
                    return course_info
            
            # 没有返回有效的课程信息
            logging.warning(f"课程 {course_id}: API 返回无效响应")
            return None
        
        except Exception as e:
            logging.warning(f"课程 {course_id}: 验证时出错 - {str(e)}")
            return None
    
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
            
            args = {
                'wstoken': self.config.get_token(),
                'wsfunction': 'core_course_get_contents',
                'courseid': course_id,
                'moodlewsrestformat': 'json'
            }
            
            response = self.request_helper.get_URL(
                'https://{moodle_domain}/webservice/rest/server.php'.format(
                    moodle_domain=self.config.get_moodle_domain()
                ),
                args
            )
            
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
    check_content: bool = False
) -> Optional[Dict[str, Any]]:
    """
    便利函数：验证课程通过网页版 API
    
    Args:
        config: 配置对象
        opts: Moodle-DL 选项对象
        course_id: 课程 ID
        check_content: 是否也检查课程是否有内容
        
    Returns:
        Dict: 课程信息（如果可访问）
        None: 如果课程不可访问
    """
    validator = CourseValidator(config, opts)
    
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


# -*- coding: utf-8 -*-
import html
import json
import logging
import time
from typing import Dict, List, Optional

from moodle_dl.config import ConfigHelper
from moodle_dl.downloader.leganto_print import is_leganto_lti_launch_url
from moodle_dl.moodle.mods import MoodleMod
from moodle_dl.moodle.request_helper import RequestRejectedError
from moodle_dl.types import Course, File
from moodle_dl.utils import PathTools as PT


class LtiMod(MoodleMod):
    """
    LTI (External Tool) module handler

    Based on official Moodle Mobile App implementation:
    moodleapp/src/addons/mod/lti/services/lti.ts

    Supports:
    - LTI tool configuration and metadata
    - Launch parameters and endpoint data
    - Tool URLs and security settings
    - Instructor choices and custom parameters
    - Launch form generation
    """

    MOD_NAME = 'lti'
    MOD_PLURAL_NAME = 'ltis'
    MOD_MIN_VERSION = 2015111600  # 3.0

    # Launch container constants
    LAUNCH_CONTAINER_DEFAULT = 0
    LAUNCH_CONTAINER_EMBED = 1
    LAUNCH_CONTAINER_EMBED_NO_BLOCKS = 2
    LAUNCH_CONTAINER_NEW_WINDOW = 3
    LAUNCH_CONTAINER_EXISTING_WINDOW = 4
    LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW = 5

    @classmethod
    def download_condition(cls, config: ConfigHelper, file: File) -> bool:
        # LTI module handles external tool integrations (including kalvidres, helixmedia via cookie_mod)
        # For cookie_mod files (kalvidres, helixmedia), always allow to ensure proper handling
        # For regular LTI files, respect the user's configuration
        if file.module_modname.startswith('cookie_mod-'):
            # Cookie-based modules (kalvidres, helixmedia) should always be processed
            return not file.deleted
        # For regular LTI files, check configuration
        return config.get_download_ltis() or (not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted))

    def _get_launch_container_name(self, launch_container: int) -> str:
        """Get human-readable name for launch container mode"""
        names = {
            self.LAUNCH_CONTAINER_DEFAULT: 'Default',
            self.LAUNCH_CONTAINER_EMBED: 'Embed',
            self.LAUNCH_CONTAINER_EMBED_NO_BLOCKS: 'Embed (no blocks)',
            self.LAUNCH_CONTAINER_NEW_WINDOW: 'New window',
            self.LAUNCH_CONTAINER_EXISTING_WINDOW: 'Existing window',
            self.LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW: 'Replace Moodle window',
        }
        return names.get(launch_container, 'Unknown')

    async def real_fetch_mod_entries(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> Dict[int, Dict[int, Dict]]:
        """
        Fetch all LTI modules from courses

        Process:
        1. Get LTI tools by courses
        2. Get launch data for each tool
        3. Export metadata, configuration, and launch parameters
        """

        # Check if LTI downloads are enabled
        if not self.config.get_download_ltis():
            return {}

        result = {}
        ltis = await self._fetch_ltis(courses, core_contents)
        for lti in ltis:
            course_id = lti.get('course', 0)
            module_id = lti.get('coursemodule', 0)
            lti_id = lti.get('id', 0)
            lti_name = lti.get('name', 'External Tool')
            launch_data = await self._get_launch_data(lti_id)

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': lti_id,
                    'name': lti_name,
                    'files': self._build_lti_files(lti, launch_data),
                },
            )

        return result

    async def _fetch_ltis(self, courses: List[Course], core_contents: Dict[int, List[Dict]]) -> List[Dict]:
        try:
            response = await self.client.async_post(
                'mod_lti_get_ltis_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            return response.get('ltis', [])
        except (RequestRejectedError, Exception) as e:
            logging.debug(f"Mobile API 获取 LTI 模块失败: {e}，尝试使用 Web API fallback...")
            return await self._fetch_ltis_web_api(courses, core_contents)

    async def _get_launch_data(self, lti_id: int) -> Optional[Dict]:
        try:
            return await self.client.async_post(
                'mod_lti_get_tool_launch_data',
                {'toolid': lti_id},
            )
        except Exception as e:
            logging.debug("Error getting LTI launch data for tool %s: %s", lti_id, str(e))
            return None

    def _build_lti_files(self, lti: Dict, launch_data: Optional[Dict]) -> List[Dict]:
        lti_name = lti.get('name', 'External Tool')
        lti_intro = lti.get('intro', '')
        lti_files = self.get_introfiles(lti, 'lti_file', copy=True)

        intro_file = self.create_intro_file(lti_intro, lti.get('timemodified', 0))
        if intro_file:
            lti_files.append(intro_file)

        if launch_data:
            lti_files.extend(self._build_launch_files(lti, launch_data, lti_name))

        metadata = self._build_metadata(lti, lti_intro, launch_data)
        lti_files.append(self.create_metadata_file(metadata, timemodified=lti.get('timemodified', 0)))

        tool_shortcut = self._build_tool_shortcut(lti, lti_name)
        if tool_shortcut:
            lti_files.append(tool_shortcut)

        return lti_files

    def _build_launch_files(self, lti: Dict, launch_data: Dict, lti_name: str) -> List[Dict]:
        endpoint = launch_data.get('endpoint', '')
        parameters = launch_data.get('parameters', [])
        launch_files = []

        if self.config.get_download_metadata_files():
            launch_files.append(self._build_launch_parameters_file(endpoint, parameters, lti))
            launch_files.append(self._build_launch_form_file(endpoint, parameters, lti_name, lti))

        if self._should_create_leganto_pdf_file(endpoint, lti_name):
            launch_files.append(self._build_leganto_pdf_file(endpoint, parameters, lti_name, lti))

        return launch_files

    @staticmethod
    def _build_launch_parameters_file(endpoint: str, parameters: List[Dict], lti: Dict) -> Dict:
        params_content = {
            'endpoint': endpoint,
            'parameters': parameters,
            'parameter_count': len(parameters),
        }

        return {
            'filename': PT.to_valid_name('Launch Parameters', is_file=True) + '.json',
            'filepath': '/',
            'content': json.dumps(params_content, indent=2, ensure_ascii=False),
            'type': 'content',
            'timemodified': lti.get('timemodified', 0),
        }

    def _build_launch_form_file(self, endpoint: str, parameters: List[Dict], lti_name: str, lti: Dict) -> Dict:
        launch_form_html = self._generate_launch_form(endpoint, parameters, lti_name)
        return {
            'filename': PT.to_valid_name('Launch Form', is_file=True) + '.html',
            'filepath': '/',
            'html': launch_form_html,
            'type': 'html',
            'timemodified': lti.get('timemodified', 0),
            'filesize': len(launch_form_html),
        }

    @staticmethod
    def _build_leganto_pdf_file(endpoint: str, parameters: List[Dict], lti_name: str, lti: Dict) -> Dict:
        return {
            'filename': PT.to_valid_name(lti_name or 'Reading List', is_file=True) + '.pdf',
            'filepath': '/',
            'content_fileurl': endpoint,
            'content': json.dumps(
                {
                    'endpoint': endpoint,
                    'parameters': parameters,
                },
                indent=2,
                ensure_ascii=False,
            ),
            'type': 'leganto_pdf',
            'timemodified': lti.get('timemodified', 0),
            'filesize': 0,
            'isexternalfile': True,
        }

    def _build_metadata(self, lti: Dict, lti_intro: str, launch_data: Optional[Dict]) -> Dict:
        metadata = {
            'lti_id': lti.get('id', 0),
            'course_id': lti.get('course', 0),
            'name': lti.get('name', 'External Tool'),
            'intro': lti_intro,
            'tool_configuration': {
                'type_id': lti.get('typeid', 0),
                'tool_url': lti.get('toolurl', ''),
                'secure_tool_url': lti.get('securetoolurl', ''),
                'resource_key': lti.get('resourcekey', ''),
                'has_password': bool(lti.get('password', '')),
            },
            'launch_settings': {
                'launch_container': {
                    'value': lti.get('launchcontainer', 0),
                    'name': self._get_launch_container_name(lti.get('launchcontainer', 0)),
                },
                'show_title_launch': bool(lti.get('showtitlelaunch', 0)),
                'show_description_launch': bool(lti.get('showdescriptionlaunch', 0)),
                'debug_launch': bool(lti.get('debuglaunch', 0)),
            },
            'instructor_choices': {
                'send_name': lti.get('instructorchoicesendname', ''),
                'send_email_addr': bool(lti.get('instructorchoicesendemailaddr', 0)),
                'allow_roster': bool(lti.get('instructorchoiceallowroster', 0)),
                'allow_setting': bool(lti.get('instructorchoiceallowsetting', 0)),
                'accept_grades': bool(lti.get('instructorchoiceacceptgrades', 0)),
                'custom_parameters': lti.get('instructorcustomparameters', ''),
            },
            'grading': {
                'grade': lti.get('grade', 0),
            },
            'appearance': {
                'icon': lti.get('icon', ''),
                'secure_icon': lti.get('secureicon', ''),
            },
            'timestamps': {
                'time_created': lti.get('timecreated', 0),
                'time_modified': lti.get('timemodified', 0),
            },
            'service': {
                'service_salt': lti.get('servicesalt', ''),
            },
        }

        if launch_data:
            metadata['launch_data'] = {
                'has_endpoint': bool(launch_data.get('endpoint', '')),
                'parameter_count': len(launch_data.get('parameters', [])),
                'endpoint_url': launch_data.get('endpoint', ''),
            }

        return metadata

    @staticmethod
    def _build_tool_shortcut(lti: Dict, lti_name: str) -> Optional[Dict]:
        tool_url = lti.get('securetoolurl', '') or lti.get('toolurl', '')
        if not tool_url:
            return None

        return {
            'filename': PT.to_valid_name(lti_name, is_file=True),
            'filepath': '/',
            'content_fileurl': tool_url,
            'type': 'url',
            'timemodified': lti.get('timemodified', 0),
        }

    @staticmethod
    def _should_create_leganto_pdf_file(endpoint: str, lti_name: str) -> bool:
        return is_leganto_lti_launch_url(endpoint) and 'reading' in (lti_name or '').lower()

    def _generate_launch_form(self, endpoint: str, parameters: List[Dict], tool_name: str) -> str:
        """
        Generate an enhanced HTML form for launching the LTI tool

        This implements a more complete LTI launch protocol with:
        - LTI standard parameter categorization
        - OAuth signature detection
        - Security best practices
        - Enhanced parameter visibility

        Based on LTI 1.1 specification: https://www.imsglobal.org/lti/ltiv1p1/implementation-guide
        """
        # Categorize LTI parameters for better organization
        lti_params = {
            'core': [],      # Core LTI parameters (required)
            'context': [],   # Context information (course, etc.)
            'resource': [],  # Resource link information
            'user': [],      # User information
            'tool_consumer': [],  # Tool consumer info
            'extension': [], # LTI extension parameters (ext_*)
            'oauth': [],     # OAuth parameters
            'custom': [],    # Custom parameters
            'other': []      # Other parameters
        }

        # Categorize parameters
        for param in parameters:
            param_name = param.get('name', '')
            param_value = param.get('value', '')

            if param_name.startswith('oauth_'):
                lti_params['oauth'].append(param)
            elif param_name.startswith('ext_'):
                lti_params['extension'].append(param)
            elif param_name.startswith('custom_'):
                lti_params['custom'].append(param)
            elif param_name in ['resource_link_id', 'resource_link_title', 'resource_link_description']:
                lti_params['resource'].append(param)
            elif param_name in ['context_id', 'context_label', 'context_title', 'context_type']:
                lti_params['context'].append(param)
            elif param_name in ['user_id', 'user_image', 'roles', 'lis_person_sourcedid', 'lis_person_name_given',
                               'lis_person_name_family', 'lis_person_name_full', 'lis_person_contact_email_primary']:
                lti_params['user'].append(param)
            elif param_name in ['tool_consumer_info_product_family_code', 'tool_consumer_info_version',
                               'tool_consumer_instance_guid', 'tool_consumer_instance_name',
                               'tool_consumer_instance_description', 'tool_consumer_instance_url',
                               'tool_consumer_instance_contact_email']:
                lti_params['tool_consumer'].append(param)
            elif param_name in ['lti_message_type', 'lti_version']:
                lti_params['core'].append(param)
            else:
                lti_params['other'].append(param)

        # Count parameters
        total_params = len(parameters)

        # Detect if OAuth is used
        has_oauth = len(lti_params['oauth']) > 0

        # Generate timestamp
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())

        form_html = f'''<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Launch LTI Tool: {html.escape(tool_name)}</title>
    <style>
        * {{
            box-sizing: border-box;
        }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            max-width: 1200px;
            margin: 0 auto;
            padding: 20px;
            background-color: #f5f5f5;
            line-height: 1.6;
        }}
        .container {{
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            padding: 30px;
            margin-bottom: 20px;
        }}
        h1 {{
            color: #2c3e50;
            margin-top: 0;
            border-bottom: 3px solid #3498db;
            padding-bottom: 10px;
        }}
        h2 {{
            color: #34495e;
            font-size: 18px;
            margin-top: 25px;
            margin-bottom: 10px;
        }}
        .info {{
            background: #e8f4f8;
            border-left: 4px solid #3498db;
            padding: 15px;
            border-radius: 4px;
            margin-bottom: 20px;
        }}
        .info strong {{
            color: #2c3e50;
        }}
        .submit-btn {{
            background: #3498db;
            color: white;
            padding: 14px 28px;
            border: none;
            border-radius: 6px;
            font-size: 16px;
            font-weight: 600;
            cursor: pointer;
            transition: background 0.3s;
            margin-top: 20px;
        }}
        .submit-btn:hover {{
            background: #2980b9;
        }}
        .params-section {{
            margin-top: 30px;
        }}
        .param-category {{
            margin-bottom: 20px;
        }}
        .param-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-top: 10px;
        }}
        .param-table th {{
            background: #ecf0f1;
            padding: 8px 12px;
            text-align: left;
            font-weight: 600;
            border-bottom: 2px solid #bdc3c7;
        }}
        .param-table td {{
            padding: 6px 12px;
            border-bottom: 1px solid #ecf0f1;
            word-break: break-all;
        }}
        .param-table tr:hover {{
            background: #f8f9fa;
        }}
        .param-name {{
            font-family: "Courier New", monospace;
            color: #8e44ad;
            font-weight: 500;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 12px;
            font-size: 11px;
            font-weight: 600;
            margin-left: 8px;
        }}
        .badge-core {{ background: #e74c3c; color: white; }}
        .badge-oauth {{ background: #27ae60; color: white; }}
        .badge-ext {{ background: #9b59b6; color: white; }}
        .warning {{
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding: 12px;
            border-radius: 4px;
            margin-top: 15px;
        }}
        .timestamp {{
            text-align: center;
            color: #7f8c8d;
            font-size: 12px;
            margin-top: 20px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 Launch LTI Tool</h1>
        <h2>{html.escape(tool_name)}</h2>

        <div class="info">
            <p><strong>🎯 Endpoint:</strong> <code>{html.escape(endpoint)}</code></p>
            <p><strong>📊 Total Parameters:</strong> {total_params}</p>
            <p><strong>🔐 OAuth Signature:</strong> {'✅ Yes' if has_oauth else '❌ No'}</p>
            <p style="margin-top: 10px;">Click the button below to launch the external tool using LTI protocol.</p>
        </div>
        {'<div class="warning"><strong>⚠️ OAuth Notice:</strong> This launch includes an OAuth signature for secure authentication. The signature will be validated by the tool provider.</div>' if has_oauth else ''}

        <form action="{html.escape(endpoint)}" name="ltiLaunchForm" id="ltiLaunchForm" method="post" enctype="application/x-www-form-urlencoded">
'''

        # Add all parameters as hidden inputs
        for param in parameters:
            param_name = param.get('name', '')
            param_value = param.get('value', '')

            # Convert to string and HTML escape the values using standard library for security
            param_name_escaped = html.escape(str(param_name), quote=True)
            param_value_escaped = html.escape(str(param_value), quote=True)

            if param_name == 'ext_submit':
                # Make this a visible submit button
                form_html += f'            <input type="submit" class="submit-btn" value="{param_value_escaped}" />\n'
            else:
                # Hidden input
                form_html += f'            <input type="hidden" name="{param_name_escaped}" value="{param_value_escaped}" />\n'

        # If no ext_submit button was found, add a default submit button
        if not any(p.get('name') == 'ext_submit' for p in parameters):
            form_html += '            <input type="submit" class="submit-btn" value="🚀 Launch Tool" />\n'

        form_html += '        </form>\n'

        # Add parameter documentation section
        form_html += '''        <div class="params-section">
            <h2>📋 LTI Launch Parameters</h2>
            <p style="color: #7f8c8d; font-size: 14px;">The following parameters will be sent to the tool provider:</p>
'''

        # Generate parameter tables by category
        category_titles = {
            'core': 'Core LTI Parameters',
            'oauth': 'OAuth Parameters',
            'context': 'Context Information (Course)',
            'resource': 'Resource Link Information',
            'user': 'User Information',
            'tool_consumer': 'Tool Consumer Information',
            'extension': 'LTI Extension Parameters',
            'custom': 'Custom Parameters',
            'other': 'Other Parameters'
        }

        for category, title in category_titles.items():
            if len(lti_params[category]) > 0:
                badge_class = 'core' if category == 'core' else ('oauth' if category == 'oauth' else ('ext' if category == 'extension' else ''))
                badge_html = f'<span class="badge badge-{badge_class}">{len(lti_params[category])}</span>' if badge_class else f'<span class="badge">{len(lti_params[category])}</span>'

                form_html += f'            <div class="param-category">\n'
                form_html += f'                <h3>{title} {badge_html}</h3>\n'
                form_html += f'                <table class="param-table">\n'
                form_html += f'                    <thead><tr><th>Parameter Name</th><th>Value</th></tr></thead>\n'
                form_html += f'                    <tbody>\n'

                for param in lti_params[category]:
                    param_name = param.get('name', '')
                    param_value = param.get('value', '')
                    param_name_escaped = html.escape(str(param_name), quote=True)
                    param_value_escaped = html.escape(str(param_value), quote=True)

                    # Truncate long values for display
                    display_value = param_value_escaped
                    if len(display_value) > 100:
                        display_value = display_value[:97] + '...'

                    form_html += f'                        <tr>\n'
                    form_html += f'                            <td class="param-name">{param_name_escaped}</td>\n'
                    form_html += f'                            <td title="{param_value_escaped}">{display_value}</td>\n'
                    form_html += f'                        </tr>\n'

                form_html += f'                    </tbody>\n'
                form_html += f'                </table>\n'
                form_html += f'            </div>\n'

        form_html += f'''        </div>

        <div class="timestamp">
            <p><strong>LTI Version:</strong> LTI 1.1</p>
            <p><strong>Generated:</strong> {timestamp}</p>
            <p style="margin-top: 15px; font-size: 11px;">
                <em>This form implements the IMS LTI® 1.1 specification for launching external learning tools.
                All required LTI parameters are included for secure tool authentication.</em>
            </p>
        </div>
    </div>

    <script type="text/javascript">
        // Optional: Auto-submit the form after a short delay
        // Uncomment the line below to enable auto-launch
        // setTimeout(function() {{ document.getElementById('ltiLaunchForm').submit(); }}, 2000);

        // Add confirmation before launch (optional)
        document.querySelector('form').addEventListener('submit', function(e) {{
            // You can add custom validation here if needed
            console.log('Launching LTI tool with {total_params} parameters');
        }});
    </script>
</body>
</html>
'''
        return form_html

    async def _fetch_ltis_web_api(
        self, courses: List[Course], core_contents: Dict[int, List[Dict]]
    ) -> List[Dict]:
        """
        使用 Web API fallback 获取 LTI 模块信息。

        这是 mod_lti_get_ltis_by_courses 的 fallback 实现。
        通过 core_course_get_contents 获取 lti 模块信息。

        改进版：从 module 对象中提取更多可用字段，减少硬编码默认值

        Return: 转换为与 Mobile API 相同格式的 lti 列表
        """
        logging.debug('🌐 使用 Web API fallback 获取 LTI 模块信息...')

        ltis = []

        # 从 core_contents 中提取 lti 模块
        modules_by_course = self.extract_modules_from_core_contents(courses, core_contents, 'lti')

        for course in courses:
            course_id = course.id
            if course_id not in modules_by_course:
                continue

            for module in modules_by_course[course_id]:
                # 从 contents 中提取 toolurl
                toolurl = ''
                securetoolurl = ''
                contents = module.get('contents', [])
                for content in contents:
                    if content.get('type') == 'url':
                        url = content.get('fileurl', '')
                        # 区分 HTTPS 和 HTTP URL
                        if url.startswith('https://'):
                            securetoolurl = url
                        else:
                            toolurl = url
                        # 如果只找到一个URL，两个都使用（确保至少有一个有效）
                        if not toolurl and not securetoolurl:
                            toolurl = url
                        break

                # 从 module 对象中提取更多字段
                # core_course_get_contents 提供的 module 对象包含以下字段：
                # - id (course module id)
                # - instance (LTI instance id)
                # - name (module name)
                # - description (intro/description)
                # - modname (module type, 应该是 'lti')
                # - modplural (module plural name)
                # - availability (availability settings)
                # - visible (是否可见)
                # - uservisible (用户是否可见)
                # - indentation (缩进级别)
                # - onclick (点击行为)
                # - aftereditlink (编辑后链接)
                # - moduleicon (模块图标)
                # - moduleplural (模块复数名)
                # - contents (内容列表)
                # - timemodified (修改时间)
                # - section (section id)
                # - sectionnumber (section 编号)
                # - sectionname (section 名称)

                # 尝试从 module 的可用字段中提取更多 LTI 特定信息
                # 注意：core_course_get_contents 不会提供完整的 LTI 配置，
                # 但我们可以提取更多可用的元数据

                lti = {
                    'id': module.get('instance', 0),
                    'coursemodule': module.get('id', 0),
                    'course': course_id,
                    'name': module.get('name', 'LTI'),
                    'intro': module.get('description', ''),
                    'introformat': 1,  # 默认 HTML 格式

                    # URLs
                    'toolurl': toolurl,
                    'securetoolurl': securetoolurl,

                    # Instructor choices (core_course_get_contents 不提供这些信息，保持默认值)
                    'instructorchoicesendname': 0,
                    'instructorchoicesendemailaddr': 0,
                    'instructorchoiceallowroster': 0,
                    'instructorchoiceallowsetting': 0,
                    'instructorcustomparameters': '',
                    'instructorchoiceacceptgrades': 0,

                    # Grading
                    'grade': 0,  # 默认无评分

                    # Launch settings
                    'launchcontainer': self.LAUNCH_CONTAINER_DEFAULT,
                    'resourcekey': '',
                    'password': '',
                    'debuglaunch': 0,
                    'showtitlelaunch': 1 if module.get('name') else 0,  # 如果有名称，显示标题
                    'showdescriptionlaunch': 1 if module.get('description') else 0,  # 如果有描述，显示描述

                    # Service
                    'servicesalt': '',

                    # 时间戳
                    'timemodified': module.get('timemodified', 0),
                    'timecreated': module.get('timecreated', 0),  # 如果可用

                    # 可见性信息（从 module 对象中提取）
                    'visible': module.get('visible', 1),
                    'uservisible': module.get('uservisible', 1),
                    'availability': module.get('availability', None),

                    # Section 信息（从 module 对象中提取）
                    'section_id': module.get('section', 0),
                    'section_number': module.get('sectionnumber', 0),
                    'section_name': module.get('sectionname', ''),

                    # Web API fallback 标记
                    '_fallback': True,  # 标记这是 fallback 数据
                    '_data_source': 'core_course_get_contents',
                }
                ltis.append(lti)

        if not ltis:
            logging.warning('⚠️ Web API fallback 未找到任何 LTI 模块')
            raise ValueError('Web API 未能检索任何 LTI 模块信息')

        logging.debug(f'✅ Web API fallback 成功获取 {len(ltis)} 个 LTI 模块')
        return ltis

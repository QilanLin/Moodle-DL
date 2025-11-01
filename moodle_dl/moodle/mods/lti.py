import json
import logging
from typing import Dict, List

from moodle_dl.config import ConfigHelper
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
        return config.get_download_ltis() or (
            not (file.module_modname.endswith(cls.MOD_NAME) and file.deleted)
        )

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

        result = {}
        if not self.config.get_download_ltis():
            return result

        # Get all LTI tools for the courses
        try:
            response = await self.client.async_post(
                'mod_lti_get_ltis_by_courses',
                self.get_data_for_mod_entries_endpoint(courses),
            )
            ltis = response.get('ltis', [])
        except RequestRejectedError:
            logging.debug("No access to LTI modules or WS not available")
            return result
        except Exception as e:
            logging.debug("Error getting LTI modules: %s", str(e))
            return result

        for lti in ltis:
            course_id = lti.get('course', 0)
            module_id = lti.get('coursemodule', 0)
            lti_id = lti.get('id', 0)
            lti_name = lti.get('name', 'External Tool')

            lti_files = []

            # Copy introfiles to avoid modifying the original dict
            intro_files = list(lti.get('introfiles', []))
            self.set_props_of_files(intro_files, type='lti_file')
            lti_files.extend(intro_files)

            # Get LTI intro/description
            lti_intro = lti.get('intro', '')
            if lti_intro:
                lti_files.append(
                    {
                        'filename': PT.to_valid_name('Introduction', is_file=True) + '.html',
                        'filepath': '/',
                        'description': lti_intro,
                        'type': 'description',
                        'timemodified': lti.get('timemodified', 0),
                    }
                )

            # Get launch data (endpoint and parameters)
            launch_data = None
            try:
                launch_response = await self.client.async_post(
                    'mod_lti_get_tool_launch_data',
                    {'toolid': lti_id},
                )
                launch_data = launch_response
            except Exception as e:
                logging.debug("Error getting LTI launch data for tool %s: %s", lti_id, str(e))

            # Create launch parameters file if available
            if launch_data:
                endpoint = launch_data.get('endpoint', '')
                parameters = launch_data.get('parameters', [])

                # Save launch parameters as JSON
                params_content = {
                    'endpoint': endpoint,
                    'parameters': parameters,
                    'parameter_count': len(parameters),
                }

                lti_files.append(
                    {
                        'filename': PT.to_valid_name('Launch Parameters', is_file=True) + '.json',
                        'filepath': '/',
                        'content': json.dumps(params_content, indent=2, ensure_ascii=False),
                        'type': 'content',
                        'timemodified': lti.get('timemodified', 0),
                    }
                )

                # Generate launch form HTML
                launch_form_html = self._generate_launch_form(endpoint, parameters, lti_name)
                lti_files.append(
                    {
                        'filename': PT.to_valid_name('Launch Form', is_file=True) + '.html',
                        'filepath': '/',
                        'html': launch_form_html,
                        'type': 'html',
                        'timemodified': lti.get('timemodified', 0),
                        'filesize': len(launch_form_html),
                    }
                )

            # Create comprehensive metadata file
            metadata = {
                'lti_id': lti_id,
                'course_id': course_id,
                'name': lti_name,
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

            # Add launch data summary to metadata
            if launch_data:
                metadata['launch_data'] = {
                    'has_endpoint': bool(launch_data.get('endpoint', '')),
                    'parameter_count': len(launch_data.get('parameters', [])),
                    'endpoint_url': launch_data.get('endpoint', ''),
                }

            lti_files.append(
                {
                    'filename': PT.to_valid_name('metadata', is_file=True) + '.json',
                    'filepath': '/',
                    'timemodified': lti.get('timemodified', 0),
                    'content': json.dumps(metadata, indent=2, ensure_ascii=False),
                    'type': 'content',
                }
            )

            # Also create a URL shortcut to the tool (for manual access)
            tool_url = lti.get('securetoolurl', '') or lti.get('toolurl', '')
            if tool_url:
                lti_files.append(
                    {
                        'filename': PT.to_valid_name(lti_name, is_file=True),
                        'filepath': '/',
                        'content_fileurl': tool_url,
                        'type': 'url',
                        'timemodified': lti.get('timemodified', 0),
                    }
                )

            self.add_module(
                result,
                course_id,
                module_id,
                {
                    'id': lti_id,
                    'name': lti_name,
                    'files': lti_files,
                },
            )

        return result

    def _generate_launch_form(self, endpoint: str, parameters: List[Dict], tool_name: str) -> str:
        """
        Generate an HTML form for launching the LTI tool

        This mimics the official Moodle Mobile App's launcher generation
        """
        html = f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Launch {tool_name}</title>
    <style>
        body {{
            font-family: Arial, sans-serif;
            max-width: 800px;
            margin: 50px auto;
            padding: 20px;
        }}
        h1 {{
            color: #333;
        }}
        .info {{
            background: #f0f0f0;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 20px;
        }}
        .submit-btn {{
            background: #0066cc;
            color: white;
            padding: 12px 24px;
            border: none;
            border-radius: 5px;
            font-size: 16px;
            cursor: pointer;
        }}
        .submit-btn:hover {{
            background: #0052a3;
        }}
        .params {{
            margin-top: 20px;
            font-size: 12px;
            color: #666;
        }}
    </style>
</head>
<body>
    <h1>Launch External Tool: {tool_name}</h1>

    <div class="info">
        <p><strong>Endpoint:</strong> {endpoint}</p>
        <p><strong>Parameters:</strong> {len(parameters)} parameter(s)</p>
        <p>Click the button below to launch the external tool.</p>
    </div>

    <form action="{endpoint}" name="ltiLaunchForm" id="ltiLaunchForm" method="post" enctype="application/x-www-form-urlencoded">
'''

        # Add all parameters as hidden inputs
        for param in parameters:
            param_name = param.get('name', '')
            param_value = param.get('value', '')

            # HTML escape the values
            param_name_escaped = param_name.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')
            param_value_escaped = param_value.replace('"', '&quot;').replace('<', '&lt;').replace('>', '&gt;')

            if param_name == 'ext_submit':
                # Make this a visible submit button
                html += f'        <input type="submit" class="submit-btn" value="{param_value_escaped}" />\n'
            else:
                # Hidden input
                html += f'        <input type="hidden" name="{param_name_escaped}" value="{param_value_escaped}" />\n'

        # If no ext_submit button was found, add a default submit button
        if not any(p.get('name') == 'ext_submit' for p in parameters):
            html += '        <input type="submit" class="submit-btn" value="Launch Tool" />\n'

        html += '''    </form>

    <div class="params">
        <p><strong>Note:</strong> This form contains the LTI launch parameters. Submit the form to launch the external tool.</p>
    </div>

    <script type="text/javascript">
        // Optional: Auto-submit the form (uncomment if desired)
        // window.onload = function() {
        //     document.getElementById('ltiLaunchForm').submit();
        // };
    </script>
</body>
</html>
'''
        return html

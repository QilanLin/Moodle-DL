# -*- coding: utf-8 -*-
import base64
import binascii
import json
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from moodle_dl.downloader.leganto_print import is_leganto_reading_list_url


@dataclass
class LegantoDownloadPlan:
    endpoint: str
    launch_parameters: Optional[List[Dict[str, str]]]
    moodle_launch_url: Optional[str]
    course_url: Optional[str]
    parse_error: Optional[Exception] = None
    token_expired: bool = False

    def has_launch_data(self) -> bool:
        return bool(self.endpoint or self.launch_parameters or self.moodle_launch_url or self.course_url)

    def target_url(self) -> str:
        return self.endpoint or self.moodle_launch_url or self.course_url

    def print_kwargs(self) -> Dict:
        return {
            'launch_parameters': self.launch_parameters,
            'moodle_launch_url': None if self.launch_parameters is not None else self.moodle_launch_url,
            'course_url': None if self.launch_parameters is not None or self.moodle_launch_url else self.course_url,
        }


def leganto_lti_launch_token_expiry(launch_parameters) -> Optional[int]:
    """Return the expiry timestamp of a Leganto LTI id_token, if readable."""
    if not launch_parameters:
        return None

    for parameter in launch_parameters:
        if not isinstance(parameter, dict) or parameter.get('name') != 'id_token':
            continue

        token = parameter.get('value')
        if not isinstance(token, str):
            return None

        parts = token.split('.')
        if len(parts) < 2:
            return None

        payload = parts[1]
        payload += '=' * (-len(payload) % 4)
        try:
            decoded_payload = base64.urlsafe_b64decode(payload.encode('ascii')).decode('utf-8')
            expiry = json.loads(decoded_payload).get('exp')
            return int(expiry) if expiry is not None else None
        except (binascii.Error, UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError):
            return None

    return None


def leganto_course_url(content_type: str, file_url: str, moodle_url: str, course_id) -> Optional[str]:
    """Return the Moodle course page used to launch Leganto with course context."""
    if content_type != 'leganto_pdf' and not is_leganto_reading_list_url(file_url):
        return None

    moodle_url = (moodle_url or '').rstrip('/')
    if not moodle_url or not course_id:
        return None
    return f'{moodle_url}/course/view.php?id={course_id}'


def leganto_moodle_launch_url(content_type: str, moodle_url: str, module_id) -> Optional[str]:
    """Return the Moodle LTI module URL used to refresh a Leganto launch."""
    if content_type != 'leganto_pdf':
        return None

    moodle_url = (moodle_url or '').rstrip('/')
    if not moodle_url or not module_id:
        return None
    return f'{moodle_url}/mod/lti/view.php?id={module_id}'


def build_leganto_download_plan(
    *,
    content_type: str,
    file_url: str,
    file_content,
    moodle_url: str,
    course_id,
    module_id,
    now: Optional[float] = None,
    token_refresh_leeway_seconds: int = 30,
) -> LegantoDownloadPlan:
    endpoint = file_url
    launch_parameters = None
    course_url = leganto_course_url(content_type, file_url, moodle_url, course_id)
    moodle_launch_url = leganto_moodle_launch_url(content_type, moodle_url, module_id)
    parse_error = None

    if file_content:
        try:
            payload = json.loads(file_content)
            endpoint = payload.get('endpoint') or endpoint
            launch_parameters = payload.get('parameters')
            course_url = payload.get('course_url') or course_url
        except (TypeError, ValueError) as exc:
            parse_error = exc

    token_expiry = leganto_lti_launch_token_expiry(launch_parameters)
    current_time = time.time() if now is None else now
    token_expired = (
        token_expiry is not None
        and token_expiry <= current_time + token_refresh_leeway_seconds
    )
    if token_expired:
        launch_parameters = None
        endpoint = moodle_launch_url or endpoint

    return LegantoDownloadPlan(
        endpoint=endpoint,
        launch_parameters=launch_parameters,
        moodle_launch_url=moodle_launch_url,
        course_url=course_url,
        parse_error=parse_error,
        token_expired=token_expired,
    )

#!/usr/bin/env python3
"""
查找特定课程
"""

import requests
from urllib.parse import urljoin

# Moodle 配置
MOODLE_URL = "https://keats.kcl.ac.uk"
TOKEN = "451d8ccfcac580505c984527356d9f67"
USER_ID = 936054
TARGET_COURSE_ID = 134647
TARGET_COURSE_NAME = "6CCS3CFL Compilers and Formal Languages"

def get_all_courses(moodle_url, token, userid):
    """获取所有课程"""
    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'core_enrol_get_users_courses',
        'wstoken': token,
        'userid': userid
    }

    response = requests.post(api_url, data=params, timeout=30)

    if response.status_code == 200:
        return response.json()
    return None

def main():
    print("正在获取课程列表...")
    courses = get_all_courses(MOODLE_URL, TOKEN, USER_ID)

    if not courses:
        print("❌ 无法获取课程列表")
        return

    print(f"\n✅ 成功获取 {len(courses)} 门课程\n")

    # 查找目标课程 ID
    print(f"查找课程 ID: {TARGET_COURSE_ID}")
    found_by_id = False
    for course in courses:
        if course.get('id') == TARGET_COURSE_ID:
            print(f"\n✅ 找到课程 (通过 ID):")
            print(f"   ID: {course.get('id')}")
            print(f"   名称: {course.get('fullname')}")
            print(f"   短名: {course.get('shortname', 'N/A')}")
            found_by_id = True
            break

    if not found_by_id:
        print(f"\n❌ 未找到 ID 为 {TARGET_COURSE_ID} 的课程")

    # 查找包含特定关键词的课程
    print(f"\n查找包含关键词 '{TARGET_COURSE_NAME}' 的课程:")
    found_by_name = []
    for course in courses:
        fullname = course.get('fullname', '')
        if TARGET_COURSE_NAME in fullname:
            found_by_name.append(course)
            print(f"   - [{course.get('id')}] {fullname}")

    if not found_by_name:
        print(f"   未找到包含 '{TARGET_COURSE_NAME}' 的课程")

    # 显示所有课程（用于调试）
    print("\n" + "="*80)
    print("所有课程列表:")
    print("="*80)
    for i, course in enumerate(courses, 1):
        print(f"{i:3}. [{course.get('id'):6}] {course.get('fullname')}")

if __name__ == "__main__":
    main()

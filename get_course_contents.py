#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取课程详细内容
"""

import requests
from urllib.parse import urljoin
import json

# Moodle 配置
MOODLE_URL = "https://keats.kcl.ac.uk"
TOKEN = "451d8ccfcac580505c984527356d9f67"
COURSE_ID = 134647

def get_course_contents(moodle_url, token, course_id):
    """获取课程内容"""
    api_url = urljoin(moodle_url, "/webservice/rest/server.php")

    params = {
        'moodlewsrestformat': 'json',
        'wsfunction': 'core_course_get_contents',
        'wstoken': token,
        'courseid': course_id
    }

    response = requests.post(api_url, data=params, timeout=30)

    if response.status_code == 200:
        return response.json()
    return None

def count_modules_by_type(sections):
    """统计各类模块数量"""
    module_counts = {}
    total_modules = 0

    for section in sections:
        for module in section.get('modules', []):
            modname = module.get('modname', 'unknown')
            module_counts[modname] = module_counts.get(modname, 0) + 1
            total_modules += 1

    return module_counts, total_modules

def main():
    print(f"正在获取课程 {COURSE_ID} 的内容...\n")

    sections = get_course_contents(MOODLE_URL, TOKEN, COURSE_ID)

    if not sections:
        print("❌ 无法获取课程内容")
        return

    if isinstance(sections, dict) and 'errorcode' in sections:
        print(f"❌ API 错误: {sections.get('errorcode')}")
        print(f"   错误信息: {sections.get('message', 'N/A')}")
        return

    print(f"✅ 成功获取课程内容")
    print(f"   章节数: {len(sections)}")

    # 统计模块类型
    module_counts, total_modules = count_modules_by_type(sections)
    print(f"   模块总数: {total_modules}")

    print("\n" + "="*80)
    print("模块类型统计:")
    print("="*80)
    for modname, count in sorted(module_counts.items(), key=lambda x: x[1], reverse=True):
        print(f"  {modname:20} : {count:3} 个")

    print("\n" + "="*80)
    print("课程结构:")
    print("="*80)

    for section in sections:
        section_name = section.get('name', 'Unnamed Section')
        section_id = section.get('id', 'N/A')
        modules = section.get('modules', [])

        print(f"\n📁 [{section_id}] {section_name}")
        print(f"   {len(modules)} 个模块")

        if len(modules) > 0:
            print("   模块列表:")
            for module in modules:
                mod_id = module.get('id', 'N/A')
                mod_name = module.get('name', 'Unnamed')
                mod_type = module.get('modname', 'unknown')
                mod_url = module.get('url', '')

                # 特别标记 kalvidres (视频)
                if mod_type == 'kalvidres':
                    print(f"      🎥 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'resource':
                    print(f"      📄 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'url':
                    print(f"      🔗 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'label':
                    print(f"      🏷️  [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'folder':
                    print(f"      📂 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'quiz':
                    print(f"      📝 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'assign':
                    print(f"      📋 [{mod_id}] {mod_name} ({mod_type})")
                elif mod_type == 'forum':
                    print(f"      💬 [{mod_id}] {mod_name} ({mod_type})")
                else:
                    print(f"      ⚪ [{mod_id}] {mod_name} ({mod_type})")

                # 显示 URL（如果有且不是太长）
                if mod_url and len(mod_url) < 100 and mod_type in ['url', 'kalvidres']:
                    print(f"           URL: {mod_url}")

    # 查找所有 kalvidres 视频
    print("\n" + "="*80)
    print("🎥 Kaltura 视频列表 (kalvidres):")
    print("="*80)

    video_count = 0
    for section in sections:
        section_name = section.get('name', 'Unnamed Section')
        for module in section.get('modules', []):
            if module.get('modname') == 'kalvidres':
                video_count += 1
                mod_id = module.get('id')
                mod_name = module.get('name')
                mod_url = module.get('url', 'N/A')
                print(f"{video_count}. [{mod_id}] {mod_name}")
                print(f"   章节: {section_name}")
                print(f"   URL: {mod_url}\n")

    if video_count == 0:
        print("   未找到 kalvidres 视频模块")
        print("   (这些视频可能需要 cookies 才能访问)")

    # 保存详细内容到文件
    output_file = "/Users/linqilan/CodingProjects/Moodle-DL/course_contents_detail.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(sections, f, indent=2, ensure_ascii=False)
    print(f"\n💾 详细内容已保存到: {output_file}")

if __name__ == "__main__":
    main()

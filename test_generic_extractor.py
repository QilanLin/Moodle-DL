#!/usr/bin/env python3
"""
测试通用的 kalvidres 文本提取器（使用实际 HAR 文件）
"""

import json
import re
import html

HAR_FILE = "/Users/linqilan/CodingProjects/example_html_resposne_HAR"

def extract_text_content(html_content):
    """
    从 HTML 中提取文本内容（通用方法，不硬编码关键词）
    """
    text_data = {}

    # 1. 提取页面标题
    title_match = re.search(r'<title>([^<]+)</title>', html_content)
    if title_match:
        text_data['page_title'] = html.unescape(title_match.group(1).strip())

    # 2. 提取模块名称（H1）
    h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', html_content, re.DOTALL)
    if h1_match:
        h1_text = clean_html(h1_match.group(1))
        if h1_text:
            text_data['module_name'] = h1_text

    # 3. 提取 activity-description（核心内容）
    activity_desc = extract_activity_description(html_content)
    if activity_desc:
        text_data['activity_description'] = activity_desc

    return text_data


def extract_activity_description(html_content):
    """
    提取 activity-description 区域
    这是页面的核心文本内容（可能包含 Errata、描述等任何内容）
    """
    pattern = r'<div\s+class="activity-description"[^>]*>(.*?)</div>\s*</div>'
    match = re.search(pattern, html_content, re.DOTALL)

    if match:
        content_html = match.group(1)
        return clean_html_preserve_structure(content_html)

    return None


def clean_html(html_text):
    """清理 HTML 标签，返回纯文本"""
    if not html_text:
        return None

    text = re.sub(r'<br\s*/?>', '\n', html_text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()

    return text if text else None


def clean_html_preserve_structure(html_text):
    """
    清理 HTML 但保留基本结构（列表、换行、格式）
    """
    if not html_text:
        return None

    # 转换 <br> 为换行
    text = re.sub(r'<br\s*/?>', '\n', html_text)

    # 转换段落
    text = re.sub(r'</p>\s*<p[^>]*>', '\n\n', text)
    text = re.sub(r'</?p[^>]*>', '\n', text)

    # 转换列表项
    text = re.sub(r'<li[^>]*>', '\n• ', text)
    text = re.sub(r'</li>', '', text)

    # 转换列表容器
    text = re.sub(r'</?ul[^>]*>', '\n', text)
    text = re.sub(r'</?ol[^>]*>', '\n', text)

    # 保留粗体（转换为 Markdown）
    text = re.sub(r'<b[^>]*>(.*?)</b>', r'**\1**', text, flags=re.DOTALL)
    text = re.sub(r'<strong[^>]*>(.*?)</strong>', r'**\1**', text, flags=re.DOTALL)

    # 保留斜体
    text = re.sub(r'<i[^>]*>(.*?)</i>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<em[^>]*>(.*?)</em>', r'*\1*', text, flags=re.DOTALL)

    # 保留链接
    text = re.sub(r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', r'[\2](\1)', text, flags=re.DOTALL)

    # 移除所有其他标签
    text = re.sub(r'<[^>]+>', '', text)

    # 解码 HTML 实体
    text = html.unescape(text)

    # 清理空白
    text = re.sub(r'\n\s*\n\s*\n+', '\n\n', text)  # 多个空行变成双空行
    text = re.sub(r' +', ' ', text)  # 多个空格变成单空格
    text = text.strip()

    return text if text else None


def main():
    print("=" * 80)
    print("测试通用 Kalvidres 文本提取器")
    print("=" * 80)

    # 读取 HAR 文件
    print(f"\n1️⃣ 读取 HAR 文件: {HAR_FILE}")
    with open(HAR_FILE, 'r') as f:
        har_data = json.load(f)

    entry = har_data['log']['entries'][0]
    html_content = entry['response']['content']['text']
    print(f"   ✅ HTML 大小: {len(html_content):,} 字符")

    # 提取文本内容
    print(f"\n2️⃣ 提取文本内容（不硬编码关键词）...")
    text_data = extract_text_content(html_content)

    print(f"   ✅ 提取了 {len(text_data)} 个字段")

    # 显示结果
    print("\n" + "=" * 80)
    print("提取结果")
    print("=" * 80)

    if text_data.get('page_title'):
        print(f"\n📄 页面标题:")
        print(f"   {text_data['page_title']}")

    if text_data.get('module_name'):
        print(f"\n📌 模块名称:")
        print(f"   {text_data['module_name']}")

    if text_data.get('activity_description'):
        print(f"\n📝 Activity Description:")
        print(f"   " + "-" * 76)
        content = text_data['activity_description']
        for line in content.split('\n')[:15]:  # 显示前15行
            print(f"   {line}")
        if len(content.split('\n')) > 15:
            print(f"   ... (共 {len(content.split('\n'))} 行)")
        print(f"   " + "-" * 76)

    # 保存为 Markdown
    print(f"\n3️⃣ 保存为 Markdown 文件...")
    output_file = "/Users/linqilan/CodingProjects/Moodle-DL/kalvidres_extracted_generic.md"

    lines = []

    if text_data.get('page_title'):
        lines.append(f"# {text_data['page_title']}")
        lines.append("")

    if text_data.get('module_name'):
        lines.append(f"## {text_data['module_name']}")
        lines.append("")

    if text_data.get('activity_description'):
        lines.append(text_data['activity_description'])
        lines.append("")

    content = '\n'.join(lines)

    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(content)

    print(f"   ✅ 已保存到: {output_file}")

    # 验证提取的内容
    print(f"\n4️⃣ 验证...")

    # 检查是否提取到 Errata（作为内容存在，而不是硬编码搜索）
    if text_data.get('activity_description'):
        content = text_data['activity_description']
        print(f"   ✅ Activity description 长度: {len(content)} 字符")
        print(f"   ✅ 包含多行内容: {len(content.split(chr(10)))} 行")

        # 显示提取的格式保留情况
        if '**' in content:
            print(f"   ✅ 保留了粗体格式（Markdown）")
        if '•' in content:
            print(f"   ✅ 保留了列表结构")

    print("\n" + "=" * 80)
    print("✅ 测试完成")
    print("=" * 80)


if __name__ == '__main__':
    main()

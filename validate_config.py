#!/usr/bin/env python3
"""
配置验证命令行工具

用法:
    python validate_config.py [config_path]
    python validate_config.py --auto-fix [config_path]

如果不指定 config_path，将使用默认路径 ./config.json

示例:
    # 验证默认配置
    python validate_config.py
    
    # 验证指定配置
    python validate_config.py /path/to/config.json
    
    # 验证并自动修复
    python validate_config.py --auto-fix
    
    # 严格模式（警告也会导致失败）
    python validate_config.py --strict

Author: Claude (AI Assistant)
Date: 2025-11-20
"""

import argparse
import json
import sys
from pathlib import Path

# 添加项目根目录到路径
sys.path.insert(0, str(Path(__file__).parent))

from moodle_dl.config_validator import (
    ConfigValidator,
    auto_fix_config,
    validate_config_file,
)


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description='验证 Moodle-DL 配置文件',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s                          # 验证默认配置 ./config.json
  %(prog)s /path/to/config.json     # 验证指定配置
  %(prog)s --auto-fix                # 验证并自动修复问题
  %(prog)s --strict                  # 严格模式（警告也会失败）
        """
    )
    
    parser.add_argument(
        'config_path',
        nargs='?',
        default='./config.json',
        help='配置文件路径（默认: ./config.json）'
    )
    
    parser.add_argument(
        '--auto-fix',
        action='store_true',
        help='自动修复常见的配置问题'
    )
    
    parser.add_argument(
        '--strict',
        action='store_true',
        help='严格模式：警告也会导致验证失败'
    )
    
    parser.add_argument(
        '--quiet',
        '-q',
        action='store_true',
        help='静默模式：只在有错误时输出'
    )
    
    parser.add_argument(
        '--json',
        action='store_true',
        help='以 JSON 格式输出结果'
    )
    
    args = parser.parse_args()
    
    # 验证配置文件
    config_path = Path(args.config_path).resolve()
    
    if not args.quiet:
        print(f'正在验证配置文件: {config_path}')
        print('=' * 60)
    
    # 创建验证器
    validator = ConfigValidator(strict=args.strict)
    result = validator.validate_config_file(str(config_path))
    
    # 如果启用自动修复
    if args.auto_fix and not result.is_valid:
        if not args.quiet:
            print('\n🔧 尝试自动修复问题...\n')
        
        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)
            
            fixed_config, fixes = auto_fix_config(config_data)
            
            if fixes:
                # 保存修复后的配置
                with open(config_path, 'w', encoding='utf-8') as f:
                    json.dump(fixed_config, f, indent=4, ensure_ascii=False)
                
                if not args.quiet:
                    print('✅ 已自动修复以下问题:')
                    for fix in fixes:
                        print(f'  • {fix}')
                    print()
                
                # 重新验证
                result = validator.validate_config_data(fixed_config)
            else:
                if not args.quiet:
                    print('ℹ️  没有可以自动修复的问题')
                    print()
        
        except Exception as e:
            print(f'❌ 自动修复失败: {e}')
            sys.exit(1)
    
    # 输出结果
    if args.json:
        # JSON 格式输出
        output = {
            'is_valid': result.is_valid,
            'errors': [
                {
                    'field': err.field,
                    'message': err.message,
                    'severity': err.severity,
                    'suggestion': err.suggestion
                }
                for err in result.errors
            ],
            'warnings': [
                {
                    'field': warn.field,
                    'message': warn.message,
                    'severity': warn.severity,
                    'suggestion': warn.suggestion
                }
                for warn in result.warnings
            ]
        }
        print(json.dumps(output, indent=2, ensure_ascii=False))
    else:
        # 人类可读格式输出
        if not args.quiet or result.has_errors() or result.has_warnings():
            print(result.get_summary())
    
    # 返回退出码
    if result.is_valid:
        if not args.quiet and not args.json:
            print('\n✅ 配置验证通过！')
        sys.exit(0)
    else:
        if not args.quiet and not args.json:
            print('\n❌ 配置验证失败！')
        sys.exit(1)


if __name__ == '__main__':
    main()


# 终端渲染重复输出 Bug - 完整上下文

## 问题描述

在交互式配置向导中，使用 `Cutie.select_multiple()` 时，按上下箭头键或空格键会导致菜单项重复渲染。

### 症状
- **步骤 1/4 (选择课程)**: 现在工作正常 ✅
- **步骤 4/4 (配置模块类型)**: 仍然有问题 ❌ - "4 more lines below..." 重复显示多次

### 终端输出示例
```
4 more lines below... 
4 more lines below... 
4 more lines below... 
4 more lines below... 
```

## 关键信息

### 工作版本
- **Commit**: `a71c6ce24931f211fab1b40cbe7917ff8e489730`
- **状态**: 该版本没有此 bug

### 当前状态
- 已尝试完全恢复旧版本逻辑
- 所有关键逻辑已匹配旧版本
- 但问题仍然存在

## 相关文件

### 主要文件
- `moodle_dl/utils.py` - 包含 `TerminalMenuRenderer` 类和 `Cutie.select_multiple()` 方法
- `moodle_dl/cli/config_wizard.py` - 配置向导，调用 `Cutie.select_multiple()`

### 调用位置
1. **步骤 1/4 (选择课程)**: `config_wizard.py` 第 239 行
2. **步骤 4/4 (配置模块类型)**: `config_wizard.py` 第 793 行

## 旧版本代码 (工作版本)

### select_multiple 完整实现
```python
def select_multiple(
    options: List[str],
    ticked_indices: Optional[List[int]] = None,
    deselected_unticked_prefix: str = '[ ] ',
    deselected_ticked_prefix: str = '[x] ',
    selected_unticked_prefix: str = '[\033[32;1mx\033[0m] ',
    selected_ticked_prefix: str = '[\033[32;1mx\033[0m] ',
    caption_indices: Optional[List[int]] = None,
    caption_prefix: str = '',
    minimal_count: int = 0,
    maximal_count: Optional[int] = None,
    hide_confirm: bool = False,
    deselected_confirm_label: str = '(( confirm ))',
    selected_confirm_label: str = '(( confirm ))',
    reserved_lines: int = 3,
) -> List[int]:
    max_index = len(options) - (1 if hide_confirm else 0)
    max_lines = len(options) + 2  # Last two line are for confirm / error / bottom-indicator + empty line
    error_message = ''

    # Lines that were output in the previous interation
    lines_printed = 0
    # By how many entries is the list shifted
    shift = 0

    while True:
        print(f'\033[{lines_printed}A')

        console_lines = shutil.get_terminal_size().lines
        # Extra empty line for correct terminal behavior
        view_hight = max(0, min(console_lines - reserved_lines, max_lines))
        # View consists of
        # (top-indicator)
        # entries
        # (bottom-indicator / error / confirm)
        # empty line

        #  Darstellbaren Einträge =  view_hight - bottom-indicator/confirm/error - top-indicator
        if shift > (max_lines - 1) - (view_hight - 2):
            shift = (max_lines - 1) - (view_hight - 2)

        # Darstellbaren Einträge normal =  view_hight - top-indicator - bottom-indicator/confirm/error - empty line
        data_bottom = shift + (view_hight - 3)
        if shift == 0:
            # we do not need to print "x more lines above...", so we have one more entry line
            data_bottom += 1
        else:
            print(f'\033[K{shift} more lines above...')

        if data_bottom > len(options):
            data_bottom = len(options)

        for i in range(shift, data_bottom):
            option = options[i]
            console_columns = shutil.get_terminal_size().columns - 5
            printable_option = option.expandtabs().replace('\n', ' ').replace('\r', ' ')
            if len(printable_option) > console_columns:
                printable_option = printable_option[: (console_columns - 2)] + '..'

            prefix = ''
            if i in caption_indices:
                prefix = caption_prefix
            elif i == cursor_index:
                if i in ticked_indices:
                    prefix = selected_ticked_prefix
                else:
                    prefix = selected_unticked_prefix
            else:
                if i in ticked_indices:
                    prefix = deselected_ticked_prefix
                else:
                    prefix = deselected_unticked_prefix
            print(f'\033[K{prefix}{printable_option}')

        if data_bottom == len(options):
            # we do not need to print "x more lines below...", instead we print the confirm label or an error
            if hide_confirm:
                print(f'{error_message}\033[K')
            else:
                if cursor_index == max_index:
                    print(f'{selected_confirm_label} {error_message}\033[K')
                else:
                    print(f'{deselected_confirm_label} {error_message}\033[K')
        else:
            print(f'{len(options) - data_bottom} more lines below... {error_message}\033[K')

        lines_printed = view_hight

        error_message = ''
        keypress = readchar.readkey()
        if keypress in Cutie.DefaultKeys.up:
            new_index = cursor_index
            while new_index > 0:
                new_index -= 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    if cursor_index < shift:
                        if shift == 2:
                            shift = 0
                        else:
                            shift = cursor_index
                    break
        elif keypress in Cutie.DefaultKeys.down:
            new_index = cursor_index
            while new_index + 1 <= max_index:
                new_index += 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    if cursor_index >= data_bottom and data_bottom != len(options):
                        shift = cursor_index - (view_hight - 4)
                    break
        elif keypress in Cutie.DefaultKeys.select:
            if cursor_index in ticked_indices:
                if len(ticked_indices) - 1 >= minimal_count:
                    ticked_indices.remove(cursor_index)
            elif maximal_count is not None:
                if len(ticked_indices) + 1 <= maximal_count:
                    ticked_indices.append(cursor_index)
            else:
                ticked_indices.append(cursor_index)
        elif keypress in Cutie.DefaultKeys.confirm:
            if minimal_count > len(ticked_indices):
                error_message = f'Must select at least {minimal_count} options'
            elif maximal_count is not None and maximal_count < len(ticked_indices):
                error_message = f'Must select at most {maximal_count} options'
            else:
                break
        elif keypress in Cutie.DefaultKeys.select_all:
            for i in range(0, len(options)):
                if i not in ticked_indices:
                    ticked_indices.append(i)
        elif keypress in Cutie.DefaultKeys.interrupt:
            raise KeyboardInterrupt
    print('\033[1A\033[K', end='', flush=True)
    return ticked_indices
```

## 当前版本代码

### TerminalMenuRenderer 类
```python
class TerminalMenuRenderer:
    def __init__(self, options_count: int, reserved_lines: int = 3, extra_lines: int = 1):
        self.options_count = options_count
        self.reserved_lines = reserved_lines
        self.extra_lines = extra_lines
        self.max_lines = options_count + extra_lines
        self.lines_printed = 0
        self.shift = 0

    def move_cursor_to_start(self) -> None:
        """Move cursor back to the start of the menu area."""
        if self.lines_printed > 0:
            # Just move cursor up - don't return to line start
            # This matches the original working implementation
            print(f'\033[{self.lines_printed}A', end='', flush=True)

    def calculate_view_height(self) -> int:
        """Calculate the available view height for the menu."""
        console_lines = shutil.get_terminal_size().lines
        return max(0, min(console_lines - self.reserved_lines, self.max_lines))

    def calculate_data_bottom(self, view_height: int) -> Tuple[int, bool]:
        """Calculate the bottom index of data to display."""
        # Adjust shift if it's too large or negative
        max_shift = max(0, (self.max_lines - 1) - (view_height - 2))
        if self.shift > max_shift:
            self.shift = max_shift
        if self.shift < 0:
            self.shift = 0

        # Calculate data bottom based on view height and shift
        data_bottom = self.shift + (view_height - 3)
        should_print_above_indicator = False
        if self.shift == 0:
            # No "x more lines above..." needed, so we have one more entry line
            data_bottom += 1
        else:
            # Should print "x more lines above..." indicator (will be printed by caller)
            should_print_above_indicator = True

        # Ensure data_bottom doesn't exceed options count and is non-negative
        if data_bottom > self.options_count:
            data_bottom = self.options_count
        if data_bottom < 0:
            data_bottom = 0

        return data_bottom, should_print_above_indicator
    
    def print_above_indicator(self) -> None:
        """Print the 'x more lines above...' indicator if shift > 0."""
        if self.shift > 0:
            # Match original: clear to end of line and print (no \r)
            print(f'\033[K{self.shift} more lines above...', flush=True)

    def truncate_option_text(self, option: str, max_width: Optional[int] = None) -> str:
        """Truncate option text to fit terminal width."""
        if max_width is None:
            console_columns = shutil.get_terminal_size().columns - 5
        else:
            console_columns = max_width

        printable_option = option.expandtabs().replace('\n', ' ').replace('\r', ' ')
        if len(printable_option) > console_columns:
            printable_option = printable_option[: (console_columns - 2)] + '..'
        return printable_option

    def print_option_line(self, prefix: str, option: str) -> None:
        """Print a single option line with proper formatting."""
        truncated = self.truncate_option_text(option)
        # Match original: clear to end of line and print (no \r)
        # The cursor is already at the start of the line after move_cursor_to_start
        print(f'\033[K{prefix}{truncated}', flush=True)

    def print_bottom_indicator(
        self, 
        data_bottom: int, 
        bottom_text: Optional[str] = None,
        error_message: str = ''
    ) -> None:
        """Print bottom indicator (either "x more lines below..." or custom text)."""
        if data_bottom == self.options_count:
            # All options displayed, print custom bottom text or error
            if bottom_text:
                # Match original: clear to end of line and print (no \r)
                print(f'{bottom_text} {error_message}\033[K', flush=True)
            elif error_message:
                print(f'{error_message}\033[K', flush=True)
        else:
            # More options below
            more_lines = self.options_count - data_bottom
            # Match original: clear to end of line and print (no \r)
            print(f'{more_lines} more lines below... {error_message}\033[K', flush=True)
```

### select_multiple 当前实现
```python
@staticmethod
def select_multiple(
    options: List[str],
    ticked_indices: Optional[List[int]] = None,
    deselected_unticked_prefix: str = '[ ] ',
    deselected_ticked_prefix: str = '[x] ',
    selected_unticked_prefix: str = '[\033[32;1mx\033[0m] ',
    selected_ticked_prefix: str = '[\033[32;1mx\033[0m] ',
    caption_indices: Optional[List[int]] = None,
    caption_prefix: str = '',
    minimal_count: int = 0,
    maximal_count: Optional[int] = None,
    hide_confirm: bool = False,
    deselected_confirm_label: str = '(( confirm ))',
    selected_confirm_label: str = '(( confirm ))',
    reserved_lines: int = 3,
) -> List[int]:
    if caption_indices is None:
        caption_indices = []
    if ticked_indices is None:
        ticked_indices = []
    max_index = len(options) - (1 if hide_confirm else 0)
    renderer = TerminalMenuRenderer(len(options), reserved_lines, extra_lines=2)
    error_message = ''
    # Match original: initialize lines_printed to 0, will be updated after first render
    renderer.lines_printed = 0

    while True:
        renderer.move_cursor_to_start()
        view_height = renderer.calculate_view_height()
        data_bottom, should_print_above = renderer.calculate_data_bottom(view_height)

        # Match original: use view_height directly, not actual_lines_printed
        # The original implementation used view_height as lines_printed
        # This is simpler and works correctly
        
        # Print "x more lines above..." indicator if needed
        if should_print_above:
            renderer.print_above_indicator()

        # Render options
        for i in range(renderer.shift, data_bottom):
            option = options[i]
            if i in caption_indices:
                prefix = caption_prefix
            elif i == cursor_index:
                prefix = selected_ticked_prefix if i in ticked_indices else selected_unticked_prefix
            else:
                prefix = deselected_ticked_prefix if i in ticked_indices else deselected_unticked_prefix
            renderer.print_option_line(prefix, option)

        # Determine bottom text (confirm label or error)
        bottom_text = None
        if data_bottom == len(options):
            if not hide_confirm:
                if cursor_index == max_index:
                    bottom_text = selected_confirm_label
                else:
                    bottom_text = deselected_confirm_label

        # Print bottom indicator
        renderer.print_bottom_indicator(data_bottom, bottom_text, error_message)

        # Update lines_printed for next frame - use view_height like original
        renderer.lines_printed = view_height

        error_message = ''
        keypress = readchar.readkey()
        if keypress in Cutie.DefaultKeys.up:
            new_index = cursor_index
            while new_index > 0:
                new_index -= 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    # Match original: update shift directly, not through method
                    if cursor_index < renderer.shift:
                        if renderer.shift == 2:
                            renderer.shift = 0
                        else:
                            renderer.shift = cursor_index
                    break
        elif keypress in Cutie.DefaultKeys.down:
            new_index = cursor_index
            while new_index + 1 <= max_index:
                new_index += 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    # Match original: update shift directly, not through method
                    if cursor_index >= data_bottom and data_bottom != len(options):
                        renderer.shift = cursor_index - (view_height - 4)
                    break
        elif keypress in Cutie.DefaultKeys.select:
            if cursor_index in ticked_indices:
                if len(ticked_indices) - 1 >= minimal_count:
                    ticked_indices.remove(cursor_index)
            elif maximal_count is not None:
                if len(ticked_indices) + 1 <= maximal_count:
                    ticked_indices.append(cursor_index)
            else:
                ticked_indices.append(cursor_index)
        elif keypress in Cutie.DefaultKeys.confirm:
            if minimal_count > len(ticked_indices):
                error_message = f'Must select at least {minimal_count} options'
            elif maximal_count is not None and maximal_count < len(ticked_indices):
                error_message = f'Must select at most {maximal_count} options'
            else:
                break
        elif keypress in Cutie.DefaultKeys.select_all:
            for i in range(0, len(options)):
                if i not in ticked_indices:
                    ticked_indices.append(i)
        elif keypress in Cutie.DefaultKeys.interrupt:
            raise KeyboardInterrupt
    print('\033[1A\033[K', end='', flush=True)
    return ticked_indices
```

## 调用方式差异

### 步骤 1/4 (选择课程) - 工作正常
```python
selected_courses = Cutie.select_multiple(
    options=choices,
    ticked_indices=defaults,
    deselected_unticked_prefix='\033[1m( )\033[0m ',
    deselected_ticked_prefix='\033[1m(\033[32m✅\033[0;1m)\033[0m ',
    selected_unticked_prefix='\033[32;1m{ }\033[0m ',
    selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
)
```

### 步骤 4/4 (配置模块类型) - 有问题
```python
selected_indices = Cutie.select_multiple(
    options=choices,
    ticked_indices=current_selections,
    deselected_unticked_prefix='\033[1m[ ]\033[0m ',
    deselected_ticked_prefix='\033[1m[\033[32m✅\033[0;1m]\033[0m ',
    selected_unticked_prefix='\033[32;1m{ }\033[0m ',
    selected_ticked_prefix='\033[32;1m{✅}\033[0m ',
)
```

### 选项格式差异
- **步骤 1/4**: `f'{int(course.id):5}\t{course.fullname}'` (较短)
- **步骤 4/4**: `f'{name}\t{desc}'` (较长，包含描述)

## 已尝试的修复

1. ✅ 恢复 `move_cursor_to_start()` 为只移动光标，不回到行首
2. ✅ 所有打印方法使用 `\033[K` 清除到行尾，没有 `\r`
3. ✅ `lines_printed` 初始化为 0，然后使用 `view_height` 更新
4. ✅ `shift` 更新直接进行，不使用 `update_shift_for_cursor` 方法
5. ✅ 退出时使用 `\033[1A\033[K` 清除最后一行

## 关键发现

### 为什么步骤 1/4 工作正常？
- 选项文本较短
- 选项数量可能不同
- 前缀格式不同：`( )` vs `[ ]`

### 为什么步骤 4/4 仍然有问题？
- 选项文本较长（包含描述）
- 选项数量：26 个模块类型
- 可能的问题：
  1. `lines_printed` 计算不准确
  2. 清除逻辑不完整
  3. 终端兼容性问题

## 技术细节

### ANSI 转义序列
- `\033[nA` - 向上移动 n 行
- `\033[K` - 清除从光标到行尾
- `\033[1A\033[K` - 向上移动 1 行并清除整行
- `\r` - 回到行首（旧版本不使用）

### 关键变量
- `lines_printed`: 上一帧打印的行数
- `view_height`: 可用视图高度
- `shift`: 列表偏移量
- `data_bottom`: 显示数据的底部索引

### 渲染流程
1. 移动光标到菜单开始位置 (`move_cursor_to_start`)
2. 计算可用视图高度 (`calculate_view_height`)
3. 计算要显示的数据范围 (`calculate_data_bottom`)
4. 打印 "x more lines above..." (如果需要)
5. 打印选项行
6. 打印底部指示器 ("x more lines below..." 或确认标签)
7. 更新 `lines_printed` 为 `view_height`
8. 等待按键输入
9. 根据按键更新 `cursor_index` 和 `shift`
10. 重复步骤 1-9

## 可能的问题根源

1. **lines_printed 不准确**: 如果实际打印的行数不等于 `view_height`，下次移动光标时会不准确
2. **清除不完整**: `\033[K` 只清除到行尾，如果旧内容更长，可能残留
3. **终端兼容性**: 某些终端对 ANSI 转义序列的支持可能不同
4. **选项文本长度**: 长文本可能导致换行或显示问题

## 调试建议

1. 添加调试输出，打印每次渲染时的 `lines_printed`, `view_height`, `data_bottom`
2. 检查实际打印的行数是否等于 `view_height`
3. 测试不同的终端（Terminal.app, iTerm2, VS Code terminal）
4. 检查选项文本是否包含特殊字符或控制字符
5. 对比步骤 1/4 和步骤 4/4 的运行时状态差异

## 相关文件路径

- `moodle_dl/utils.py` - 第 980-1658 行（TerminalMenuRenderer 和 Cutie 类）
- `moodle_dl/cli/config_wizard.py` - 第 239 行（步骤 1/4），第 793 行（步骤 4/4）

## 环境信息

- OS: macOS (darwin 25.1.0)
- Python: 3.12
- Shell: zsh
- 工作目录: `/Users/linqilan/CodingProjects/moodle/Moodle-DL`

        max_index = len(options) - (1 if hide_confirm else 0)
        max_lines = len(options) + 2  # Last two line are for confirm / error / bottom-indicator + empty line
        error_message = ''

        # Lines that were output in the previous interation
        lines_printed = 0
        # By how many entries is the list shifted
        shift = 0

        while True:
            print(f'\033[{lines_printed}A')

            console_lines = shutil.get_terminal_size().lines
            # Extra empty line for correct terminal behavior
            view_hight = max(0, min(console_lines - reserved_lines, max_lines))
            # View consists of
            # (top-indicator)
            # entries
            # (bottom-indicator / error / confirm)
            # empty line

            #  Darstellbaren Einträge =  view_hight - bottom-indicator/confirm/error - top-indicator
            if shift > (max_lines - 1) - (view_hight - 2):
                shift = (max_lines - 1) - (view_hight - 2)

            # Darstellbaren Einträge normal =  view_hight - top-indicator - bottom-indicator/confirm/error - empty line
            data_bottom = shift + (view_hight - 3)
            if shift == 0:
                # we do not need to print "x more lines above...", so we have one more entry line
                data_bottom += 1
            else:
                print(f'\033[K{shift} more lines above...')

            if data_bottom > len(options):
                data_bottom = len(options)

            for i in range(shift, data_bottom):
                option = options[i]
                console_columns = shutil.get_terminal_size().columns - 5
                printable_option = option.expandtabs().replace('\n', ' ').replace('\r', ' ')
                if len(printable_option) > console_columns:
                    printable_option = printable_option[: (console_columns - 2)] + '..'

                prefix = ''
                if i in caption_indices:
                    prefix = caption_prefix
                elif i == cursor_index:
                    if i in ticked_indices:
                        prefix = selected_ticked_prefix
                    else:
                        prefix = selected_unticked_prefix
                else:
                    if i in ticked_indices:
                        prefix = deselected_ticked_prefix
                    else:
                        prefix = deselected_unticked_prefix
                print(f'\033[K{prefix}{printable_option}')

            if data_bottom == len(options):
                # we do not need to print "x more lines below...", instead we print the confirm label or an error
                if hide_confirm:
                    print(f'{error_message}\033[K')
                else:
                    if cursor_index == max_index:
                        print(f'{selected_confirm_label} {error_message}\033[K')
                    else:
                        print(f'{deselected_confirm_label} {error_message}\033[K')
            else:
                print(f'{len(options) - data_bottom} more lines below... {error_message}\033[K')

            lines_printed = view_hight

            error_message = ''
            keypress = readchar.readkey()
            if keypress in Cutie.DefaultKeys.up:
                new_index = cursor_index
                while new_index > 0:
                    new_index -= 1
                    if new_index not in caption_indices:
                        cursor_index = new_index
                        if cursor_index < shift:
                            if shift == 2:
                                shift = 0
                            else:
                                shift = cursor_index
                        break
            elif keypress in Cutie.DefaultKeys.down:
                new_index = cursor_index
                while new_index + 1 <= max_index:
                    new_index += 1
                    if new_index not in caption_indices:
                        cursor_index = new_index
                        if cursor_index >= data_bottom and data_bottom != len(options):
                            shift = cursor_index - (view_hight - 4)
                        break
            elif keypress in Cutie.DefaultKeys.select:
                if cursor_index in ticked_indices:
                    if len(ticked_indices) - 1 >= minimal_count:
                        ticked_indices.remove(cursor_index)
                elif maximal_count is not None:
                    if len(ticked_indices) + 1 <= maximal_count:
                        ticked_indices.append(cursor_index)
                else:
                    ticked_indices.append(cursor_index)
            elif keypress in Cutie.DefaultKeys.confirm:
                if minimal_count > len(ticked_indices):
                    error_message = f'Must select at least {minimal_count} options'
                elif maximal_count is not None and maximal_count < len(ticked_indices):
                    error_message = f'Must select at most {maximal_count} options'
                else:
                    break
            elif keypress in Cutie.DefaultKeys.select_all:
                for i in range(0, len(options)):
                    if i not in ticked_indices:
                        ticked_indices.append(i)
            elif keypress in Cutie.DefaultKeys.interrupt:
                raise KeyboardInterrupt
        print('\033[1A\033[K', end='', flush=True)
        return ticked_indices

## 旧版本完整代码 (从 commit a71c6ce)

```python
@staticmethod
def select_multiple(
    options: List[str],
    ticked_indices: Optional[List[int]] = None,
    deselected_unticked_prefix: str = '[ ] ',
    deselected_ticked_prefix: str = '[x] ',
    selected_unticked_prefix: str = '[\033[32;1mx\033[0m] ',
    selected_ticked_prefix: str = '[\033[32;1mx\033[0m] ',
    caption_indices: Optional[List[int]] = None,
    caption_prefix: str = '',
    minimal_count: int = 0,
    maximal_count: Optional[int] = None,
    hide_confirm: bool = False,
    deselected_confirm_label: str = '(( confirm ))',
    selected_confirm_label: str = '(( confirm ))',
    reserved_lines: int = 3,
) -> List[int]:
    max_index = len(options) - (1 if hide_confirm else 0)
    max_lines = len(options) + 2  # Last two line are for confirm / error / bottom-indicator + empty line
    error_message = ''

    # Lines that were output in the previous interation
    lines_printed = 0
    # By how many entries is the list shifted
    shift = 0

    while True:
        print(f'\033[{lines_printed}A')

        console_lines = shutil.get_terminal_size().lines
        # Extra empty line for correct terminal behavior
        view_hight = max(0, min(console_lines - reserved_lines, max_lines))
        # View consists of
        # (top-indicator)
        # entries
        # (bottom-indicator / error / confirm)
        # empty line

        #  Darstellbaren Einträge =  view_hight - bottom-indicator/confirm/error - top-indicator
        if shift > (max_lines - 1) - (view_hight - 2):
            shift = (max_lines - 1) - (view_hight - 2)

        # Darstellbaren Einträge normal =  view_hight - top-indicator - bottom-indicator/confirm/error - empty line
        data_bottom = shift + (view_hight - 3)
        if shift == 0:
            # we do not need to print "x more lines above...", so we have one more entry line
            data_bottom += 1
        else:
            print(f'\033[K{shift} more lines above...')

        if data_bottom > len(options):
            data_bottom = len(options)

        for i in range(shift, data_bottom):
            option = options[i]
            console_columns = shutil.get_terminal_size().columns - 5
            printable_option = option.expandtabs().replace('\n', ' ').replace('\r', ' ')
            if len(printable_option) > console_columns:
                printable_option = printable_option[: (console_columns - 2)] + '..'

            prefix = ''
            if i in caption_indices:
                prefix = caption_prefix
            elif i == cursor_index:
                if i in ticked_indices:
                    prefix = selected_ticked_prefix
                else:
                    prefix = selected_unticked_prefix
            else:
                if i in ticked_indices:
                    prefix = deselected_ticked_prefix
                else:
                    prefix = deselected_unticked_prefix
            print(f'\033[K{prefix}{printable_option}')

        if data_bottom == len(options):
            # we do not need to print "x more lines below...", instead we print the confirm label or an error
            if hide_confirm:
                print(f'{error_message}\033[K')
            else:
                if cursor_index == max_index:
                    print(f'{selected_confirm_label} {error_message}\033[K')
                else:
                    print(f'{deselected_confirm_label} {error_message}\033[K')
        else:
            print(f'{len(options) - data_bottom} more lines below... {error_message}\033[K')

        lines_printed = view_hight

        error_message = ''
        keypress = readchar.readkey()
        if keypress in Cutie.DefaultKeys.up:
            new_index = cursor_index
            while new_index > 0:
                new_index -= 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    if cursor_index < shift:
                        if shift == 2:
                            shift = 0
                        else:
                            shift = cursor_index
                    break
        elif keypress in Cutie.DefaultKeys.down:
            new_index = cursor_index
            while new_index + 1 <= max_index:
                new_index += 1
                if new_index not in caption_indices:
                    cursor_index = new_index
                    if cursor_index >= data_bottom and data_bottom != len(options):
                        shift = cursor_index - (view_hight - 4)
                    break
        elif keypress in Cutie.DefaultKeys.select:
            if cursor_index in ticked_indices:
                if len(ticked_indices) - 1 >= minimal_count:
                    ticked_indices.remove(cursor_index)
            elif maximal_count is not None:
                if len(ticked_indices) + 1 <= maximal_count:
                    ticked_indices.append(cursor_index)
            else:
                ticked_indices.append(cursor_index)
        elif keypress in Cutie.DefaultKeys.confirm:
            if minimal_count > len(ticked_indices):
                error_message = f'Must select at least {minimal_count} options'
            elif maximal_count is not None and maximal_count < len(ticked_indices):
                error_message = f'Must select at most {maximal_count} options'
            else:
                break
        elif keypress in Cutie.DefaultKeys.select_all:
            for i in range(0, len(options)):
                if i not in ticked_indices:
                    ticked_indices.append(i)
        elif keypress in Cutie.DefaultKeys.interrupt:
            raise KeyboardInterrupt
    print('\033[1A\033[K', end='', flush=True)
    return ticked_indices
```

## 关键差异总结

### 旧版本 (工作)
- 所有逻辑在一个函数中
- 直接使用局部变量 `lines_printed`, `shift`
- 每次循环开始时：`print(f'\033[{lines_printed}A')` (没有 `end='', flush=True`)
- 所有 `print` 语句：`print(f'...\033[K')` (没有 `flush=True`)

### 新版本 (有问题)
- 逻辑分离到 `TerminalMenuRenderer` 类
- 使用实例变量 `self.lines_printed`, `self.shift`
- `move_cursor_to_start()`: `print(f'\033[{self.lines_printed}A', end='', flush=True)`
- 所有 `print` 语句：`print(f'...\033[K', flush=True)`

### 可能的问题
1. `flush=True` 可能导致输出时机不同
2. 类封装可能影响某些行为
3. 某些细微的逻辑差异未被发现


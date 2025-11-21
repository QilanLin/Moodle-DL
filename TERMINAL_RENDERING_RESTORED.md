# 终端渲染修复 - 恢复旧版本逻辑

## 问题确认

从实际终端输出可以看到，问题确实存在，选项重复显示。

## 根本原因

在重构为 `TerminalMenuRenderer` 时，我们改变了原始 `cutie` 库的工作方式：

1. **添加了 `\r` 回到行首**：这改变了光标行为
2. **使用 `actual_lines_printed` 而不是 `view_height`**：这可能导致行数计算不准确

## 解决方案

**恢复旧版本（commit a71c6ce）的逻辑**，该版本没有这个 bug。

### 关键区别

**旧版本（工作正常）**：
```python
# 只移动光标，不回到行首
print(f'\033[{lines_printed}A')

# 清除到行尾并打印，不回到行首
print(f'\033[K{prefix}{printable_option}')

# 使用 view_height 作为 lines_printed
lines_printed = view_hight
```

**新版本（有bug）**：
```python
# 移动光标并回到行首
print(f'\033[{self.lines_printed}A\r')

# 回到行首，清除到行尾，然后打印
print(f'\r\033[K{prefix}{truncated}')

# 使用 actual_lines_printed
renderer.lines_printed = actual_lines_printed
```

### 修复内容

1. **`move_cursor_to_start()`**：只移动光标，不回到行首
   ```python
   print(f'\033[{self.lines_printed}A', end='', flush=True)
   ```

2. **所有打印方法**：使用 `\033[K` 清除到行尾，**不添加 `\r`**
   ```python
   print(f'\033[K{prefix}{truncated}', flush=True)
   ```

3. **`lines_printed` 更新**：使用 `view_height` 而不是 `actual_lines_printed`
   ```python
   renderer.lines_printed = view_height
   ```

## 修复的文件

- `moodle_dl/utils.py`：
  - `TerminalMenuRenderer.move_cursor_to_start()` - 恢复旧版本逻辑
  - `TerminalMenuRenderer.print_above_indicator()` - 移除 `\r`
  - `TerminalMenuRenderer.print_option_line()` - 移除 `\r`
  - `TerminalMenuRenderer.print_bottom_indicator()` - 移除 `\r`，调整顺序
  - `Cutie.select()` - 使用 `view_height` 作为 `lines_printed`
  - `Cutie.select_multiple()` - 使用 `view_height` 作为 `lines_printed`

## 验证

✅ 语法检查通过
✅ 模块导入成功
✅ 逻辑已恢复为旧版本

## 总结

通过恢复旧版本的逻辑，应该能彻底解决重复渲染问题。旧版本的方法更简单，不需要回到行首，只需要移动光标并清除到行尾即可。


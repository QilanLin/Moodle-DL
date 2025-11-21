# 调试检查清单

## 需要检查的关键点

### 1. lines_printed 的准确性
- [ ] 第一次循环时，`lines_printed = 0`，`move_cursor_to_start()` 不移动光标
- [ ] 第一次渲染后，`lines_printed = view_height`
- [ ] 后续循环时，`move_cursor_to_start()` 移动 `lines_printed` 行
- [ ] **问题**: 实际打印的行数是否真的等于 `view_height`？

### 2. 清除逻辑
- [ ] `move_cursor_to_start()` 只移动光标，不清除内容
- [ ] 每个 `print` 语句都使用 `\033[K` 清除到行尾
- [ ] **问题**: 如果旧内容比新内容长，`\033[K` 可能无法完全清除？

### 3. 选项文本处理
- [ ] 选项文本被 `truncate_option_text()` 截断
- [ ] 制表符被 `expandtabs()` 展开
- [ ] **问题**: 长文本是否导致意外的换行或显示问题？

### 4. 底部指示器
- [ ] "x more lines below..." 只在 `data_bottom != len(options)` 时打印
- [ ] 使用 `\033[K` 清除到行尾
- [ ] **问题**: 为什么这个文本会重复显示？

### 5. 终端兼容性
- [ ] 测试不同的终端应用
- [ ] 检查终端对 ANSI 转义序列的支持
- [ ] **问题**: 是否特定终端的问题？

## 建议的调试步骤

1. **添加调试输出**
   ```python
   print(f"DEBUG: lines_printed={renderer.lines_printed}, view_height={view_height}, data_bottom={data_bottom}", file=sys.stderr)
   ```

2. **检查实际打印的行数**
   - 在每次渲染后，计算实际打印的行数
   - 对比 `lines_printed` 和实际行数

3. **测试最小案例**
   - 创建一个简单的测试，只有几个选项
   - 逐步增加选项数量，找出问题出现的临界点

4. **对比步骤 1/4 和 4/4**
   - 记录两个步骤的运行时状态
   - 找出差异点

5. **检查终端输出**
   - 使用 `script` 命令记录终端输出
   - 分析 ANSI 转义序列的实际效果


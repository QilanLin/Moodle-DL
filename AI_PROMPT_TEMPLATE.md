# 给新 AI 的提示模板

## 快速开始提示

```
我有一个终端 UI 渲染 bug 需要修复。问题是在交互式菜单中，按上下箭头键或空格键时会出现重复渲染。

**关键信息：**
- 工作版本：commit `a71c6ce24931f211fab1b40cbe7917ff8e489730`（该版本没有此 bug）
- 当前版本：已尝试恢复旧版本逻辑，但问题仍然存在
- 症状：步骤 4/4 的菜单中，"4 more lines below..." 会重复显示多次

**相关文件：**
- `TERMINAL_RENDERING_BUG_CONTEXT.md` - 包含完整的问题描述、旧版本代码、当前版本代码、技术细节
- `DEBUG_CHECKLIST.md` - 调试检查清单
- `moodle_dl/utils.py` - 包含 `TerminalMenuRenderer` 类和 `Cutie.select_multiple()` 方法
- `moodle_dl/cli/config_wizard.py` - 配置向导，调用 `Cutie.select_multiple()`

**请先阅读 `TERMINAL_RENDERING_BUG_CONTEXT.md` 了解完整上下文，然后：**
1. 仔细对比旧版本（工作版本）和新版本的代码差异
2. 找出导致重复渲染的根本原因
3. 提供修复方案

**关键观察：**
- 步骤 1/4 (选择课程) 工作正常 ✅
- 步骤 4/4 (配置模块类型) 有问题 ❌
- 两个步骤使用相同的 `select_multiple()` 函数，但选项格式不同（步骤 4/4 的选项文本更长）

**可能的关键差异：**
- 旧版本：`print(f'\033[{lines_printed}A')` (没有 `end='', flush=True`)
- 新版本：`print(f'\033[{self.lines_printed}A', end='', flush=True)`
- 旧版本：所有 `print` 语句没有 `flush=True`
- 新版本：所有 `print` 语句都有 `flush=True`
```

## 详细提示（如果需要更深入的分析）

```
我需要修复一个终端 UI 渲染 bug。请按以下步骤进行：

**第一步：理解问题**
1. 阅读 `TERMINAL_RENDERING_BUG_CONTEXT.md` 了解完整上下文
2. 理解问题症状：在交互式菜单中，按上下箭头键或空格键时，底部指示器 "x more lines below..." 会重复显示

**第二步：对比代码**
1. 仔细对比旧版本（commit a71c6ce）和新版本的 `select_multiple()` 实现
2. 注意以下关键差异：
   - 旧版本：所有逻辑在一个函数中，使用局部变量
   - 新版本：逻辑分离到 `TerminalMenuRenderer` 类，使用实例变量
   - 旧版本：`print` 语句没有 `flush=True`
   - 新版本：所有 `print` 语句都有 `flush=True`

**第三步：分析问题**
1. 为什么步骤 1/4 工作正常，但步骤 4/4 有问题？
2. 两个步骤使用相同的函数，但选项格式不同（步骤 4/4 的选项文本更长）
3. 检查 `lines_printed` 的计算是否准确
4. 检查清除逻辑是否完整

**第四步：提供修复方案**
1. 找出导致重复渲染的根本原因
2. 提供修复代码
3. 确保修复不会影响步骤 1/4 的正常工作

**关键文件：**
- `TERMINAL_RENDERING_BUG_CONTEXT.md` - 完整上下文（必须阅读）
- `DEBUG_CHECKLIST.md` - 调试检查清单
- `moodle_dl/utils.py` - 主要代码文件
```

## 最简提示（如果 AI 很聪明）

```
修复终端 UI 渲染 bug：在交互式菜单中按上下箭头键时，底部指示器会重复显示。

**工作版本：** commit `a71c6ce24931f211fab1b40cbe7917ff8e489730`
**问题文件：** `moodle_dl/utils.py` 中的 `TerminalMenuRenderer` 和 `Cutie.select_multiple()`
**完整上下文：** 见 `TERMINAL_RENDERING_BUG_CONTEXT.md`

请阅读上下文文档，对比旧版本和新版本代码，找出导致重复渲染的原因并修复。
```

## 如果 AI 需要更多信息

如果新 AI 需要更多信息，可以告诉它：

1. **查看旧版本代码：**
   ```bash
   git show a71c6ce24931f211fab1b40cbe7917ff8e489730:moodle_dl/utils.py
   ```

2. **查看当前版本代码：**
   ```bash
   cat moodle_dl/utils.py
   ```

3. **运行测试：**
   ```bash
   moodle-dl --init --sso
   ```
   然后进入步骤 4/4，按上下箭头键观察问题

4. **关键变量：**
   - `lines_printed`: 上一帧打印的行数
   - `view_height`: 可用视图高度
   - `shift`: 列表偏移量
   - `data_bottom`: 显示数据的底部索引

5. **ANSI 转义序列：**
   - `\033[nA`: 向上移动 n 行
   - `\033[K`: 清除从光标到行尾
   - `\033[1A\033[K`: 向上移动 1 行并清除整行


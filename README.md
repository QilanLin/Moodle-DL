<div align="center">
    <br>
    <h2>Moodle-DL</h2>
    一个用于批量下载 Moodle 课程资料的命令行工具。
    <br>
    当前仓库为带中文界面和若干下载逻辑改动的维护分支。
</div>

---

`moodle-dl` 是一个控制台程序，用来下载 Moodle 课程中日常学习所需的内容。它还支持通知、增量下载、课程筛选，以及对多种 Moodle 模块和外部链接的处理。

## 功能概览

- 下载课程文件、作业、作业提交、Book、日历事件、论坛、Workshop、Lesson、Quiz、描述内容等。
- 处理外部链接和外部文件，例如 OpenCast、YouTube、Sciebo、OwnCloud、Kaltura、Helixmedia、Google Drive 等。
- 支持增量下载：再次运行时只下载新增或变更内容。
- 支持通知：Telegram、Discord、XMPP、邮件等。
- 支持配置向导，初始化和后续配置都可以通过 CLI 完成。
- 支持下载你已选课的课程，以及你可见的公开课程。

开发讨论主要在 GitHub Issues：<https://github.com/C0D3D3V/Moodle-DL/issues>

## 安装

> 注意：这是一个带中文本地化和行为调整的源码分支，建议直接从源码安装，不要依赖 PyPI。

### 环境要求

- Python `>= 3.7`
- 建议优先使用你准备运行 `moodle-dl` 的那个 Python 解释器来安装，例如 `python3 -m pip ...`

### 从源码安装

```bash
git clone <repository-url>
cd Moodle-DL
python3 -m pip install -e .
```

这里使用的是 editable install。优点是：
- 你 `git pull` 后通常不需要重新安装。
- 本地代码修改会直接生效。

### 更稳的运行方式

有些机器即使执行了 `pip install -e .`，也可能因为 `PATH`、`conda`、`venv` 或用户脚本目录配置问题，导致找不到 `moodle-dl` 命令。

这个仓库自带可执行脚本 `moodle-dl`，所以最稳的方式是直接在项目目录里运行：

```bash
./moodle-dl --help
```

如果你想完全绕开 PATH，也可以这样运行：

```bash
python3 -m moodle_dl.main --help
```

如果你确认已经安装成功但命令仍然找不到，请检查：

```bash
which python3
python3 -m pip --version
python3 -m pip show moodle-dl
python3 -c "import shutil; print(shutil.which('moodle-dl'))"
```

常见原因通常是：
- `pip` 安装到了另一个 Python 环境
- 脚本被安装到了 `~/.local/bin` 或某个虚拟环境的 `bin/`，但不在 `PATH` 里
- 你在当前目录里直接输入 `moodle-dl`，但 shell 默认不会搜索当前目录，这种情况应该写成 `./moodle-dl`

### Windows 说明

如果你在 Windows 上运行，建议使用 `PowerShell` 或 `CMD`，不要使用 `mintty`、`MINGW` 等终端。

如果依赖编译失败，可能需要安装 Visual C++ Build Tools。

## 快速开始

### 初始化配置

普通登录：

```bash
./moodle-dl --init
```

如果学校使用 SSO：

```bash
./moodle-dl --init --sso
```

如果要启用有头模式进行 SSO 调试：

```bash
MOODLE_DL_HEADFUL=1 ./moodle-dl --init --sso
```

如果你更喜欢 `python -m` 方式，也可以写成：

```bash
MOODLE_DL_HEADFUL=1 python3 -m moodle_dl.main --init --sso
```

### 开始下载

```bash
./moodle-dl
```

### 查看帮助

```bash
./moodle-dl --help
```

## 常用命令

- 初始化配置：

```bash
./moodle-dl --init
```

- 使用 SSO 初始化配置：

```bash
./moodle-dl --init --sso
```

- 下载课程内容：

```bash
./moodle-dl
```

- 打开配置向导：

```bash
./moodle-dl --config
```

- Token 失效后重新获取：

```bash
./moodle-dl --new-token
```

- 使用 SSO 重新获取 Token：

```bash
./moodle-dl --new-token --sso
```

- 刷新浏览器 Cookies：

```bash
./moodle-dl --refresh-cookies
```

- 重试失败下载：

```bash
./moodle-dl --retry-failed
```

- 指定下载目录：

```bash
./moodle-dl --path /your/download/path
```

- 重置已下载文件状态：

```bash
./moodle-dl --reset-downloaded-files
```

中文别名：

```bash
./moodle-dl --重置下载文件
```

## 使用说明

`moodle-dl` 主要依赖 Moodle Mobile API。如果你的 Moodle 站点禁用了官方 Moodle App 所使用的接口，那么本工具将无法正常连接。

如果你不希望把当前工作目录作为下载目录，请在命令里显式传入 `--path`。

### `--init`

- 创建初始配置。
- CLI 配置向导会引导你完成首次设置。
- 如果学校使用 SSO，可以额外加上 `--sso`。
- 如果后续保存的 token 被 Moodle 拒绝，可使用 `--new-token` 重新获取。
- 如需自动化登录，也可以额外提供 `--username`、`--password` 或 `--token`。

### `moodle-dl`

- 完成配置后，通常执行这一条命令就足够下载所有课程内容并输出结果。

### `--config`

- 打开 CLI 配置向导。
- 可修改几乎所有常用设置，例如：
  - 选择要下载的课程
  - 重命名课程目录
  - 是否为课程创建子目录结构
  - 是否下载 submissions、descriptions、description 内链接、database、quiz、lesson、workshop、forum 等
  - 是否下载外部文件
  - 是否下载依赖 cookies 的内容

并不是所有高级配置都在向导中可见，更多细项可直接查看配置文件。

## 这个分支的主要改动

相较于上游版本，这个分支包含以下定制：

- 完整中文本地化：界面、向导、日志和提示信息都尽量中文化。
- 更适合实际使用的交互体验：配置向导支持更顺畅的前进/后退导航。
- 下载逻辑调整：
  - `yt-dlp` 只在确实需要浏览器 cookies 的嵌入式视频场景启用，例如 `kalvidres`、`helixmedia`、部分 `LTI`。
  - 普通网页链接优先生成快捷方式，而不是盲目保存整页源码。
  - 对真实外部文件优先尝试下载文件本体。
- 更强的 SSO / cookies / 数据库状态跟踪支持。

## 常见问题

### 1. `moodle-dl: command not found`

优先使用下面任意一种：

```bash
./moodle-dl --help
```

或：

```bash
python3 -m moodle_dl.main --help
```

如果你坚持使用全局命令，再去排查 `PATH` 和安装环境。

### 2. SSO 登录失败

可以先用有头模式：

```bash
MOODLE_DL_HEADFUL=1 ./moodle-dl --init --sso
```

如果浏览器里登录了多个 Microsoft / Google / 学校账号，建议：
- 手动先在浏览器里选好正确账号
- 或者使用单独浏览器 profile

### 3. Cookies 过期

重新运行：

```bash
./moodle-dl --refresh-cookies
```

或者重新初始化：

```bash
./moodle-dl --init --sso
```

### 4. Git 更新后要不要重新安装

如果你是通过：

```bash
python3 -m pip install -e .
```

安装的，那么 `git pull` 之后通常不需要重新安装。

## 配置与日志文件

常见文件位置：

- 配置：`~/.moodle-dl/config.json`
- 日志：`~/.moodle-dl/MoodleDL.log`
- 状态数据库：`~/.moodle-dl/moodle_state.db`

某些自定义运行目录下，也可能在当前目录生成对应的 `config.json`、`MoodleDL.log`、`moodle_state.db`。

## 安全说明

- Moodle 账号密码本身不会被明文长期保存在标准配置流程里，但 token、cookies、通知账号等数据依然是敏感信息。
- `config.json` 中保存的 token 属于高敏感凭据，不应泄露。
- 如果启用了 cookie 相关功能，cookies 文件或数据库中的会话信息同样属于高敏感数据。
- 邮件 / XMPP 等通知服务的登录信息可能以明文形式保存，建议使用专门的通知账号，而不是主账号。

## 替代下载器

以下项目和 `moodle-dl` 目标相近，但各自面向的学校、技术路线和功能重点不同：

- [webeep-sync](https://github.com/toto04/webeep-sync#english-version)
  - 使用 Node.js 编写
  - 提供 GUI
  - 面向米兰理工大学 Moodle

- [syncMyMoodle](https://github.com/Romern/syncMyMoodle)
  - 与 `moodle-dl` 目标接近
  - 面向亚琛工业大学 Moodle

- [edu-sync](https://github.com/mkroening/edu-sync)
  - 使用 Rust 编写
  - 性能较好

- [tum-moodle-downloader](https://github.com/omareldeeb/tum-moodle-downloader)
  - 偏向网页抓取而不是 Moodle Mobile API
  - 提供一些更细粒度的下载命令
  - 面向慕尼黑工业大学 Moodle

- [moodle-buddy](https://github.com/marcelreppi/moodle-buddy)
  - Firefox / Chrome 插件
  - 支持批量下载和通知

- [moodle-downloader](https://github.com/harsilspatel/moodle-downloader)
  - Chrome 扩展
  - 批量下载 Moodle 资源

- [Orga Bot](https://github.com/YoshiiPlayzz/orga_bot)
  - 基于 `moodle-dl`
  - 用 Discord 发送 Moodle 文件

- [discord-moodle-bot](https://github.com/tjarbo/discord-moodle-bot)
  - 为 Moodle 课程提供 Discord 通知能力

## 贡献

如果你希望参与维护或提交改动，请查看 `CONTRIBUTING.md`。

## 许可证

本项目使用 GPL-3.0 许可证，详见 `LICENSE`。

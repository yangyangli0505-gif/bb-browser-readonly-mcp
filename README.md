# bb-browser-readonly-mcp

一个独立的、实验性的 **MCP Server**，把本机 [bb-browser](https://github.com/epiral/bb-browser) CLI 的部分只读能力提供给 Codex 等 MCP 客户端。无需其他智能体项目或其 Python 环境。

**0.1.1 已修复实测遇到的小红书正文读取超时。** 2026-10-30 在 Windows、Chrome 后台隐藏状态下，3 篇笔记的 5 次正文读取和 1 次首页评论读取全部成功，单次 MCP 调用耗时 0.22～1.07 秒；其中 2 篇此前没有正文缓存。X 搜索及其他操作系统尚未完成端到端验证。

本项目是社区封装，与 bb-browser 官方无隶属关系；它不是原官方 MCP 的完整替代品，也不会恢复 CLI 的 `--mcp` 参数。

```text
Codex / 其他 MCP 客户端
    ↓ MCP stdio
本项目 server.py → backend.py
    ↓ 执行命令
本机 bb-browser CLI
    ↓
Chrome → X / 小红书
```

## 功能范围

| MCP 工具 | 用途 |
| --- | --- |
| `bb_browser_status` | 检查程序、适配器与浏览器连接状态，不代表账号已登录或搜索成功 |
| `search_x` | 搜索 X 帖子 |
| `search_xiaohongshu` | 搜索小红书笔记 |
| `read_xiaohongshu_note` | 读取小红书笔记 |
| `read_xiaohongshu_comments` | 读取小红书评论 |

仅开放这 5 个工具。没有发布、点赞、发评论、通用点击或任意 adapter 执行工具。只读指不主动修改网站业务数据；搜索仍可能导航或打开浏览器标签页。

搜索通过已安装的社区 adapter 执行。正文和评论使用本仓库附带的同步页面读取脚本，由 bb-browser 执行，等待与超时由 Python 控制；不会向 MCP 客户端开放任意 JavaScript 执行工具。

## 当前验证情况

| 检查 | 结果 |
| --- | --- |
| Windows / Python 3.14 / MCP SDK 2.1.1 | 已运行协议测试 |
| MCP `initialize` 握手 | 通过 |
| `tools/list` 发现 5 个工具 | 通过 |
| 空搜索关键词返回 `INVALID_ARGUMENT` | 通过 |
| Windows 搜索参数不经过 shell 解释 | 在独立服务环境中验证过 |
| 小红书真实搜索 | 2026-10-30 通过；“北京徒步”，限制 3 条，返回 3 篇笔记及标题、作者、链接 |
| 小红书笔记正文 | 0.1.1 后台实测 5/5 成功，覆盖 3 篇笔记、2 篇无正文缓存、切回原笔记；0.22～1.07 秒 |
| 小红书评论读取 | 0.1.1 后台实测成功，返回首页 10 条评论；0.32 秒；未验证完整分页 |
| 回归测试 | 15 项 Python + 11 项 JavaScript 测试通过，覆盖计时、标签页固定、输入及返回数据校验 |
| X 搜索 | 未完成端到端验证 |
| macOS / Linux | 未测试 |

本次实测通过 stdio 调用已配置给 Codex 的独立 MCP 服务，使用已登录的小红书浏览器环境；该部署的执行代码与本仓库一致。

bb-browser、浏览器登录状态及网站 adapter 是独立的运行条件。本项目不能保证它们始终可用。

## 0.1.1：正文超时的原因与修复

旧实现等待整个社区详情 adapter 的异步脚本返回。实测发现：页面处于 `hidden` 状态时，导航后的 `setTimeout(1800)` 实际约 **379 秒**才恢复，早已超过 bb-browser 内部 30 秒请求上限。正文已在页面中，但脚本仍卡在固定等待，导致 `Daemon request timed out`。后台页面计时器可能被延后的机制见 [Chrome 官方说明](https://developer.chrome.com/blog/timer-throttling-in-chrome-88)。

现在每次页面调用只执行短同步步骤：校验账号和目标笔记、必要时触发只读加载、读取已完成的数据。Python 使用单一的 25 秒总时限轮询结果，并固定同一个标签页；页面内不再执行固定 sleep 或等待循环。读取脚本使用局部函数，不复用页面中的旧版全局 helper。超时也会明确归类为 `TIMEOUT`。

升级时请一起更新 `backend.py`、`server.py` 和新增的 **`xiaohongshu_read.js`**，然后刷新或重新启动客户端的 MCP 连接。只替换 Python 文件会缺少页面读取脚本。

## 安装

依赖：Python **3.10+**、Node.js **18+**、npm，以及 bb-browser 支持的浏览器。最低版本来自依赖包要求，实际协议测试环境是 Python 3.14；其他 Python 版本尚未测试。

下载本仓库到一个固定目录，在该目录打开 PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
npm install -g bb-browser@0.14.2
bb-browser site update
```

`requirements.txt` 固定 MCP SDK 版本。`requirements.lock.txt` 是 Windows / Python 3.14 测试环境的完整版本快照，包含 Windows 专用依赖；不要把它当作跨平台锁文件。

bb-browser 需要在其实际使用的浏览器配置中完成网站登录。本项目不提供账号，不复制 Cookie，也不读取其他项目的 `.env`。如果 `BB_BROWSER_CDP_URL` 为空，浏览器选择遵循 bb-browser 自己的行为。

## 接入 Codex

将下面内容加入 Codex 的 `config.toml`。**先把所有 `C:/path/to/...` 替换为你自己电脑上的绝对路径。** Python 必须指向刚创建的虚拟环境。

```toml
[mcp_servers.bb-browser-readonly]
enabled = true
command = "C:/path/to/bb-browser-readonly-mcp/.venv/Scripts/python.exe"
args = ["-E", "-s", "C:/path/to/bb-browser-readonly-mcp/server.py"]
cwd = "C:/path/to/bb-browser-readonly-mcp"
startup_timeout_sec = 20
tool_timeout_sec = 120
enabled_tools = ["bb_browser_status", "search_x", "search_xiaohongshu", "read_xiaohongshu_note", "read_xiaohongshu_comments"]

[mcp_servers.bb-browser-readonly.env]
BB_BROWSER_NODE = "C:/path/to/node.exe"
BB_BROWSER_CLI = "C:/path/to/npm/node_modules/bb-browser/dist/cli.js"
BB_BROWSER_CDP_URL = ""
```

定位 Windows 安装路径：

```powershell
(Get-Command node).Source
npm root -g
```

把 `npm root -g` 输出的目录与 `/bb-browser/dist/cli.js` 拼成 `BB_BROWSER_CLI`。本服务直接运行 Node 和 CLI 脚本，不经 `.cmd` 或 shell 转义搜索词。

其他支持 stdio 的 MCP 客户端也可以使用相同的 Python 启动命令、参数与环境变量。客户端负责启动服务进程，无需另开一个终端长期运行它。多个客户端通常各自启动一份服务进程，但仍可能共用同一个浏览器。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| `BB_BROWSER_NODE` | Node 可执行文件，默认在 `PATH` 中查找 |
| `BB_BROWSER_CLI` | bb-browser 的 `dist/cli.js` 绝对路径；未设置时尝试 Windows 默认全局 npm 目录 |
| `BB_BROWSER_CDP_URL` | 可选的浏览器调试地址；空值由 bb-browser 自行选择 |
| `BB_BROWSER_MIN_INTERVAL_S` | 当前服务进程内两次 site 调用的最小间隔，默认 `0.8` 秒 |

macOS / Linux 用户需要自行设置 `BB_BROWSER_CLI`，并改用 `.venv/bin/python` 等对应路径。相关平台尚未测试，不能据此声称兼容。

## 不操作浏览器的协议测试

```powershell
.\.venv\Scripts\python.exe smoke.py
```

`smoke.py` 使用自己的 Python 解释器启动同目录 `server.py`，只进行 MCP 握手、工具发现和空关键词错误检查。它不会调用状态工具或实际搜索，也不要求已安装 bb-browser 或已登录浏览器。

成功时输出包含：

```json
{"handshake":"OK","tool_count":5,"empty_keyword":"INVALID_ARGUMENT","browser_operations_performed":false}
```

协议测试通过后，仍需单独验证实际网站调用。如果工具返回 `LOGIN_REQUIRED`，在所选浏览器中完成登录后重试；如果返回 `Daemon request timed out`，应继续检查 bb-browser 守护进程和浏览器连接，不能据此宣称搜帖可用。

不连接浏览器的回归测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_backend.py -v
node --test tests/xiaohongshu_read.test.cjs
```

## 文件

- `server.py`：MCP 工具注册与 stdio 服务入口。
- `backend.py`：参数处理、CLI 执行与结果解析。
- `xiaohongshu_read.js`：小红书正文和首页评论的同步读取步骤，必须随 Python 文件一起安装。
- `smoke.py`：不操作浏览器的 MCP 协议测试。
- `tests/`：Python 超时与协议外壳测试、JavaScript 页面状态测试。
- `requirements.txt` / `requirements.lock.txt`：直接依赖与测试环境快照。

源码不包含浏览器配置、Cookie、账号或虚拟环境。

## 许可证

本项目采用 [MIT License](LICENSE)。bb-browser 和 MCP SDK 是外部安装的依赖，分别遵循各自的许可证。

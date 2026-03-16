# 架构设计文档

## 系统概览

Coding Agent Service 是一个单容器 Web 服务，通过 FastAPI 提供 HTTP/WebSocket API，利用 tmux 管理 AI 编码代理进程，并通过伪终端（PTY）实现浏览器终端实时流。

```
┌─────────────────────────────────────────────────────┐
│                   Docker 容器                        │
│                                                     │
│  ┌──────────────────────────────────────────────┐   │
│  │              FastAPI 应用 (app.py)            │   │
│  │                                              │   │
│  │  POST /task ──────────► 启动 tmux 会话        │   │
│  │  GET  /task ──────────► 查询任务状态          │   │
│  │  POST /task/cancel ───► 取消任务              │   │
│  │  GET  /ui ────────────► 返回终端 HTML          │   │
│  │  WS   /ws ────────────► PTY ↔ WebSocket 桥接  │   │
│  └──────────────────────────────────────────────┘   │
│                         │                           │
│              ┌──────────▼──────────┐                │
│              │   tmux 会话 "agent"  │                │
│              │  (run.sh 脚本执行)  │                │
│              └──────────┬──────────┘                │
│                         │                           │
│              ┌──────────▼──────────┐                │
│              │    /workspace       │                │
│              │  (克隆的 Git 仓库)  │                │
│              └─────────────────────┘                │
└─────────────────────────────────────────────────────┘
            │                        │
     HTTP/WS 客户端            外部 Git 仓库
      (浏览器 / API)
```

## 组件设计

### 1. FastAPI 应用层 (`app.py`)

**职责**：接收 HTTP/WebSocket 请求，协调任务生命周期。

**关键全局状态**：

| 变量 | 类型 | 说明 |
|------|------|------|
| `current_token` | `Optional[str]` | 当前任务的访问令牌 |
| `current_task` | `Optional[str]` | 当前任务描述 |
| `task_exit_code` | `Optional[int]` | 任务退出码（`None` 表示运行中）|
| `_monitor_task` | `Optional[asyncio.Task]` | 后台监控协程引用 |

### 2. 任务执行层（tmux + run.sh）

**执行流程**：

```
POST /task
  ├── 生成 16 字节随机 token
  ├── （可选）写入 MCP 服务器配置到 ~/.claude/settings.json
  ├── 生成 /tmp/run.sh 脚本：
  │     git clone → cd workspace → claude-code → git add/commit/push
  ├── 启动 tmux 会话（tmux new-session -d）
  └── 启动 _monitor_session 后台协程
```

**run.sh 脚本结构**：

```bash
#!/bin/bash
set -euo pipefail

# 1. 清理并克隆目标仓库
rm -rf /workspace && mkdir -p /workspace
git clone --branch <branch> <repo_url> /workspace
cd /workspace

# 2. 运行 AI 编码代理
claude-code [--yes] "<task>"

# 3. 提交并推送变更
git add -A
git diff --cached --quiet || git commit -m "agent: <task_summary>"
git push origin HEAD
```

### 3. 状态监控层（`_monitor_session` 协程）

**职责**：异步轮询 tmux 会话状态，在会话退出后读取退出码文件并更新 `task_exit_code`。

```
_monitor_session(token)
  └── loop (每 2 秒)
        ├── token 已变更 → 退出监控
        ├── tmux 会话存在 → 继续等待
        └── tmux 会话消失
              ├── 读取 /tmp/agent_exit_code
              └── 更新 task_exit_code
```

**状态转换图**：

```
IDLE ──[POST /task]──► RUNNING ──[tmux exit 0]──► DONE
  ▲                         │
  │                         └──[tmux exit ≠0]──► FAILED
  └────[POST /task/cancel]───────────────────────────┘
```

### 4. WebSocket 终端桥接层

**职责**：将浏览器 WebSocket 连接与 tmux 伪终端（PTY）双向绑定。

```
Browser (xterm.js) ◄──WS──► FastAPI /ws ◄──PTY──► tmux attach-session
    用户输入 ──────────────────────────────────────► PTY stdin
    终端输出 ◄──────────────────────────────────────── PTY stdout
```

**PTY 实现**：使用 `pty.openpty()` 创建主从伪终端对，`subprocess.Popen` 以从端作为 stdio 启动 `tmux attach-session`，主端通过 asyncio executor 异步读取并转发到 WebSocket。

### 5. 前端 UI (`ui.html`)

**技术栈**：纯静态 HTML + xterm.js 5.3.0 + xterm-addon-fit

**功能**：
- 通过 WebSocket 连接显示实时终端输出
- 顶部状态栏每 3 秒轮询 `/task` 接口展示任务状态
- `__TOKEN__` 占位符在服务端渲染时被替换为实际 token

## 数据流

### 任务提交流

```
Client                    FastAPI               tmux              Git Remote
  │                          │                    │                    │
  │──POST /task──────────────►│                    │                    │
  │                          │──new-session───────►│                    │
  │                          │  (run.sh)           │──git clone─────────►│
  │◄──{token, ui_url}────────│                    │◄──────────────────│
  │                          │──_monitor_task──────►│ (poll every 2s)    │
  │                          │                    │──claude-code exec  │
  │                          │                    │──git push──────────►│
  │                          │◄──exit code─────────│                    │
```

### WebSocket 终端流

```
Browser               FastAPI /ws              PTY master/slave     tmux
  │                       │                         │                 │
  │──WS connect───────────►│                         │                 │
  │                       │──pty.openpty()──────────►│                 │
  │                       │──Popen(tmux attach)──────────────────────►│
  │◄──terminal output─────│◄────────────────────────│◄────────────────│
  │──keyboard input───────►│─────────────────────────►│───────────────►│
```

## 部署架构

### Docker 镜像分层

```
python:3.12-slim          # 基础镜像
  └── apt: git, curl, tmux, ca-certificates, gnupg, nodejs
        └── npm: @anthropic-ai/claude-code
              └── pip: fastapi, uvicorn, pexpect, websockets
                    └── COPY: app.py, ui.html
                          └── CMD: python app.py
```

### 运行时配置

| 配置项 | 说明 |
|--------|------|
| `ANTHROPIC_API_KEY` | Anthropic API 密钥（必须通过环境变量注入）|
| 端口 `8000` | HTTP/WebSocket 服务端口 |
| `/workspace` | 任务执行工作目录（容器内） |
| `~/.claude/settings.json` | claude-code MCP 配置文件 |

### 扩展考虑

- 当前设计不支持多并发任务，如需扩展可引入任务队列（如 Celery + Redis）
- 持久化任务历史可引入数据库（如 SQLite）
- 多实例部署需将全局状态迁移至外部存储

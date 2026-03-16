# 原始需求文档

## 项目背景

Coding Agent Service 是一个基于 AI 驱动的编码代理服务，旨在通过 HTTP API 接收编程任务，自动克隆目标代码仓库，利用 AI 编码工具（claude-code）执行任务，并将修改提交回远程仓库。

## 功能需求

### 1. 任务提交（POST /task）

- 接收用户提交的编程任务请求，包含：
  - `repo_url`：目标 Git 仓库 HTTPS 地址（必填）
  - `repo_token`：仓库访问令牌，用于鉴权克隆和推送（选填）
  - `branch`：操作的目标分支，默认 `main`（选填）
  - `task`：自然语言描述的编程任务（必填）
  - `auto_mode`：是否以全自动模式运行 claude-code，默认 `true`（选填）
  - `mcp_servers`：MCP 工具服务器配置，用于扩展 AI 工具能力（选填）
- 同一时刻只允许一个任务处于运行状态；若当前有任务运行，则返回 `409 Conflict`
- 返回任务令牌（`token`）、终端 UI 地址（`ui_url`）及初始状态 `RUNNING`

### 2. 任务状态查询（GET /task）

- 通过 `token` 查询当前任务的运行状态
- 状态枚举：`IDLE`、`RUNNING`、`DONE`、`FAILED`
- 无效 token 返回 `401 Unauthorized`

### 3. 任务取消（POST /task/cancel）

- 通过 `token` 取消当前运行中的任务
- 取消后清理工作区、tmux 会话及临时文件
- 无效 token 返回 `401 Unauthorized`

### 4. 终端 UI（GET /ui）

- 返回一个基于 xterm.js 的 Web 终端页面
- 通过 WebSocket 实时查看 AI 编码代理的执行输出
- 支持键盘输入（可与代理交互）

### 5. WebSocket 终端流（WS /ws）

- 通过 WebSocket 协议将 tmux 伪终端（PTY）的输出实时推送到前端
- 支持前端向终端发送输入数据
- 无效 token 时以 `4401` 关闭连接

## 非功能需求

### 安全性

- 任务令牌使用 `secrets.token_hex(16)` 生成，保证随机性
- 仓库访问令牌通过 OAuth2 方式嵌入克隆 URL，不暴露于日志
- 所有 shell 参数均使用 `shlex.quote` 转义，防止命令注入

### 可靠性

- tmux 会话异常退出时，自动通过退出码文件记录任务失败状态
- 后台协程 `_monitor_session` 定期轮询 tmux 会话，确保状态最终一致

### 可观测性

- 服务通过 Python `logging` 模块输出结构化日志
- 任务状态可通过 `/task` API 随时查询

### 容器化

- 服务以 Docker 容器方式交付，基于 `python:3.12-slim`
- 容器内预装 git、tmux、Node.js 及 claude-code CLI
- 暴露 `8000` 端口

## 约束条件

- 同一容器实例同一时间只支持一个并发任务
- 依赖外部 Git 仓库和 claude-code CLI 工具
- 需要有效的 Anthropic API Key 才能运行 claude-code

# coding-agent-service

AI 驱动的编码代理服务——通过 HTTP API 提交编程任务，自动克隆目标仓库、调用 claude-code 执行任务，并将修改提交推送回远程仓库。

## 快速开始

### 环境要求

- Docker 20.10+
- 有效的 Anthropic API Key

### 构建镜像

```bash
docker build -t coding-agent-service .
```

### 运行容器

```bash
docker run -d \
  -p 8000:8000 \
  -e ANTHROPIC_API_KEY=<your-api-key> \
  --name coding-agent \
  coding-agent-service
```

### 提交任务

```bash
curl -X POST http://localhost:8000/task \
  -H "Content-Type: application/json" \
  -d '{
    "repo_url": "https://github.com/your-org/your-repo",
    "repo_token": "<your-github-token>",
    "branch": "main",
    "task": "在 README.md 中添加安装说明"
  }'
```

响应示例：

```json
{
  "token": "a1b2c3d4e5f6...",
  "ui_url": "/ui?token=a1b2c3d4e5f6...",
  "status": "RUNNING"
}
```

### 查看实时终端

在浏览器中打开 `http://localhost:8000/ui?token=<token>` 即可查看 AI 代理的实时执行过程。

## API 参考

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/task` | 提交编程任务 |
| `GET` | `/task?token=<token>` | 查询任务状态 |
| `POST` | `/task/cancel?token=<token>` | 取消当前任务 |
| `GET` | `/ui?token=<token>` | 查看终端 UI |
| `WS` | `/ws?token=<token>` | WebSocket 终端流 |

任务状态枚举：`IDLE` | `RUNNING` | `DONE` | `FAILED`

## 文档

- [原始需求文档](docs/requirements.md)
- [架构设计文档](docs/architecture.md)

## 开发

### 运行测试

```bash
pip install -r requirements.txt pytest httpx pytest-asyncio
pytest tests/ -v
```

### 本地开发（无 Docker）

```bash
pip install -r requirements.txt
python app.py
```
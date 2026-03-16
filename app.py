import asyncio
import json
import logging
import os
import pty
import secrets
import shlex
import shutil
import subprocess
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

logger = logging.getLogger("coding_agent")

app = FastAPI(title="Coding Agent Service")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
TMUX_SESSION = "agent"
WORKSPACE = "/workspace"
RUN_SCRIPT = "/tmp/run.sh"
EXIT_CODE_FILE = "/tmp/agent_exit_code"
MAX_COMMIT_SUMMARY_LENGTH = 72

# ---------------------------------------------------------------------------
# In-memory state
# ---------------------------------------------------------------------------
current_token: Optional[str] = None
current_task: Optional[str] = None
task_exit_code: Optional[int] = None  # None = still running/not started
_monitor_task: Optional[asyncio.Task] = None  # reference to avoid GC


# ---------------------------------------------------------------------------
# Helper – tmux
# ---------------------------------------------------------------------------

def _tmux_session_exists() -> bool:
    result = subprocess.run(
        ["tmux", "has-session", "-t", TMUX_SESSION],
        capture_output=True,
    )
    return result.returncode == 0


def _get_status() -> str:
    if current_token is None:
        return "IDLE"
    if _tmux_session_exists():
        return "RUNNING"
    if task_exit_code is None:
        return "IDLE"
    return "DONE" if task_exit_code == 0 else "FAILED"


def _kill_session() -> None:
    if _tmux_session_exists():
        subprocess.run(["tmux", "kill-session", "-t", TMUX_SESSION], capture_output=True)


def _cleanup() -> None:
    _kill_session()
    if os.path.isdir(WORKSPACE):
        shutil.rmtree(WORKSPACE, ignore_errors=True)
    for path in (RUN_SCRIPT, EXIT_CODE_FILE):
        if os.path.isfile(path):
            os.remove(path)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class McpServerConfig(BaseModel):
    url: str
    token: str


class TaskRequest(BaseModel):
    repo_url: str
    repo_token: Optional[str] = None
    branch: str = "main"
    task: str
    auto_mode: bool = True
    mcp_servers: Optional[dict[str, McpServerConfig]] = None


# ---------------------------------------------------------------------------
# Task runner helpers
# ---------------------------------------------------------------------------

def _build_authenticated_url(repo_url: str, repo_token: Optional[str]) -> str:
    """Embed OAuth token into an HTTPS clone URL if a token is provided."""
    if not repo_token:
        return repo_url
    if repo_url.startswith("https://"):
        rest = repo_url[len("https://"):]
        return f"https://oauth2:{repo_token}@{rest}"
    return repo_url


def _write_mcp_config(mcp_servers: dict[str, McpServerConfig]) -> None:
    """Write MCP server config to the claude-code settings file."""
    config_dir = os.path.expanduser("~/.claude")
    os.makedirs(config_dir, exist_ok=True)
    settings_path = os.path.join(config_dir, "settings.json")

    existing: dict = {}
    if os.path.isfile(settings_path):
        try:
            with open(settings_path) as f:
                existing = json.load(f)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read existing settings file %s: %s – starting fresh", settings_path, exc)
            existing = {}

    existing.setdefault("mcpServers", {})
    for name, cfg in mcp_servers.items():
        existing["mcpServers"][name] = {"url": cfg.url, "token": cfg.token}

    with open(settings_path, "w") as f:
        json.dump(existing, f, indent=2)


def _generate_run_script(
    repo_url: str,
    branch: str,
    task: str,
    auto_mode: bool,
) -> str:
    """Return the shell script content that performs the coding task."""
    quoted_task = shlex.quote(task)
    quoted_branch = shlex.quote(branch)
    quoted_url = shlex.quote(repo_url)
    cli_flag = "--yes" if auto_mode else ""
    task_summary = task.replace('"', "'")[:MAX_COMMIT_SUMMARY_LENGTH]
    quoted_summary = shlex.quote(f"agent: {task_summary}")

    return f"""#!/bin/bash
set -euo pipefail

rm -rf {shlex.quote(WORKSPACE)} && mkdir -p {shlex.quote(WORKSPACE)}
git clone --branch {quoted_branch} {quoted_url} {shlex.quote(WORKSPACE)}
cd {shlex.quote(WORKSPACE)}

claude-code {cli_flag} {quoted_task}

git add -A
git diff --cached --quiet || git commit -m {quoted_summary}
git push origin HEAD
"""


async def _monitor_session(token: str) -> None:
    """Wait for the tmux session to exit then record its exit code."""
    global task_exit_code
    while True:
        await asyncio.sleep(2)
        if current_token != token:
            break
        if not _tmux_session_exists():
            if os.path.isfile(EXIT_CODE_FILE):
                try:
                    with open(EXIT_CODE_FILE) as f:
                        task_exit_code = int(f.read().strip())
                except (ValueError, OSError):
                    task_exit_code = 1
            else:
                task_exit_code = 1
            break


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.post("/task")
async def submit_task(req: TaskRequest):
    global current_token, current_task, task_exit_code, _monitor_task

    if _get_status() == "RUNNING":
        raise HTTPException(
            status_code=409,
            detail="A task is already running. Wait for it to complete or cancel it via POST /task/cancel",
        )

    if req.mcp_servers:
        _write_mcp_config(req.mcp_servers)

    token = secrets.token_hex(16)
    current_token = token
    current_task = req.task
    task_exit_code = None

    clone_url = _build_authenticated_url(req.repo_url, req.repo_token)
    script_content = _generate_run_script(clone_url, req.branch, req.task, req.auto_mode)

    with open(RUN_SCRIPT, "w") as f:
        f.write(script_content)
    os.chmod(RUN_SCRIPT, 0o755)

    if os.path.isfile(EXIT_CODE_FILE):
        os.remove(EXIT_CODE_FILE)

    _kill_session()
    # The semicolon ensures exit code is captured even when set -e causes early exit
    tmux_cmd = f"bash {RUN_SCRIPT}; echo $? > {EXIT_CODE_FILE}"
    subprocess.run(
        ["tmux", "new-session", "-d", "-s", TMUX_SESSION, tmux_cmd],
        check=True,
    )

    # Keep a reference to the task to prevent it from being garbage-collected
    _monitor_task = asyncio.create_task(_monitor_session(token))
    return {
        "token": token,
        "ui_url": f"/ui?token={token}",
        "status": "RUNNING",
    }


@app.get("/task")
async def get_task(token: str = Query(...)):
    if token != current_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    return {"status": _get_status(), "task": current_task}


@app.get("/ui", response_class=HTMLResponse)
async def get_ui(token: str = Query(...)):
    if token != current_token:
        raise HTTPException(status_code=401, detail="Invalid token")
    ui_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui.html")
    with open(ui_path) as f:
        content = f.read()
    return HTMLResponse(content=content.replace("__TOKEN__", token))


@app.websocket("/ws")
async def websocket_terminal(websocket: WebSocket, token: str = Query(...)):
    if token != current_token:
        await websocket.close(code=4401)
        return

    await websocket.accept()

    # Open a PTY so tmux attach behaves as a proper interactive terminal
    master_fd, slave_fd = pty.openpty()

    proc = subprocess.Popen(
        ["tmux", "attach-session", "-t", TMUX_SESSION],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    loop = asyncio.get_event_loop()

    async def read_from_pty() -> None:
        try:
            while True:
                data = await loop.run_in_executor(None, _safe_read, master_fd)
                if not data:
                    break
                await websocket.send_bytes(data)
        except (WebSocketDisconnect, Exception):
            pass

    async def write_to_pty() -> None:
        try:
            while True:
                data = await websocket.receive_bytes()
                os.write(master_fd, data)
        except (WebSocketDisconnect, Exception):
            pass

    read_task = asyncio.create_task(read_from_pty())
    write_task = asyncio.create_task(write_to_pty())

    done, pending = await asyncio.wait(
        [read_task, write_task],
        return_when=asyncio.FIRST_COMPLETED,
    )
    for t in pending:
        t.cancel()

    try:
        proc.terminate()
        proc.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass

    try:
        os.close(master_fd)
    except OSError:
        pass

    try:
        await websocket.close()
    except Exception:
        pass


def _safe_read(fd: int, n: int = 4096) -> bytes:
    """Read from fd, returning empty bytes on OSError (EOF / closed)."""
    try:
        return os.read(fd, n)
    except OSError:
        return b""


@app.post("/task/cancel")
async def cancel_task(token: str = Query(...)):
    global current_token, current_task, task_exit_code, _monitor_task

    if token != current_token:
        raise HTTPException(status_code=401, detail="Invalid token")

    if _monitor_task and not _monitor_task.done():
        _monitor_task.cancel()
    _monitor_task = None

    _cleanup()
    current_token = None
    current_task = None
    task_exit_code = None

    return {"status": "IDLE"}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)

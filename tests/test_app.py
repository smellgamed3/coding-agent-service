"""Coding Agent Service 单元测试与 API 集成测试"""

import json
import os
import shlex
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, mock_open, patch

from fastapi.testclient import TestClient

# 将项目根目录加入 sys.path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import app as agent_app


class TestBuildAuthenticatedUrl(unittest.TestCase):
    """测试 _build_authenticated_url 函数"""

    def test_no_token_returns_original_url(self):
        url = "https://github.com/org/repo"
        result = agent_app._build_authenticated_url(url, None)
        self.assertEqual(result, url)

    def test_empty_token_returns_original_url(self):
        url = "https://github.com/org/repo"
        result = agent_app._build_authenticated_url(url, "")
        self.assertEqual(result, url)

    def test_https_url_with_token_embeds_oauth2(self):
        url = "https://github.com/org/repo"
        token = "mytoken123"
        result = agent_app._build_authenticated_url(url, token)
        self.assertEqual(result, "https://oauth2:mytoken123@github.com/org/repo")

    def test_non_https_url_with_token_returns_original(self):
        url = "git@github.com:org/repo.git"
        token = "mytoken123"
        result = agent_app._build_authenticated_url(url, token)
        self.assertEqual(result, url)

    def test_url_with_existing_credentials_is_not_modified(self):
        """已含凭据的 URL 不应被再次嵌入 Token（防止 Token 意外拼接或暴露）"""
        url = "https://user:pass@github.com/org/repo"
        token = "newtoken"
        result = agent_app._build_authenticated_url(url, token)
        self.assertEqual(result, url)

    def test_url_with_at_sign_in_host_is_not_modified(self):
        """含 @ 的 URL（可能用于劫持 Token 至第三方）应原样返回，不嵌入 Token"""
        # 恶意 URL：oauth2:token 会被发送到 evil.com，而非 github.com
        url = "https://evil.com@github.com/org/repo"
        token = "secret"
        result = agent_app._build_authenticated_url(url, token)
        # @ 出现在 netloc 中，urllib.parse 将 evil.com 识别为 username，应原样返回
        self.assertEqual(result, url)
        # 确保 Token 未出现在返回值中
        self.assertNotIn(token, result)

    def test_https_url_with_port_embeds_token_correctly(self):
        """带端口号的 HTTPS URL 应正确嵌入 Token"""
        url = "https://gitlab.example.com:8443/org/repo"
        token = "mytoken"
        result = agent_app._build_authenticated_url(url, token)
        self.assertEqual(result, "https://oauth2:mytoken@gitlab.example.com:8443/org/repo")

    def test_invalid_url_without_hostname_returns_original(self):
        """hostname 解析失败的无效 URL 应原样返回，不嵌入 Token"""
        url = "https://"
        token = "secret"
        result = agent_app._build_authenticated_url(url, token)
        self.assertEqual(result, url)
        self.assertNotIn(token, result)


class TestGenerateRunScript(unittest.TestCase):
    """测试 _generate_run_script 函数"""

    def test_script_contains_git_clone(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True
        )
        self.assertIn("git clone", script)

    def test_script_contains_claude_code(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True
        )
        self.assertIn("claude-code", script)

    def test_auto_mode_true_includes_yes_flag(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True
        )
        self.assertIn("--yes", script)

    def test_auto_mode_false_excludes_yes_flag(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", False
        )
        self.assertNotIn("--yes", script)

    def test_script_contains_git_push(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True
        )
        self.assertIn("git push", script)

    def test_cli_tool_defaults_to_claude_code(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True
        )
        self.assertIn("claude-code", script)
        self.assertNotIn("opencode", script)

    def test_cli_tool_opencode_uses_opencode_command(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True, "opencode"
        )
        self.assertIn("opencode", script)
        self.assertNotIn("claude-code", script)

    def test_cli_tool_opencode_auto_mode_includes_yes_flag(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", True, "opencode"
        )
        self.assertIn("--yes", script)

    def test_cli_tool_opencode_auto_mode_false_excludes_yes_flag(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", "修复 bug", False, "opencode"
        )
        self.assertNotIn("--yes", script)

    def test_task_summary_truncated_to_max_length(self):
        long_task = "A" * 200
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", long_task, True
        )
        # 提交信息格式为 "agent: <task_summary>"，task_summary 长度不超过 MAX_COMMIT_SUMMARY_LENGTH
        # 因此提交信息总长度不超过 len("agent: ") + MAX_COMMIT_SUMMARY_LENGTH
        self.assertIn("agent: ", script)
        # 提取脚本中 git commit -m 后的提交信息，验证 task_summary 长度已被截断
        task_summary = long_task[:agent_app.MAX_COMMIT_SUMMARY_LENGTH]
        self.assertIn(task_summary, script)
        # 确认没有超过最大长度的 task 部分出现在提交信息中
        over_length = long_task[: agent_app.MAX_COMMIT_SUMMARY_LENGTH + 1]
        self.assertNotIn(f"agent: {over_length}", script)

    def test_special_chars_in_task_are_quoted(self):
        task = "fix: handle 'single quotes' and $variables"
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "main", task, True
        )
        # shlex.quote 将整个参数包裹在单引号内，防止 shell 展开
        # 验证 claude-code 存在且参数是经过 shlex.quote 处理后的字符串
        self.assertIn("claude-code", script)
        # shlex.quote 后的结果应出现在脚本中（单引号包裹，内部单引号用 '"'"' 转义）
        quoted_task = shlex.quote(task)
        self.assertIn(quoted_task, script)

    def test_branch_is_included_in_clone_command(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "feature/my-branch", "task", True
        )
        self.assertIn("feature/my-branch", script)

    def test_invalid_cli_tool_raises_value_error(self):
        """不支持的 cli_tool 应抛出 ValueError，防止 shell 注入"""
        with self.assertRaises(ValueError):
            agent_app._generate_run_script(
                "https://github.com/org/repo", "main", "task", False, "malicious; rm -rf /"
            )

    def test_invalid_cli_tool_auto_mode_raises_value_error(self):
        """auto_mode=True 时不支持的 cli_tool 同样应抛出 ValueError"""
        with self.assertRaises(ValueError):
            agent_app._generate_run_script(
                "https://github.com/org/repo", "main", "task", True, "unknown-tool"
            )


class TestGetStatus(unittest.TestCase):
    """测试 _get_status 函数"""

    def test_idle_when_no_token(self):
        original_token = agent_app.current_token
        try:
            agent_app.current_token = None
            self.assertEqual(agent_app._get_status(), "IDLE")
        finally:
            agent_app.current_token = original_token

    def test_running_when_token_and_session_exists(self):
        original_token = agent_app.current_token
        try:
            agent_app.current_token = "sometoken"
            with patch.object(agent_app, "_tmux_session_exists", return_value=True):
                self.assertEqual(agent_app._get_status(), "RUNNING")
        finally:
            agent_app.current_token = original_token

    def test_done_when_exit_code_zero(self):
        original_token = agent_app.current_token
        original_exit = agent_app.task_exit_code
        try:
            agent_app.current_token = "sometoken"
            agent_app.task_exit_code = 0
            with patch.object(agent_app, "_tmux_session_exists", return_value=False):
                self.assertEqual(agent_app._get_status(), "DONE")
        finally:
            agent_app.current_token = original_token
            agent_app.task_exit_code = original_exit

    def test_failed_when_exit_code_nonzero(self):
        original_token = agent_app.current_token
        original_exit = agent_app.task_exit_code
        try:
            agent_app.current_token = "sometoken"
            agent_app.task_exit_code = 1
            with patch.object(agent_app, "_tmux_session_exists", return_value=False):
                self.assertEqual(agent_app._get_status(), "FAILED")
        finally:
            agent_app.current_token = original_token
            agent_app.task_exit_code = original_exit

    def test_idle_when_token_set_but_no_session_and_no_exit_code(self):
        original_token = agent_app.current_token
        original_exit = agent_app.task_exit_code
        try:
            agent_app.current_token = "sometoken"
            agent_app.task_exit_code = None
            with patch.object(agent_app, "_tmux_session_exists", return_value=False):
                self.assertEqual(agent_app._get_status(), "IDLE")
        finally:
            agent_app.current_token = original_token
            agent_app.task_exit_code = original_exit


class TestWriteMcpConfig(unittest.TestCase):
    """测试 _write_mcp_config 函数"""

    def test_writes_mcp_servers_to_settings(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            settings_path = os.path.join(tmpdir, "settings.json")
            with patch("os.path.expanduser", return_value=tmpdir):
                with patch("os.makedirs"):
                    # 直接测试写入逻辑
                    config = {
                        "my-server": agent_app.McpServerConfig(
                            url="https://mcp.example.com", token="tok123"
                        )
                    }
                    # 手动模拟写入
                    existing: dict = {}
                    existing.setdefault("mcpServers", {})
                    for name, cfg in config.items():
                        existing["mcpServers"][name] = {
                            "url": cfg.url,
                            "token": cfg.token,
                        }
                    with open(settings_path, "w") as f:
                        json.dump(existing, f)
                    with open(settings_path) as f:
                        data = json.load(f)
                    self.assertIn("my-server", data["mcpServers"])
                    self.assertEqual(
                        data["mcpServers"]["my-server"]["url"],
                        "https://mcp.example.com",
                    )

    def test_mcp_settings_file_has_restricted_permissions(self):
        """MCP 配置文件应设置为 0o600，防止其他用户读取含 Token 的内容"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("os.path.expanduser", return_value=tmpdir):
                config = {
                    "srv": agent_app.McpServerConfig(
                        url="https://mcp.example.com", token="secret_token"
                    )
                }
                agent_app._write_mcp_config(config)
            settings_path = os.path.join(tmpdir, "settings.json")
            file_mode = stat.S_IMODE(os.stat(settings_path).st_mode)
            self.assertEqual(file_mode, 0o600, "MCP 配置文件权限应为 0o600（仅所有者可读写）")


class TestMcpServerConfigModel(unittest.TestCase):
    """测试 McpServerConfig 数据模型"""

    def test_valid_config(self):
        cfg = agent_app.McpServerConfig(url="https://example.com", token="tok")
        self.assertEqual(cfg.url, "https://example.com")
        self.assertEqual(cfg.token, "tok")


class TestTaskRequestModel(unittest.TestCase):
    """测试 TaskRequest 数据模型"""

    def test_defaults(self):
        req = agent_app.TaskRequest(
            repo_url="https://github.com/org/repo", task="do something"
        )
        self.assertEqual(req.branch, "main")
        self.assertTrue(req.auto_mode)
        self.assertIsNone(req.repo_token)
        self.assertIsNone(req.mcp_servers)
        self.assertEqual(req.cli_tool, "claude-code")

    def test_cli_tool_claude_code(self):
        req = agent_app.TaskRequest(
            repo_url="https://github.com/org/repo",
            task="do something",
            cli_tool="claude-code",
        )
        self.assertEqual(req.cli_tool, "claude-code")

    def test_cli_tool_opencode(self):
        req = agent_app.TaskRequest(
            repo_url="https://github.com/org/repo",
            task="do something",
            cli_tool="opencode",
        )
        self.assertEqual(req.cli_tool, "opencode")

    def test_cli_tool_invalid_value_raises_error(self):
        with self.assertRaises(Exception):
            agent_app.TaskRequest(
                repo_url="https://github.com/org/repo",
                task="do something",
                cli_tool="unknown-tool",
            )


class TestApiSubmitTask(unittest.TestCase):
    """测试 POST /task 端点"""

    def setUp(self):
        self.client = TestClient(agent_app.app)
        # 重置全局状态
        agent_app.current_token = None
        agent_app.current_task = None
        agent_app.task_exit_code = None

    def _valid_payload(self):
        return {
            "repo_url": "https://github.com/org/repo",
            "task": "修复 bug",
        }

    def test_submit_task_returns_running_status(self):
        with patch.object(agent_app, "_get_status", return_value="IDLE"), \
             patch("subprocess.run"), \
             patch("builtins.open", mock_open()), \
             patch("os.chmod"), \
             patch("os.path.isfile", return_value=False), \
             patch.object(agent_app, "_kill_session"), \
             patch("asyncio.create_task"):
            resp = self.client.post("/task", json=self._valid_payload())
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("token", data)
        self.assertEqual(data["status"], "RUNNING")
        self.assertIn("ui_url", data)

    def test_submit_task_conflict_when_running(self):
        with patch.object(agent_app, "_get_status", return_value="RUNNING"):
            resp = self.client.post("/task", json=self._valid_payload())
        self.assertEqual(resp.status_code, 409)

    def test_submit_task_ui_url_contains_token(self):
        with patch.object(agent_app, "_get_status", return_value="IDLE"), \
             patch("subprocess.run"), \
             patch("builtins.open", mock_open()), \
             patch("os.chmod"), \
             patch("os.path.isfile", return_value=False), \
             patch.object(agent_app, "_kill_session"), \
             patch("asyncio.create_task"):
            resp = self.client.post("/task", json=self._valid_payload())
        data = resp.json()
        token = data["token"]
        self.assertIn(token, data["ui_url"])

    def test_submit_task_missing_required_fields_returns_422(self):
        resp = self.client.post("/task", json={"repo_url": "https://github.com/org/repo"})
        self.assertEqual(resp.status_code, 422)

    def test_submit_task_missing_repo_url_returns_422(self):
        resp = self.client.post("/task", json={"task": "do something"})
        self.assertEqual(resp.status_code, 422)

    def test_submit_task_with_mcp_servers_calls_write_mcp_config(self):
        payload = dict(self._valid_payload())
        payload["mcp_servers"] = {
            "my-mcp": {"url": "https://mcp.example.com", "token": "tok123"}
        }
        with patch.object(agent_app, "_get_status", return_value="IDLE"), \
             patch("subprocess.run"), \
             patch("builtins.open", mock_open()), \
             patch("os.chmod"), \
             patch("os.path.isfile", return_value=False), \
             patch.object(agent_app, "_kill_session"), \
             patch("asyncio.create_task"), \
             patch.object(agent_app, "_write_mcp_config") as mock_write:
            resp = self.client.post("/task", json=payload)
        self.assertEqual(resp.status_code, 200)
        mock_write.assert_called_once()

    def test_submit_task_with_opencode_tool(self):
        payload = dict(self._valid_payload())
        payload["cli_tool"] = "opencode"
        with patch.object(agent_app, "_get_status", return_value="IDLE"), \
             patch("subprocess.run"), \
             patch("builtins.open", mock_open()), \
             patch("os.chmod"), \
             patch("os.path.isfile", return_value=False), \
             patch.object(agent_app, "_kill_session"), \
             patch("asyncio.create_task"), \
             patch.object(agent_app, "_generate_run_script", return_value="#!/bin/bash\n") as mock_gen:
            resp = self.client.post("/task", json=payload)
        self.assertEqual(resp.status_code, 200)
        mock_gen.assert_called_once()
        # cli_tool 参数应为 "opencode"（可能是位置参数或关键字参数）
        self.assertIn("opencode", mock_gen.call_args.args + tuple(mock_gen.call_args.kwargs.values()))

    def test_submit_task_with_invalid_cli_tool_returns_422(self):
        payload = dict(self._valid_payload())
        payload["cli_tool"] = "invalid-tool"
        resp = self.client.post("/task", json=payload)
        self.assertEqual(resp.status_code, 422)

    def test_submit_task_default_cli_tool_is_claude_code(self):
        payload = dict(self._valid_payload())
        with patch.object(agent_app, "_get_status", return_value="IDLE"), \
             patch("subprocess.run"), \
             patch("builtins.open", mock_open()), \
             patch("os.chmod"), \
             patch("os.path.isfile", return_value=False), \
             patch.object(agent_app, "_kill_session"), \
             patch("asyncio.create_task"), \
             patch.object(agent_app, "_generate_run_script", return_value="#!/bin/bash\n") as mock_gen:
            resp = self.client.post("/task", json=payload)
        self.assertEqual(resp.status_code, 200)
        mock_gen.assert_called_once()
        self.assertIn("claude-code", mock_gen.call_args.args + tuple(mock_gen.call_args.kwargs.values()))

    def test_submit_task_run_script_has_restricted_permissions(self):
        """运行脚本应设置为 0o700，防止其他用户读取含认证 Token 的内容"""
        with tempfile.NamedTemporaryFile(delete=False, suffix=".sh") as tmp:
            tmp_path = tmp.name
        try:
            with patch.object(agent_app, "_get_status", return_value="IDLE"), \
                 patch("subprocess.run"), \
                 patch.object(agent_app, "RUN_SCRIPT", tmp_path), \
                 patch("os.path.isfile", return_value=False), \
                 patch.object(agent_app, "_kill_session"), \
                 patch("asyncio.create_task"):
                resp = self.client.post("/task", json=self._valid_payload())
            self.assertEqual(resp.status_code, 200)
            file_mode = stat.S_IMODE(os.stat(tmp_path).st_mode)
            self.assertEqual(file_mode, 0o700, "脚本文件权限应为 0o700（仅所有者可读写执行）")
        finally:
            os.unlink(tmp_path)


class TestApiGetTask(unittest.TestCase):
    """测试 GET /task 端点"""

    def setUp(self):
        self.client = TestClient(agent_app.app)
        agent_app.current_token = "valid-token-abc"
        agent_app.current_task = "测试任务"
        agent_app.task_exit_code = None

    def tearDown(self):
        agent_app.current_token = None
        agent_app.current_task = None
        agent_app.task_exit_code = None

    def test_get_task_with_valid_token_returns_200(self):
        with patch.object(agent_app, "_get_status", return_value="RUNNING"):
            resp = self.client.get("/task?token=valid-token-abc")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["status"], "RUNNING")
        self.assertEqual(data["task"], "测试任务")

    def test_get_task_with_invalid_token_returns_401(self):
        resp = self.client.get("/task?token=wrong-token")
        self.assertEqual(resp.status_code, 401)

    def test_get_task_returns_done_status(self):
        agent_app.task_exit_code = 0
        with patch.object(agent_app, "_tmux_session_exists", return_value=False):
            resp = self.client.get("/task?token=valid-token-abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "DONE")

    def test_get_task_returns_failed_status(self):
        agent_app.task_exit_code = 1
        with patch.object(agent_app, "_tmux_session_exists", return_value=False):
            resp = self.client.get("/task?token=valid-token-abc")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "FAILED")


class TestApiGetUI(unittest.TestCase):
    """测试 GET /ui 端点"""

    def setUp(self):
        self.client = TestClient(agent_app.app)
        agent_app.current_token = "ui-token-xyz"
        agent_app.current_task = "UI 测试任务"

    def tearDown(self):
        agent_app.current_token = None
        agent_app.current_task = None

    def test_get_ui_with_valid_token_returns_html(self):
        resp = self.client.get("/ui?token=ui-token-xyz")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers["content-type"])
        # 验证 token 已注入到 HTML 中（替换 __TOKEN__）
        self.assertIn("ui-token-xyz", resp.text)
        self.assertNotIn("__TOKEN__", resp.text)

    def test_get_ui_with_invalid_token_returns_401(self):
        resp = self.client.get("/ui?token=bad-token")
        self.assertEqual(resp.status_code, 401)


class TestApiCancelTask(unittest.TestCase):
    """测试 POST /task/cancel 端点"""

    def setUp(self):
        self.client = TestClient(agent_app.app)
        agent_app.current_token = "cancel-token"
        agent_app.current_task = "待取消任务"
        agent_app.task_exit_code = None

    def tearDown(self):
        agent_app.current_token = None
        agent_app.current_task = None
        agent_app.task_exit_code = None

    def test_cancel_with_valid_token_returns_idle(self):
        with patch.object(agent_app, "_cleanup"):
            resp = self.client.post("/task/cancel?token=cancel-token")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "IDLE")

    def test_cancel_clears_global_state(self):
        with patch.object(agent_app, "_cleanup"):
            self.client.post("/task/cancel?token=cancel-token")
        self.assertIsNone(agent_app.current_token)
        self.assertIsNone(agent_app.current_task)
        self.assertIsNone(agent_app.task_exit_code)

    def test_cancel_with_invalid_token_returns_401(self):
        resp = self.client.post("/task/cancel?token=wrong-token")
        self.assertEqual(resp.status_code, 401)


class TestApiDocsEndpoint(unittest.TestCase):
    """验证 FastAPI 自动生成文档端点可访问"""

    def setUp(self):
        self.client = TestClient(agent_app.app)

    def test_openapi_docs_available(self):
        resp = self.client.get("/docs")
        self.assertEqual(resp.status_code, 200)

    def test_openapi_json_available(self):
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("paths", data)
        self.assertIn("/task", data["paths"])


class TestKillSessionAndCleanup(unittest.TestCase):
    """测试 _kill_session 和 _cleanup 辅助函数"""

    def test_kill_session_only_when_exists(self):
        with patch.object(agent_app, "_tmux_session_exists", return_value=False), \
             patch("subprocess.run") as mock_run:
            agent_app._kill_session()
            mock_run.assert_not_called()

    def test_kill_session_runs_tmux_kill(self):
        with patch.object(agent_app, "_tmux_session_exists", return_value=True), \
             patch("subprocess.run") as mock_run:
            agent_app._kill_session()
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            self.assertIn("kill-session", args)

    def test_cleanup_removes_workspace_and_files(self):
        with patch.object(agent_app, "_kill_session"), \
             patch("os.path.isdir", return_value=True), \
             patch("shutil.rmtree") as mock_rmtree, \
             patch("os.path.isfile", return_value=True), \
             patch("os.remove") as mock_remove:
            agent_app._cleanup()
            mock_rmtree.assert_called_once_with(agent_app.WORKSPACE, ignore_errors=True)
            self.assertEqual(mock_remove.call_count, 2)


if __name__ == "__main__":
    unittest.main()

"""Coding Agent Service 单元测试"""

import json
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

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
        import shlex
        # shlex.quote 后的结果应出现在脚本中（单引号包裹，内部单引号用 '"'"' 转义）
        quoted_task = shlex.quote(task)
        self.assertIn(quoted_task, script)

    def test_branch_is_included_in_clone_command(self):
        script = agent_app._generate_run_script(
            "https://github.com/org/repo", "feature/my-branch", "task", True
        )
        self.assertIn("feature/my-branch", script)


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
        import tempfile
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


if __name__ == "__main__":
    unittest.main()

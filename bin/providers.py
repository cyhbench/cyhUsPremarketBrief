"""呼叫 Claude／Codex 產生報告本文。AI 只有 Brave 瀏覽工具；失敗一律丟 ProviderFailed。"""
import json
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path.home() / ".local/share/ai-bot-browser"))
import bot_browser  # noqa: E402  共用模組，只引用不修改

# 導覽與讀取所需的最小集合。不含 evaluate、upload、drop、run_code、screenshot、network_*。
BRAVE_TOOLS = ["browser_navigate", "browser_navigate_back", "browser_snapshot", "browser_find",
               "browser_click", "browser_type", "browser_press_key", "browser_wait_for",
               "browser_tabs", "browser_close"]
BRAVE_BLOCKED = ["browser_file_upload", "browser_drop", "browser_evaluate", "browser_run_code_unsafe",
                 "browser_take_screenshot", "browser_network_requests", "browser_network_request",
                 "browser_console_messages", "browser_fill_form", "browser_select_option",
                 "browser_drag", "browser_hover", "browser_handle_dialog", "browser_resize",
                 "browser_emulate_media"]
MAX_OUTPUT = 512 * 1024


class ProviderFailed(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def run_process(cmd, stdin_text, cwd, timeout):
    """跑子程序；逾時時殺整個程序群組（含它啟動的 MCP／Brave）。回傳 (rc, stdout)。"""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            cwd=cwd, text=True, start_new_session=True)
    try:
        out, _ = proc.communicate(stdin_text, timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise ProviderFailed("TIMEOUT")
    finally:
        try:
            os.killpg(proc.pid, signal.SIGKILL)  # 正常結束後殘留的子程序（MCP、Brave）
        except ProcessLookupError:
            pass
    if len(out) > MAX_OUTPUT:
        raise ProviderFailed("OUTPUT_TOO_LARGE")
    return proc.returncode, out


def claude_cmd():
    return ["claude", "-p", "--output-format", "json", "--tools", "",
            "--no-session-persistence", "--disable-slash-commands", "--setting-sources", "",
            *bot_browser.claude_options(blocked_tools=[f"mcp__brave__{t}" for t in BRAVE_BLOCKED]),
            "--allowedTools", *[f"mcp__brave__{t}" for t in BRAVE_TOOLS]]


def codex_cmd(out_file):
    opts = bot_browser.codex_options()
    # 共用模組的 disabled_tools 只封一項；這裡改為 enabled_tools 白名單加明確封鎖清單。
    flags = []
    for flag, value in zip(opts[::2], opts[1::2]):
        if not value.startswith("mcp_servers.brave.disabled_tools="):
            flags += [flag, value]
    extra = {"mcp_servers.brave.enabled_tools": BRAVE_TOOLS, "mcp_servers.brave.disabled_tools": BRAVE_BLOCKED,
             "features.shell_tool": False, "approval_policy": "never"}
    for k, v in extra.items():
        flags += ["-c", f"{k}={json.dumps(v)}"]
    return ["codex", "exec", "--ignore-user-config", "--skip-git-repo-check", "-s", "read-only",
            "--output-last-message", str(out_file), *flags, "-"]


def parse_claude(out):
    """只認 {"type":"result","is_error":false,"result":"..."}；其他結構一律當協定錯誤。"""
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        raise ProviderFailed("PROTOCOL_ERROR")
    if not isinstance(data, dict) or data.get("type") != "result" or not isinstance(data.get("result"), str):
        raise ProviderFailed("PROTOCOL_ERROR")
    if data.get("is_error"):
        raise ProviderFailed("REPORTED_ERROR")
    if not data["result"].strip():
        raise ProviderFailed("EMPTY")
    return data["result"]


def run_claude(prompt, timeout):
    with tempfile.TemporaryDirectory(prefix="brief-claude-") as cwd:
        rc, out = run_process(claude_cmd(), prompt, cwd, timeout)
    if rc != 0:
        raise ProviderFailed("EXIT_ERROR")
    return parse_claude(out)


def run_codex(prompt, timeout):
    with tempfile.TemporaryDirectory(prefix="brief-codex-") as cwd:
        out_file = Path(cwd) / "last-message.txt"
        rc, _ = run_process(codex_cmd(out_file), prompt, cwd, timeout)
        if rc != 0:
            raise ProviderFailed("EXIT_ERROR")
        text = out_file.read_text(encoding="utf-8") if out_file.exists() else ""
    if not text.strip():
        raise ProviderFailed("EMPTY")
    return text

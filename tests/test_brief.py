import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
from unittest import mock
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import briefrun  # noqa: E402
sys.path.insert(0, str(ROOT / "tests"))
import permission_canary as canary  # noqa: E402
import providers  # noqa: E402
import reportcheck  # noqa: E402
import tradingcal  # noqa: E402
from tradingcal import NY, ny_at  # noqa: E402

CAL = tradingcal.Calendar.load(ROOT / "data/nyse-calendar.json")
D = date(2026, 11, 3)  # 週二，一般交易日


def body(extra=""):
    return f"""## 1. 重要美國市場頭條
- 某事件一句話（[CNBC](https://www.cnbc.com/x)）{extra}
## 2. 今日美國經濟數據
今日無重大美國經濟數據公布
## 3. 亞太股市
日經 225 1 ｜ KOSPI 2 ｜ 台灣加權 3 ｜ 恆生 4 ｜ 上證 5 ｜ ASX 200 6
## 4. 美股期貨
S&P 500 1 ｜ Nasdaq 100 2 ｜ 道瓊 3
## 5. 美元指數
DXY 101.8
## 6. Mag 7
Apple 1 Microsoft 2 Alphabet 3 Amazon 4 NVIDIA 5 Meta 6 Tesla 7
## 7. 前一交易日復盤
無前一份報告，略過對照
## 資料來源
- CNBC：https://quote.cnbc.com/
"""


def full(d=D, producer="claude", early=None, at=None):
    at = at or ny_at(d, 9, 3)
    return reportcheck.header(d, producer, at, early) + body()


class CalendarTests(unittest.TestCase):
    def st(self, s):
        return CAL.status(date.fromisoformat(s))

    def test_status(self):
        self.assertEqual(self.st("2026-11-26"), "closed")
        self.assertEqual(self.st("2026-11-27"), "early")
        self.assertEqual(self.st("2026-11-28"), "closed")
        self.assertEqual(self.st("2026-07-03"), "closed")
        for d in ("2027-06-18", "2027-07-05", "2027-12-24"):
            self.assertEqual(self.st(d), "closed")
        self.assertEqual(self.st("2027-12-31"), "open")
        self.assertEqual(self.st("2026-11-03"), "open")

    def test_unknown_year_and_bad_data(self):
        with self.assertRaises(tradingcal.CalendarUnknown):
            self.st("2028-03-01")
        self.assertEqual(self.st("2028-03-04"), "closed")  # 週六不需要日曆
        good = json.loads((ROOT / "data/nyse-calendar.json").read_text())
        for mutate in (lambda g: g["years"]["2026"]["closed_dates"].append("2026-01-01"),
                       lambda g: g["years"]["2026"]["early_closes"].update({"2026-01-01": "13:00"}),
                       lambda g: g["years"]["2026"]["closed_dates"].append("2027-01-04"),
                       lambda g: g["years"]["2026"]["early_closes"].update({"2026-11-27": "09:00"}),
                       lambda g: g.update(timezone="UTC")):
            g = json.loads(json.dumps(good))
            mutate(g)
            with self.assertRaises(tradingcal.CalendarUnknown):
                tradingcal.Calendar(g)

    def test_previous_session(self):
        self.assertEqual(CAL.previous_session(date(2026, 11, 27)), date(2026, 11, 25))
        self.assertEqual(CAL.previous_session(date(2027, 1, 4)), date(2026, 12, 31))
        self.assertEqual(CAL.previous_session(date(2026, 11, 2)), date(2026, 10, 30))
        self.assertEqual(CAL.previous_session(date(2028, 1, 3)), date(2027, 12, 31))
        with self.assertRaises(tradingcal.CalendarUnknown):
            CAL.previous_session(date(2028, 1, 4))


class DstTests(unittest.TestCase):
    def test_nine_am_ny_in_taipei(self):
        for d, tpe in ((date(2026, 10, 30), "21:00"), (date(2026, 11, 2), "22:00"),
                       (date(2027, 3, 12), "22:00"), (date(2027, 3, 15), "21:00")):
            self.assertEqual(ny_at(d, 9, 0).astimezone(tradingcal.TPE).strftime("%H:%M"), tpe, d)

    def test_header_zone_name_and_taipei_date(self):
        h = reportcheck.header(date(2027, 3, 15), "codex", ny_at(date(2027, 3, 15), 9, 1), None)
        self.assertIn("美東 09:01 EDT／台灣 03-15 21:01", h)
        h = reportcheck.header(D, "claude", ny_at(D, 9, 1), None)
        self.assertIn("美東 09:01 EST／台灣 11-03 22:01", h)
        self.assertIn("開盤 09:30 ET（台灣 22:30）", h)


class ReportTests(unittest.TestCase):
    def bad(self, text, d=D, producer="claude", early=None):
        with self.assertRaises(reportcheck.ReportInvalid):
            reportcheck.validate(text, d, producer, early)

    def test_accepts_good_report_with_markdown_brackets(self):
        self.assertTrue(reportcheck.validate(full(), D, "claude"))

    def test_rejections(self):
        good = full()
        self.bad(good, producer="codex")
        self.bad(good, d=date(2026, 11, 4))
        self.bad(good.replace("## 4. 美股期貨\n", ""))
        self.bad(good.replace("## 5. 美元指數\n", "## 5. 美元指數\n## 5. 美元指數\n"))
        self.bad(good.replace("Tesla 7", ""))
        self.bad(good.replace("DXY 101.8", "DXY X.XX"))
        self.bad(good.replace("https://quote.cnbc.com/", "CNBC"))
        self.bad(good.replace("DXY 101.8", ""))
        self.bad(good + "\x00")
        self.bad(good + "字" * 60000)
        self.bad(good.replace("## 5. 美元指數", "```\n## 5. 美元指數\n```\n## 5. 美元指數\n```"))  # fence 內假標題 + 結構破壞

    def test_fence_heading_not_counted(self):
        text = full().replace("DXY 101.8", "```\n## 3. 亞太股市\n```\nDXY 101.8")
        self.assertTrue(reportcheck.validate(text, D, "claude"))

    def test_early_close_note(self):
        d = date(2026, 11, 27)
        t = reportcheck.header(d, "claude", ny_at(d, 9, 3), tradingcal.time(13, 0)) + body()
        self.assertTrue(reportcheck.validate(t, d, "claude", tradingcal.time(13, 0)))
        self.bad(full(d), d=d, early=tradingcal.time(13, 0))

    def test_clean_body_strips_preamble_only(self):
        self.assertTrue(reportcheck.clean_body("好的，以下是報告\n" + body()).startswith("## 1."))
        with self.assertRaises(reportcheck.ReportInvalid):
            reportcheck.clean_body("沒有標題")


class ProviderTests(unittest.TestCase):
    def test_parse_claude(self):
        ok = json.dumps({"type": "result", "is_error": False, "result": "text"})
        self.assertEqual(providers.parse_claude(ok), "text")
        for bad, code in ((json.dumps({"type": "result", "is_error": True, "result": "x"}), "REPORTED_ERROR"),
                          (json.dumps({"type": "result", "is_error": False, "result": " "}), "EMPTY"),
                          (json.dumps({"type": "other"}), "PROTOCOL_ERROR"), ("not json", "PROTOCOL_ERROR"),
                          (json.dumps([1]), "PROTOCOL_ERROR")):
            with self.assertRaises(providers.ProviderFailed) as cm:
                providers.parse_claude(bad)
            self.assertEqual(cm.exception.code, code)

    @staticmethod
    def codex_settings(cmd):
        pairs = [tuple(v.split("=", 1)) for f, v in zip(cmd, cmd[1:]) if f == "-c"]
        keys = [k for k, _ in pairs]
        assert len(keys) == len(set(keys)), f"-c 設定重複: {sorted(k for k in keys if keys.count(k) > 1)}"
        return {k: json.loads(v) for k, v in pairs}

    def test_codex_effective_config(self):
        cfg = self.codex_settings(providers.codex_cmd("/tmp/o"))  # 重複 key 會在這裡失敗
        self.assertIs(cfg["features.shell_tool"], False)
        self.assertEqual(cfg["approval_policy"], "never")
        self.assertEqual(cfg["mcp_servers.brave.enabled_tools"], providers.BRAVE_TOOLS)
        self.assertEqual(cfg["mcp_servers.brave.disabled_tools"], providers.BRAVE_BLOCKED)
        self.assertFalse(set(providers.BRAVE_TOOLS) & set(providers.BRAVE_BLOCKED))
        cmd = providers.codex_cmd("/tmp/o")
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertEqual(cmd.count("-s"), 1)
        self.assertIn("--ignore-user-config", cmd)
        with self.assertRaises(AssertionError):  # 反向控制：尾端追加相反設定要被抓到
            self.codex_settings(cmd + ["-c", "features.shell_tool=true"])

    def test_claude_effective_tools(self):
        c = providers.claude_cmd()
        self.assertEqual(c.count("--tools"), 1)
        self.assertEqual(c[c.index("--tools") + 1], "")
        self.assertEqual(c.count("--allowedTools"), 1)
        allowed = c[c.index("--allowedTools") + 1:]
        self.assertEqual(allowed, [f"mcp__brave__{t}" for t in providers.BRAVE_TOOLS])
        disallowed = c[c.index("--disallowedTools") + 1:c.index("--allowedTools")]
        self.assertIn("mcp__brave__browser_file_upload", disallowed)
        self.assertFalse(set(allowed) & set(disallowed))

    def test_timeout_kills_process_group(self):
        marker = Path(tempfile.mkdtemp()) / "pid"
        t0 = time.monotonic()
        with self.assertRaises(providers.ProviderFailed) as cm:
            providers.run_process(["sh", "-c", f"sleep 300 & echo $! > {marker}; wait"], "", "/tmp", 1)
        self.assertEqual(cm.exception.code, "TIMEOUT")
        self.assertLess(time.monotonic() - t0, 10)
        pid = int(marker.read_text())
        time.sleep(0.3)
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def test_run_process_output_ok_and_exit_code(self):
        self.assertEqual(providers.run_process(["sh", "-c", "cat; exit 3"], "hi", "/tmp", 5), (3, "hi"))


class Fx(unittest.TestCase):
    """本機 bare remote + clone；時間、AI、通知、掃描都用假的。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = Path(self.tmp.name)
        self.remote, self.repo, self.state = t / "remote.git", t / "work", t / "state"
        self.git("init", "-q", "--bare", "-b", "main", str(self.remote), cwd=t)
        self.git("clone", "-q", str(self.remote), str(self.repo), cwd=t)
        for k, v in (("user.name", "t"), ("user.email", "t@example.com")):
            self.git("config", k, v)
        (self.repo / "data").mkdir()
        (self.repo / "prompts").mkdir()
        for rel in ("data/nyse-calendar.json", "prompts/brief.md"):
            (self.repo / rel).write_text((ROOT / rel).read_text())
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("push", "-q", "-u", "origin", "main")
        self.now = ny_at(D, 9, 3).astimezone(timezone.utc)
        self.sent, self.calls = [], []
        self.deps = briefrun.Deps(clock=lambda: self.now, claude=self.claude, codex=self.codex,
                                  notify=lambda m: self.sent.append(m) or True, scan=lambda text: 0,
                                  pages=lambda d, text, until: True)

    def tearDown(self):
        self.tmp.cleanup()

    def git(self, *a, cwd=None):
        r = subprocess.run(["git", *a], cwd=cwd or self.repo, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r.stdout

    def claude(self, prompt, timeout):
        self.calls.append("claude")
        return body()

    def codex(self, prompt, timeout):
        self.calls.append("codex")
        return body()

    def run_at(self, d=D, **kw):
        return briefrun.run(self.repo, self.state, self.deps, d, **kw)

    def remote_log(self):
        return self.git("log", "--format=%s", "main", cwd=self.remote)


class RunTests(Fx):
    def test_publish_success(self):
        self.assertEqual(self.run_at()[0], "PUBLISHED")
        self.assertIn("brief: 2026-11-03 (claude)", self.remote_log())
        files = self.git("show", "--name-only", "--format=", "main", cwd=self.remote).split()
        self.assertEqual(sorted(files), ["reports/2026-11-03.md", "reports/index.json"])
        idx = json.loads(self.git("show", "main:reports/index.json", cwd=self.remote))
        self.assertEqual(idx["reports"][0]["date"], "2026-11-03")
        self.assertEqual(idx["reports"][0]["producer"], "claude")
        self.assertEqual(self.sent, [])
        self.assertEqual(self.calls, ["claude"])

    def test_closed_days_no_ai_no_notify_no_commit(self):
        for d in (date(2026, 11, 26), date(2026, 11, 28)):
            self.assertEqual(self.run_at(d)[0], "SKIPPED_CLOSED")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.sent, [])
        self.assertEqual(self.remote_log().split("\n")[0], "init")

    def test_early_close_day_publishes_with_note(self):
        d = date(2026, 11, 27)
        self.now = ny_at(d, 9, 3).astimezone(timezone.utc)
        self.assertEqual(self.run_at(d)[0], "PUBLISHED")
        self.assertIn("13:00 ET 收盤", (self.repo / "reports/2026-11-27.md").read_text())

    def test_claude_failure_falls_back_to_codex(self):
        def boom(prompt, timeout):
            self.calls.append("claude")
            raise providers.ProviderFailed("EXIT_ERROR")
        self.deps.claude = boom
        self.assertEqual(self.run_at()[0], "PUBLISHED")
        self.assertEqual(self.calls, ["claude", "codex"])
        self.assertIn("brief: 2026-11-03 (codex)", self.remote_log())
        self.assertIn("產生者：Codex", (self.repo / "reports/2026-11-03.md").read_text())
        self.assertEqual(self.sent, [])

    def test_invalid_claude_report_falls_back(self):
        self.deps.claude = lambda p, t: self.calls.append("claude") or "## 1. 重要美國市場頭條\n只有一節"
        self.assertEqual(self.run_at()[0], "PUBLISHED")
        self.assertEqual(self.calls, ["claude", "codex"])

    def test_both_fail_no_commit_one_notice(self):
        def boom(prompt, timeout):
            raise providers.ProviderFailed("EXIT_ERROR")
        self.deps.claude = self.deps.codex = boom
        status, _ = self.run_at()
        self.assertEqual(status, "FAILED")
        self.assertEqual(self.remote_log().split("\n")[0], "init")
        self.assertEqual(len(self.sent), 1)
        self.assertIn("2026-11-03", self.sent[0])
        self.assertIn("ALL_PROVIDERS_FAILED", self.sent[0])
        self.assertFalse((self.repo / "reports/2026-11-03.md").exists())

    def test_past_0925_no_commit(self):
        def slow(prompt, timeout):
            self.now = ny_at(D, 9, 26).astimezone(timezone.utc)
            return body()
        self.deps.claude = slow
        status, detail = self.run_at()
        self.assertEqual(status, "FAILED")
        self.assertIn("DEADLINE_EXCEEDED", detail)
        self.assertEqual(self.remote_log().split("\n")[0], "init")
        self.assertEqual(len(self.sent), 1)
        self.assertFalse((self.repo / "reports/2026-11-03.md").exists())

    def test_start_windows(self):
        self.now = ny_at(D, 8, 59, 59).astimezone(timezone.utc)
        self.assertEqual(self.run_at()[0], "SKIPPED_EARLY")
        self.now = ny_at(D, 9, 15).astimezone(timezone.utc)
        self.assertEqual(self.run_at()[0], "FAILED")
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.sent), 1)

    def test_codex_not_started_when_no_time_left(self):
        def late(prompt, timeout):
            self.now = ny_at(D, 9, 23, 30).astimezone(timezone.utc)
            raise providers.ProviderFailed("TIMEOUT")
        self.deps.claude = late
        self.assertEqual(self.run_at()[0], "FAILED")
        self.assertNotIn("codex", self.calls)

    def test_already_published_is_idempotent(self):
        self.run_at()
        self.calls.clear()
        self.assertEqual(self.run_at()[0], "ALREADY_PUBLISHED")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.remote_log().count("brief:"), 1)

    def test_report_without_index_entry_stops(self):
        (self.repo / "reports").mkdir(exist_ok=True)
        (self.repo / "reports/2026-11-03.md").write_text("x")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "stray")
        self.git("push", "-q")
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "STATE_UNKNOWN"))

    def test_dirty_worktree_stops(self):
        (self.repo / "stray.txt").write_text("x")
        self.assertEqual(self.run_at()[1].split(":")[0], "DIRTY_WORKTREE")
        self.assertEqual(self.calls, [])

    def test_push_rejected_keeps_local_commit_and_notifies(self):
        other = Path(self.tmp.name) / "other"
        self.git("clone", "-q", str(self.remote), str(other), cwd=self.tmp.name)
        self.git("config", "user.name", "t", cwd=other)
        self.git("config", "user.email", "t@example.com", cwd=other)

        def racing(prompt, timeout):  # AI 執行期間遠端被別人推進
            (other / "x.txt").write_text("x")
            self.git("add", "-A", cwd=other)
            self.git("commit", "-q", "-m", "other", cwd=other)
            self.git("push", "-q", cwd=other)
            return body()
        self.deps.claude = racing
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "PUSH_FAILED"))
        self.assertIn("brief: 2026-11-03", self.git("log", "--format=%s", "-1"))  # 沒有 reset
        self.assertNotIn("brief:", self.remote_log())
        self.assertEqual(len(self.sent), 1)
        self.assertIn("本機已有 commit", self.sent[0])
        self.assertEqual(self.run_at()[1].split(":")[0], "PULL_FAILED")  # 已分岔，不自動解

    def test_unpushed_local_commit_stops_next_run(self):
        (self.repo / "reports").mkdir(exist_ok=True)
        (self.repo / "reports/2026-11-02.md").write_text("old")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "brief: 2026-11-02 (claude)")
        self.assertEqual(self.run_at()[1].split(":")[0], "LOCAL_UNPUSHED")
        self.assertEqual(self.calls, [])

    def test_secret_scan_blocks(self):
        self.deps.scan = lambda text: 1
        self.assertEqual(self.run_at()[1].split(":")[0], "SECRET_SCAN")
        self.assertEqual(self.remote_log().split("\n")[0], "init")
        self.deps.scan = None
        self.assertEqual(self.run_at()[1].split(":")[0], "SECRET_SCAN")

    def test_unknown_calendar_year_notifies(self):
        status, detail = self.run_at(date(2028, 3, 1))
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "CALENDAR_UNKNOWN"))
        self.assertEqual(len(self.sent), 1)

    def test_notification_failure_is_swallowed(self):
        def bad(msg):
            raise OSError("down")
        self.deps.notify = bad
        self.deps.claude = self.deps.codex = lambda p, t: (_ for _ in ()).throw(providers.ProviderFailed("EXIT_ERROR"))
        self.assertEqual(self.run_at()[0], "FAILED")
        self.assertIn("NOTIFY_FAILED", (self.state / "2026-11-03.log").read_text())

    def test_dry_run_touches_nothing(self):
        status, detail = self.run_at(dry_run=True)
        self.assertEqual(status, "DRY_RUN_OK")
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertTrue((self.state / "dryrun/2026-11-03.md").exists())
        self.assertEqual(self.remote_log().split("\n")[0], "init")

    def test_baseline_and_gap_go_into_prompt(self):
        self.run_at()
        d2 = date(2026, 11, 5)  # 跳過 11-04：缺報
        self.now = ny_at(d2, 9, 3).astimezone(timezone.utc)
        seen = []
        self.deps.claude = lambda p, t: seen.append(p) or body()
        self.assertEqual(self.run_at(d2)[0], "PUBLISHED")
        p = seen[0]
        self.assertIn("前一份報告日期：2026-11-03", p)
        self.assertIn("2026-11-04", p.split("缺報的交易日：")[1].split("。")[0])
        self.assertIn("前一交易日：2026-11-04", p)
        self.assertNotIn("{{", p.split("<<<前一份報告開始")[0])

    def test_prompt_does_not_expand_placeholders_from_baseline(self):
        idx = {"schema_version": 1, "reports": [{"date": "2026-11-02", "generated_at": "x", "producer": "claude"}]}
        (self.repo / "reports").mkdir(exist_ok=True)
        (self.repo / "reports/2026-11-02.md").write_text("## 1. x\n{{REPORT_DATE}}\n")
        p = briefrun.build_prompt(self.repo, CAL, D, self.now, idx)
        self.assertIn("{{REPORT_DATE}}", p.split("<<<前一份報告開始")[1])


class FailureExitTests(Fx):
    """B1–B5、B7：全新 clone、啟動失敗、發布預算、通知回覆、Pages 確認。"""

    def reports_tracked(self):
        return self.git("ls-tree", "-r", "--name-only", "HEAD", "reports").strip()

    def test_fresh_clone_has_no_reports_dir_and_still_publishes(self):
        self.assertEqual(self.reports_tracked(), "")
        self.assertFalse((self.repo / "reports").exists())
        self.assertEqual(self.run_at()[0], "PUBLISHED")
        self.assertIn("2026-11-03", self.git("show", "main:reports/index.json", cwd=self.remote))
        self.assertIn("2026-11-03", self.git("show", "main:reports/2026-11-03.md", cwd=self.remote))

    def test_missing_cli_launches_fail_over_once(self):
        with mock.patch.object(providers, "claude_cmd", lambda: ["/nonexistent/claude"]):
            with self.assertRaises(providers.ProviderFailed) as cm:
                providers.run_claude("p", 5)
            self.assertEqual(cm.exception.code, "LAUNCH_ERROR")
            self.deps.claude = providers.run_claude  # 真正的 launcher
            self.assertEqual(self.run_at()[0], "PUBLISHED")
        self.assertEqual(self.calls, ["codex"])
        self.assertEqual(self.sent, [])

    def test_both_cli_missing_single_safe_notice(self):
        with mock.patch.object(providers, "claude_cmd", lambda: ["/nonexistent/claude"]), \
                mock.patch.object(providers, "codex_cmd", lambda out: ["/nonexistent/codex"]):
            self.deps.claude, self.deps.codex = providers.run_claude, providers.run_codex
            status, detail = self.run_at()
        self.assertEqual(status, "FAILED")
        self.assertIn("ALL_PROVIDERS_FAILED", detail)
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("/nonexistent", self.sent[0])
        self.assertIn("LAUNCH_ERROR", self.sent[0])

    def test_corrupt_index_and_missing_baseline_notify_once(self):
        (self.repo / "reports").mkdir()
        (self.repo / "reports/index.json").write_text("{not json")
        self.git("add", "-A"); self.git("commit", "-q", "-m", "bad"); self.git("push", "-q")
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "INDEX_INVALID"))
        self.assertEqual(len(self.sent), 1)
        # 清單列出前一份報告，但檔案不在
        idx = {"schema_version": 1, "reports": [{"date": "2026-11-02", "generated_at": "x", "producer": "claude"}]}
        (self.repo / "reports/index.json").write_text(json.dumps(idx))
        self.git("add", "-A"); self.git("commit", "-q", "-m", "idx"); self.git("push", "-q")
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "BASELINE_UNREADABLE"))
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(self.calls, [])

    def test_unexpected_exception_uses_failure_exit_without_raw_error(self):
        def boom(prompt, timeout):
            raise RuntimeError("secret-looking-detail")
        self.deps.claude = boom
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "UNEXPECTED_ERROR"))
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn("secret-looking-detail", self.sent[0])
        self.assertIn("RuntimeError", (self.state / "2026-11-03.log").read_text())

    def test_unexpected_exception_text_never_reaches_log(self):
        def boom(prompt, timeout):
            raise RuntimeError("MARKER-NOT-A-REAL-SECRET\x1b[31m")
        self.deps.claude = boom
        self.run_at()
        log = (self.state / "2026-11-03.log").read_text()
        self.assertIn("UNEXPECTED RuntimeError", log)  # 正向：型別仍記錄
        self.assertNotIn("MARKER-NOT-A-REAL-SECRET", log)
        self.assertNotIn("\x1b", log)

    def test_service_outlives_wrapper_and_real_fallback_after_claude_timeout(self):
        unit = (ROOT / "deploy/cyh-us-premarket-brief.service").read_text()
        minutes = int(re.search(r"^TimeoutStartSec=(\d+)min", unit, re.M).group(1))
        dl = briefrun.deadlines(D)
        # 包裝器最晚結束：Pages 確認 09:30 再留 2 分鐘清理與通知
        self.assertGreaterEqual(minutes * 60, (dl["pages"] - dl["start"]).total_seconds() + 120)
        # 真實程序：Claude 卡住到 09:15 被殺 → Codex 仍能完成並發布（以縮短的時窗模擬，不經 systemd）
        report = Path(self.tmp.name) / "r.md"
        report.write_text(body())
        base, t0 = ny_at(D, 9, 14, 56), time.monotonic()
        self.deps.clock = lambda: (base + timedelta(seconds=time.monotonic() - t0)).astimezone(timezone.utc)
        self.deps.claude, self.deps.codex = providers.run_claude, providers.run_codex
        with mock.patch.object(briefrun, "MIN_PROVIDER_SECONDS", 1), \
                mock.patch.object(providers, "claude_cmd", lambda: ["sh", "-c", "sleep 300"]), \
                mock.patch.object(providers, "codex_cmd", lambda out: ["sh", "-c", f"cat >/dev/null; cat {report} > {out}"]):
            t = time.monotonic()
            status, _ = self.run_at()
        self.assertEqual(status, "PUBLISHED")
        self.assertLess(time.monotonic() - t, 30)
        self.assertIn("brief: 2026-11-03 (codex)", self.remote_log())
        self.assertIn("claude 失敗：claude:TIMEOUT", (self.state / "2026-11-03.log").read_text())

    def test_no_push_when_commit_finishes_after_0924_30(self):
        real_git = briefrun.git
        pushes = []

        def fake_git(repo, *a, timeout=60):
            if a[0] == "push":
                pushes.append(timeout)
            r = real_git(repo, *a, timeout=timeout)
            if a[0] == "commit":
                self.now = ny_at(D, 9, 24, 59).astimezone(timezone.utc)
            return r
        with mock.patch.object(briefrun, "git", fake_git):
            status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "PUSH_SKIPPED_LATE"))
        self.assertEqual(pushes, [])
        self.assertEqual(len(self.sent), 1)

    def test_scan_crossing_hard_deadline_writes_no_report_files(self):
        def late_claude(prompt, timeout):
            self.now = ny_at(D, 9, 24, 29).astimezone(timezone.utc)
            return body()

        def slow_scan(text):
            self.now += timedelta(seconds=32)
            return 0
        self.deps.claude, self.deps.scan = late_claude, slow_scan
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "DEADLINE_EXCEEDED"))
        self.assertFalse((self.repo / f"reports/{D.isoformat()}.md").exists())
        self.assertFalse((self.repo / "reports/index.json").exists())
        self.assertEqual(self.git("status", "--porcelain"), "")  # 不留髒工作目錄，下次不會被 DIRTY_WORKTREE 擋
        self.assertEqual(len(self.sent), 1)

    def test_git_timeouts_never_exceed_remaining_budget(self):
        real_git, seen = briefrun.git, {}

        def fake_git(repo, *a, timeout=60):
            seen[a[0]] = timeout
            return real_git(repo, *a, timeout=timeout)
        self.now = ny_at(D, 9, 24, 10).astimezone(timezone.utc)  # 直接呼叫 publish，驗證各步驟 timeout 以距 09:25 為上限
        self.state.mkdir()
        with mock.patch.object(briefrun, "git", fake_git):
            briefrun.publish(self.repo, D, "claude", full(), self.deps, briefrun.deadlines(D), self.state / "x.log")
        for cmd in ("add", "commit", "push"):
            self.assertEqual(seen[cmd], 50, cmd)  # 剩 50 秒到 09:25，不得加寬（舊版 push 會是 60 以內加 20）

    def test_push_ok_but_remote_not_at_head_is_unconfirmed(self):
        real_git = briefrun.git

        def fake_git(repo, *a, timeout=60):
            if a[0] == "ls-remote":
                return subprocess.CompletedProcess(a, 0, "0" * 40 + "\trefs/heads/main\n", "")
            return real_git(repo, *a, timeout=timeout)
        with mock.patch.object(briefrun, "git", fake_git):
            status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "PUSH_UNCONFIRMED"))
        self.assertEqual(len(self.sent), 1)

    def test_pages_not_confirmed_is_reported_as_pushed_not_unpublished(self):
        self.deps.pages = lambda d, text, until: False
        status, detail = self.run_at()
        self.assertEqual((status, detail.split(":")[0]), ("FAILED", "PAGES_NOT_CONFIRMED"))
        self.assertIn("brief: 2026-11-03", self.remote_log())  # 確實已推
        self.assertEqual(len(self.sent), 1)
        self.assertIn("不是未發布", self.sent[0])
        log = (self.state / "2026-11-03.log").read_text()
        self.assertIn("PUSH_CONFIRMED", log)
        self.assertNotIn("PAGES_VISIBLE", log)
        self.deps.pages = None  # 未設定確認器也不能當成功
        self.assertEqual(self.run_at()[0], "ALREADY_PUBLISHED")

    def test_notify_false_is_logged_once_without_retry(self):
        calls = []
        self.deps.notify = lambda m: calls.append(m) and False
        self.deps.claude = self.deps.codex = lambda p, t: (_ for _ in ()).throw(providers.ProviderFailed("EXIT_ERROR"))
        self.assertEqual(self.run_at()[0], "FAILED")
        self.assertEqual(len(calls), 1)
        self.assertIn("NOTIFY_FAILED API_NOT_OK", (self.state / "2026-11-03.log").read_text())


class FakeResp:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self.payload if isinstance(self.payload, bytes) else self.payload.encode()


class SenderAndPagesTests(unittest.TestCase):
    def post(self, payload=None, exc=None):
        def opener(req, timeout):
            self.assertEqual(timeout, 10)
            if exc:
                raise exc
            return FakeResp(payload)
        return briefrun.telegram_post("TOKEN", 1, "hi", urlopen=opener)

    def test_telegram_post_ok_notok_timeout(self):
        self.assertTrue(self.post('{"ok": true}'))
        self.assertFalse(self.post('{"ok": false, "description": "x"}'))
        self.assertFalse(self.post('{}'))
        with self.assertRaises(TimeoutError):
            self.post(exc=TimeoutError())

    def pages(self, serve, until_s=40):
        d, now = D, [ny_at(D, 9, 25)]
        sleeps = []

        def opener(req, timeout):
            r = serve(req.full_url.split("?")[0].replace(briefrun.PAGES_URL, ""), len(sleeps))
            if r is None:
                raise urllib.error.HTTPError(req.full_url, 404, "nf", {}, None)
            return FakeResp(r)

        def sleep(n):
            sleeps.append(n)
            now[0] += timedelta(seconds=n)
        ok = briefrun.pages_visible(d, "REPORT", now[0] + timedelta(seconds=until_s), urlopen=opener,
                                    clock=lambda: now[0], sleep=sleep)
        return ok, len(sleeps)

    def test_pages_visible_when_listed_and_content_equal(self):
        idx = json.dumps({"reports": [{"date": "2026-11-03"}]})
        ok, n = self.pages(lambda p, i: idx if p.endswith("json") else "REPORT")
        self.assertEqual((ok, n), (True, 0))

    def test_pages_waits_for_deploy_then_succeeds(self):
        idx = json.dumps({"reports": [{"date": "2026-11-03"}]})
        ok, n = self.pages(lambda p, i: None if i < 2 else (idx if p.endswith("json") else "REPORT"))
        self.assertEqual((ok, n), (True, 2))

    def test_pages_success_after_deadline_is_false(self):
        idx = json.dumps({"reports": [{"date": "2026-11-03"}]})
        now, timeouts = [ny_at(D, 9, 29, 59)], []

        def opener(req, timeout):
            timeouts.append(timeout)
            now[0] += timedelta(seconds=8)  # 請求本身耗時
            return FakeResp(idx if req.full_url.split("?")[0].endswith("json") else "REPORT")
        until = ny_at(D, 9, 30)
        ok = briefrun.pages_visible(D, "REPORT", until, urlopen=opener, clock=lambda: now[0], sleep=lambda n: None)
        self.assertFalse(ok)
        self.assertLessEqual(timeouts[0], 1)  # 第一次請求 timeout 已扣掉剩餘預算

    def test_pages_already_past_deadline_makes_no_request(self):
        calls = []
        ok = briefrun.pages_visible(D, "REPORT", ny_at(D, 9, 30), urlopen=lambda *a, **k: calls.append(1),
                                    clock=lambda: ny_at(D, 9, 30, 1), sleep=lambda n: None)
        self.assertEqual((ok, calls), (False, []))

    def test_pages_stale_or_missing_or_mismatched_content_is_false(self):
        old = json.dumps({"reports": [{"date": "2026-11-02"}]})
        new = json.dumps({"reports": [{"date": "2026-11-03"}]})
        self.assertFalse(self.pages(lambda p, i: old if p.endswith("json") else "REPORT")[0])  # 過期清單
        self.assertFalse(self.pages(lambda p, i: None)[0])  # 404（Pages 未開）
        self.assertFalse(self.pages(lambda p, i: new if p.endswith("json") else "OLD")[0])  # 內容不符
        self.assertFalse(self.pages(lambda p, i: "garbage")[0])


class OutputLimitTests(unittest.TestCase):
    def test_runaway_stdout_is_capped_without_buffering_all(self):
        t = time.monotonic()
        with self.assertRaises(providers.ProviderFailed) as cm:
            providers.run_process(["yes"], "", "/tmp", 30)
        self.assertEqual(cm.exception.code, "OUTPUT_TOO_LARGE")
        self.assertLess(time.monotonic() - t, 10)

    def test_oversize_codex_file_rejected_before_reading(self):
        def fake_cmd(out):
            return ["sh", "-c", f"head -c {providers.MAX_OUTPUT + 1} /dev/zero | tr '\\0' a > {out}"]
        with mock.patch.object(providers, "codex_cmd", fake_cmd):
            with self.assertRaises(providers.ProviderFailed) as cm:
                providers.run_codex("p", 10)
        self.assertEqual(cm.exception.code, "OUTPUT_TOO_LARGE")

    def test_exact_limit_is_accepted(self):
        rc, out = providers.run_process(["sh", "-c", f"head -c {providers.MAX_OUTPUT} /dev/zero | tr '\\0' a"], "", "/tmp", 10)
        self.assertEqual((rc, len(out)), (0, providers.MAX_OUTPUT))


class CanaryJudgeTests(unittest.TestCase):
    """permission_canary 的判準：用合成事件串流驗證「該過的過、該擋的擋」，不耗額度。"""

    @staticmethod
    def claude_stream(tools=None, snapshot_denied=True, nav_ok=True, extra=()):
        tools = sorted(canary.ALLOWED) if tools is None else tools
        ev = [{"type": "system", "subtype": "init", "tools": tools}]
        steps = [("browser_snapshot", not snapshot_denied, "Error: File access denied: /x is outside allowed roots. Allowed roots: /t"),
                 ("browser_navigate", nav_ok, "ok")] + list(extra)
        for i, (name, ok, text) in enumerate(steps):
            ev.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "id": f"t{i}", "name": f"mcp__brave__{name}", "input": {}}]}})
            ev.append({"type": "user", "message": {"content": [{"type": "tool_result", "tool_use_id": f"t{i}", "is_error": not ok, "content": text}]}})
        return "\n".join(json.dumps(e) for e in ev)

    def test_claude_pass_and_each_failure_is_caught(self):
        self.assertEqual(canary.evaluate("claude", self.claude_stream())[0], [])
        self.assertTrue(canary.evaluate("claude", self.claude_stream(tools=sorted(canary.ALLOWED) + ["Bash"]))[0])
        self.assertTrue(canary.evaluate("claude", self.claude_stream(snapshot_denied=False))[0])  # 工具沒擋
        self.assertTrue(canary.evaluate("claude", self.claude_stream(nav_ok=False))[0])  # 報價沒取到
        self.assertTrue(canary.evaluate("claude", self.claude_stream(extra=[("browser_file_upload", True, "done")]))[0])
        self.assertTrue(canary.evaluate("claude", self.claude_stream(), canary_exists=True)[0])
        self.assertTrue(canary.evaluate("claude", "")[0])  # 沒有任何事件（模型沒嘗試／provider 失敗）不得算通過

    def test_codex_shell_item_fails(self):
        def item(it):
            return json.dumps({"type": "item.completed", "item": it})
        snap = {"type": "mcp_tool_call", "server": "brave", "tool": "browser_snapshot", "status": "failed",
                "result": {"content": [{"type": "text", "text": "File access denied: outside allowed roots"}]}}
        nav = {"type": "mcp_tool_call", "server": "brave", "tool": "browser_navigate", "status": "completed", "result": {"content": []}}
        good = "\n".join([item(snap), item(nav)])
        self.assertEqual(canary.evaluate("codex", good)[0], [])
        self.assertTrue(canary.evaluate("codex", good + "\n" + item({"type": "command_execution"}))[0])
        other = {"type": "mcp_tool_call", "server": "other", "tool": "read_file", "status": "completed", "result": {"content": []}}
        self.assertTrue(canary.evaluate("codex", good + "\n" + item(other))[0])  # 白名單外 server 的成功呼叫
        self.assertTrue(canary.evaluate("codex", good + "\n" + item(dict(nav, server="other")))[0])  # 同名工具但 server 不對
        self.assertTrue(canary.evaluate("codex", good + "\n" + item(dict(nav, tool="read_file")))[0])  # brave 但不在白名單
        self.assertTrue(canary.evaluate("codex", item(nav))[0])  # 沒嘗試越界寫入 = 未驗證


class NotifyAndScanTests(unittest.TestCase):
    def test_scanner_hits_fake_token_and_not_plain_text(self):
        fake = "123456789:" + "A" * 35
        self.assertGreaterEqual(briefrun.secret_hits(f"x {fake} y"), 1)
        self.assertEqual(briefrun.secret_hits("美股盤前 https://www.cnbc.com/ 101.8"), 0)


if __name__ == "__main__":
    unittest.main()

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
import briefrun  # noqa: E402
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

    def test_commands_have_no_shell_or_upload(self):
        c = providers.claude_cmd()
        self.assertEqual(c[c.index("--tools") + 1], "")
        allowed = c[c.index("--allowedTools") + 1:]
        self.assertNotIn("mcp__brave__browser_file_upload", allowed)
        self.assertIn("mcp__brave__browser_file_upload", c)  # 在 disallowedTools
        x = " ".join(providers.codex_cmd("/tmp/o"))
        self.assertIn("features.shell_tool=false", x)
        self.assertIn("read-only", x)
        self.assertIn('enabled_tools=["browser_navigate"', x)
        self.assertEqual(x.count("disabled_tools"), 1)

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
        (self.repo / "reports").mkdir()
        for rel in ("data/nyse-calendar.json", "prompts/brief.md"):
            (self.repo / rel).write_text((ROOT / rel).read_text())
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "init")
        self.git("push", "-q", "-u", "origin", "main")
        self.now = ny_at(D, 9, 3).astimezone(timezone.utc)
        self.sent, self.calls = [], []
        self.deps = briefrun.Deps(clock=lambda: self.now, claude=self.claude, codex=self.codex,
                                  notify=lambda m: self.sent.append(m), scan=lambda text: 0)

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
        (self.repo / "reports/2026-11-02.md").write_text("## 1. x\n{{REPORT_DATE}}\n")
        p = briefrun.build_prompt(self.repo, CAL, D, self.now, idx)
        self.assertIn("{{REPORT_DATE}}", p.split("<<<前一份報告開始")[1])


class NotifyAndScanTests(unittest.TestCase):
    def test_scanner_hits_fake_token_and_not_plain_text(self):
        fake = "123456789:" + "A" * 35
        self.assertGreaterEqual(briefrun.secret_hits(f"x {fake} y"), 1)
        self.assertEqual(briefrun.secret_hits("美股盤前 https://www.cnbc.com/ 101.8"), 0)


if __name__ == "__main__":
    unittest.main()

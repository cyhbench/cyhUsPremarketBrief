"""盤前快報包裝器：判斷交易日 → AI 產生 → 檢查 → commit／push；失敗通知一次。"""
import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import providers
import reportcheck
from tradingcal import NY, TPE, Calendar, CalendarUnknown, ny_at

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = Path.home() / ".local/state/cyhUsPremarketBrief"
CONFIG = Path.home() / ".config/cyhUsPremarketBrief/config.json"
BOT_DIR = Path.home() / "projects/telegram-codex-bot"
GIT_TIMEOUT = 60
MIN_PROVIDER_SECONDS = 60


class Failure(Exception):
    """流程失敗：code 是穩定原因碼，message 是可對外的安全摘要（不含原始錯誤）。"""

    def __init__(self, code, message):
        super().__init__(code)
        self.code, self.message = code, message


@dataclass
class Deps:
    clock: callable = lambda: datetime.now(timezone.utc)
    claude: callable = providers.run_claude
    codex: callable = providers.run_codex
    notify: callable = None
    scan: callable = None


# ---------- 時間 ----------
def deadlines(d):
    return {"start": ny_at(d, 9, 0), "claude": ny_at(d, 9, 15), "codex": ny_at(d, 9, 24),
            "publish": ny_at(d, 9, 24, 30), "hard": ny_at(d, 9, 25)}


def simulated_clock(d):
    """測試用：從 d 的美東 09:00:05 開始，隨真實經過時間前進。"""
    base, t0 = ny_at(d, 9, 0, 5), time.monotonic()
    return lambda: base + timedelta(seconds=time.monotonic() - t0)


# ---------- git ----------
def git(repo, *args, timeout=GIT_TIMEOUT):
    try:
        r = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failure("GIT_TIMEOUT", f"git {args[0]} 逾時")
    return r


def git_ok(repo, *args, code, message, timeout=GIT_TIMEOUT):
    r = git(repo, *args, timeout=timeout)
    if r.returncode != 0:
        raise Failure(code, message)
    return r.stdout


# ---------- 報告清單 ----------
def load_index(repo):
    p = repo / "reports/index.json"
    if not p.exists():
        return {"schema_version": 1, "reports": []}
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1 or not isinstance(data.get("reports"), list):
        raise Failure("INDEX_INVALID", "reports/index.json 格式不符")
    return data


def write_atomic(path, text):
    with tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8") as f:
        f.write(text)
    os.replace(f.name, path)


def new_index(index, d, producer, generated_at):
    entries = [e for e in index["reports"] if e["date"] != d.isoformat()]
    entries.append({"date": d.isoformat(), "generated_at": generated_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "producer": producer})
    entries.sort(key=lambda e: e["date"], reverse=True)
    return {"schema_version": 1, "reports": entries}


# ---------- 提示詞 ----------
def ff_day(d):
    return f"{d:%b}{d.day}.{d.year}".lower()


def build_prompt(repo, cal, d, now, index):
    status = cal.status(d)
    prev, gap = None, ""
    try:
        prev = cal.previous_session(d)
    except CalendarUnknown:
        pass
    base = next((e for e in index["reports"] if e["date"] < d.isoformat()), None)
    body = "（無前一份報告）"
    if base:
        text = (repo / f"reports/{base['date']}.md").read_text(encoding="utf-8")
        body = text[text.find("## 1."):] if "## 1." in text else text
        bd = date.fromisoformat(base["date"])
        if prev and bd != prev:
            missing, cur = [], bd + timedelta(days=1)
            while cur <= prev:
                if cal.status(cur) != "closed":
                    missing.append(cur.isoformat())
                cur += timedelta(days=1)
            gap = f"它不是上一個交易日的報告，中間缺報的交易日：{'、'.join(missing)}。復盤時註明報告日期與缺報日。"
        elif not prev:
            gap = "日曆覆蓋不足，無法確認上一交易日與缺報日。"
    prev_urls = "日曆覆蓋不足，無法確認"
    if prev:
        days = [prev, prev + timedelta(days=1)]
        prev_urls = "、".join(f"https://www.forexfactory.com/calendar?day={ff_day(x)}" for x in days)
    early = cal.early_close(d)
    values = {
        "REPORT_DATE": d.isoformat(), "WEEKDAY": reportcheck.WEEKDAYS[d.weekday()],
        "NOW_NY": now.astimezone(NY).strftime("%H:%M %Z"), "OPEN_TPE": ny_at(d, 9, 30).astimezone(TPE).strftime("%H:%M"),
        "EARLY_CLOSE_NOTE": f"今天是早收日，{early:%H:%M} ET 收盤。" if status == "early" else "",
        "PREV_SESSION": prev.isoformat() if prev else "日曆覆蓋不足，無法確認上一交易日",
        "BASELINE_DATE": base["date"] if base else "無", "GAP_NOTE": gap,
        "FF_TODAY": ff_day(d), "FF_PREV_URLS": prev_urls, "BASELINE_BODY": body,
    }
    template = (repo / "prompts/brief.md").read_text(encoding="utf-8")
    # 一次替換，避免前一份報告內容裡的 {{…}} 被當成範本再展開
    return re.sub(r"\{\{(\w+)\}\}", lambda m: values.get(m.group(1), m.group(0)), template)


# ---------- 通知與秘密掃描 ----------
def telegram_notify(text):
    """送一則訊息給設定檔中的 chat；成功回 True。只試一次。"""
    sys.path.insert(0, str(BOT_DIR))
    import secret_guard
    token = secret_guard.secret("TELEGRAM_CLAUDE_BOT_TOKEN")
    chat_id = json.loads(CONFIG.read_text())["chat_id"]
    data = urllib.parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp).get("ok") is True


def secret_hits(text):
    sys.path.insert(0, str(BOT_DIR))
    import secret_guard
    return secret_guard.GUARD.redact(text)[1]


# ---------- 主流程 ----------
def log_line(log, msg):
    with open(log, "a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc):%Y-%m-%dT%H:%M:%SZ} {msg}\n")


def generate(prompt, d, deps, dl, log, early):
    """依序試 Claude、Codex；回傳 (producer, 完整報告)。全部失敗丟 Failure。"""
    reasons = []
    for name, fn, limit in (("claude", deps.claude, dl["claude"]), ("codex", deps.codex, dl["codex"])):
        remaining = (limit - deps.clock()).total_seconds()
        if remaining < MIN_PROVIDER_SECONDS:
            reasons.append(f"{name}:DEADLINE")
            log_line(log, f"{name} 略過：剩餘 {remaining:.0f} 秒")
            continue
        started = deps.clock()
        try:
            body = reportcheck.clean_body(fn(prompt, remaining))
            full = reportcheck.header(d, name, deps.clock(), early) + body
            reportcheck.validate(full, d, name, early)
        except providers.ProviderFailed as e:
            reasons.append(f"{name}:{e.code}")
        except reportcheck.ReportInvalid as e:
            reasons.append(f"{name}:REPORT_INVALID")
            log_line(log, f"{name} 格式檢查失敗：{e}")
        else:
            log_line(log, f"{name} 成功，耗時 {(deps.clock() - started).total_seconds():.0f} 秒")
            return name, full
        log_line(log, f"{name} 失敗：{reasons[-1]}")
    code = "DEADLINE_EXCEEDED" if all(r.endswith("DEADLINE") for r in reasons) else "ALL_PROVIDERS_FAILED"
    raise Failure(code, "；".join(reasons))


def publish(repo, d, producer, full, deps, dl, log):
    if deps.clock() >= dl["publish"]:
        raise Failure("DEADLINE_EXCEEDED", "已超過發布期限（09:24:30），不發布")
    if deps.scan is None or deps.scan(full):
        raise Failure("SECRET_SCAN", "秘密掃描發現問題或掃描器不可用，不發布")
    report = repo / f"reports/{d.isoformat()}.md"
    with open(report, "x", encoding="utf-8") as f:  # 不覆寫
        f.write(full)
    now = deps.clock()
    write_atomic(repo / "reports/index.json", json.dumps(new_index(load_index(repo), d, producer, now), ensure_ascii=False, indent=2) + "\n")
    git_ok(repo, "add", "--", f"reports/{d.isoformat()}.md", "reports/index.json", code="GIT_ADD", message="git add 失敗")
    staged = sorted(git_ok(repo, "diff", "--cached", "--name-only", code="GIT_DIFF", message="git diff 失敗").split())
    if staged != sorted([f"reports/{d.isoformat()}.md", "reports/index.json"]):
        raise Failure("UNEXPECTED_STAGED", "暫存區含預期外的檔案")
    if deps.clock() >= dl["hard"]:
        raise Failure("DEADLINE_EXCEEDED", "已超過 09:25，不 commit")
    git_ok(repo, "commit", "-q", "-m", f"brief: {d.isoformat()} ({producer})", code="GIT_COMMIT", message="git commit 失敗")
    log_line(log, "已 commit（本機）")
    if deps.clock() >= dl["hard"]:
        raise Failure("PUSH_SKIPPED_LATE", "本機已有 commit，超過 09:25 未推送")
    r = git(repo, "push", "-q", timeout=max(5, min(60, int((dl["hard"] - deps.clock()).total_seconds()) + 20)))
    if r.returncode != 0:
        raise Failure("PUSH_FAILED", "本機已有 commit，未確認推送成功")
    log_line(log, "PUSH_CONFIRMED")


def run(repo, state_dir, deps, report_date=None, dry_run=False):
    """回傳 (狀態, 說明)。狀態：PUBLISHED／DRY_RUN_OK／SKIPPED_*／ALREADY_PUBLISHED／FAILED。"""
    state_dir.mkdir(parents=True, exist_ok=True)
    d = report_date or deps.clock().astimezone(NY).date()
    log = state_dir / f"{d.isoformat()}.log"
    lock = open(state_dir / "lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return "SKIPPED_LOCKED", "另一個執行中"
    try:
        return _run_locked(repo, state_dir, deps, d, dry_run, log)
    except Failure as e:
        log_line(log, f"FAILED {e.code} {e.message}")
        if not dry_run and deps.notify:
            try:
                deps.notify(f"⚠️ 美股盤前快報 {d.isoformat()} 未發布：{e.message}（{e.code}）\n記錄：{log}")
            except Exception as ne:  # 通知失敗只記錄，不重試、不遞迴通知
                log_line(log, f"NOTIFY_FAILED {type(ne).__name__}")
        return "FAILED", f"{e.code}: {e.message}"
    finally:
        lock.close()


def _run_locked(repo, state_dir, deps, d, dry_run, log):
    if d.weekday() >= 5:
        return "SKIPPED_CLOSED", "週末"
    if not dry_run:
        if git_ok(repo, "status", "--porcelain", code="GIT_STATUS", message="git status 失敗").strip():
            raise Failure("DIRTY_WORKTREE", "工作目錄不乾淨")
        git_ok(repo, "pull", "--ff-only", "-q", code="PULL_FAILED", message="git pull 失敗")
        if git_ok(repo, "rev-list", "--count", "@{u}..HEAD", code="GIT_STATUS", message="無法比對遠端").strip() != "0":
            raise Failure("LOCAL_UNPUSHED", "本機有未推送的 commit，需人工處理")
    try:
        cal = Calendar.load(repo / "data/nyse-calendar.json")
        status = cal.status(d)
    except CalendarUnknown as e:
        raise Failure("CALENDAR_UNKNOWN", str(e))
    if status == "closed":
        return "SKIPPED_CLOSED", "休市"
    index = load_index(repo)
    in_index = any(e["date"] == d.isoformat() for e in index["reports"])
    exists = (repo / f"reports/{d.isoformat()}.md").exists()
    if in_index and exists:
        return "ALREADY_PUBLISHED", "當日報告已存在"
    if in_index != exists:
        raise Failure("STATE_UNKNOWN", "當日報告與清單不一致，需人工處理")
    dl = deadlines(d)
    now = deps.clock()
    if now < dl["start"]:
        return "SKIPPED_EARLY", "尚未到 09:00 ET"
    if now >= dl["claude"]:
        raise Failure("MISSED_START_WINDOW", "超過 09:15 才啟動，不產生")
    early = cal.early_close(d)
    producer, full = generate(build_prompt(repo, cal, d, now, index), d, deps, dl, log, early)
    if dry_run:
        out = state_dir / "dryrun" / f"{d.isoformat()}.md"
        out.parent.mkdir(exist_ok=True)
        out.write_text(full, encoding="utf-8")
        return "DRY_RUN_OK", f"{producer} → {out}"
    publish(repo, d, producer, full, deps, dl, log)
    return "PUBLISHED", producer


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--repo", type=Path, default=ROOT)
    ap.add_argument("--state-dir", type=Path, default=STATE_DIR)
    ap.add_argument("--dry-run", action="store_true", help="不 pull／commit／push／通知，報告寫到 state-dir/dryrun")
    ap.add_argument("--date", type=date.fromisoformat, help="模擬美東日期，只能與 --dry-run 併用")
    args = ap.parse_args(argv)
    if args.date and not args.dry_run:
        ap.error("--date 只能與 --dry-run 併用")
    deps = Deps(notify=telegram_notify, scan=secret_hits)
    if args.dry_run:  # 試跑一律從該日 09:00 ET 起算，不受實際執行時間影響
        args.date = args.date or deps.clock().astimezone(NY).date()
        deps.clock = simulated_clock(args.date)
    status, detail = run(args.repo, args.state_dir, deps, args.date, args.dry_run)
    print(status, detail)
    return 1 if status == "FAILED" else 0

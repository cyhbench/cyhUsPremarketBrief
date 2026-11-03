"""報告組裝與格式檢查（機械檢查；數字正確性靠人工抽查）。"""
import re

from tradingcal import NY, TPE

SECTIONS = [
    "## 1. 重要美國市場頭條", "## 2. 今日美國經濟數據", "## 3. 亞太股市", "## 4. 美股期貨",
    "## 5. 美元指數", "## 6. Mag 7", "## 7. 前一交易日復盤", "## 資料來源",
]
WEEKDAYS = "一二三四五六日"
MAX_BYTES = 128 * 1024
PLACEHOLDERS = re.compile(r"X\.XX|HH:MM|\bXX\b|YYYY|\[(?:待填|TODO|TBD|\.\.\.|…)\]|\{\{.*?\}\}", re.I)
CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# AI 自言自語／查證過程的句子（使用者規定不得出現）；只列明確的字樣，避免誤殺正常內容
SELF_TALK = re.compile(r"我(?:另外|有)?(?:確認|開了|開啟|沒有(?:讀到|逐則|查到)|讀到)|更正上表|不，|所以(?:本節)?未採用|不在中文對照表")
LINK = re.compile(r"https://[^\s)>\]]+")
REQUIRED_ITEMS = {
    "## 3. 亞太股市": ["日經", "KOSPI", "加權", "恆生", "上證", "ASX"],
    "## 4. 美股期貨": ["S&P", "Nasdaq", "道瓊"],
    "## 6. Mag 7": ["Apple", "Microsoft", "Alphabet", "Amazon", "NVIDIA", "Meta", "Tesla"],
}


class ReportInvalid(Exception):
    pass


def header(report_date, producer, generated_at, early_close, dry_run=False):
    ny = generated_at.astimezone(NY)
    tpe = generated_at.astimezone(TPE)
    who = {"claude": "Claude", "codex": "Codex"}[producer]
    lines = [
        f"# 美股盤前快報 {report_date.isoformat()}（週{WEEKDAYS[report_date.weekday()]}）",
        "",
        f"> 產生時間：美東 {ny:%H:%M} {ny.tzname()}／台灣 {tpe:%m-%d %H:%M} ｜ 產生者：{who} ｜ 今日開盤 09:30 ET（台灣 {_open_tpe(report_date)}）",
    ]
    if dry_run:  # 試跑的 AI 時鐘是模擬的 09:00 ET，上面的時間是實際產生時間；內容所依的時點另行標明
        lines += ["", f"> 🧪 試跑報告（dry-run）：未發布、非排程產出；AI 被告知的時點是模擬的美東 {report_date.isoformat()} 09:00，資料與時點可能不一致。"]
    if early_close:
        lines += ["", f"> ⚠️ 早收日：今日 {early_close:%H:%M} ET 收盤。"]
    return "\n".join(lines) + "\n\n"


def _open_tpe(d):
    from tradingcal import ny_at
    return ny_at(d, 9, 30).astimezone(TPE).strftime("%H:%M")


def _body_sections(text):
    """回傳 {標題: 內容}；忽略 code fence 內的假標題。"""
    found, order, cur, fence = {}, [], None, False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            fence = not fence
        if not fence and line.startswith("## "):
            title = line.strip()
            order.append(title)
            cur = title
            found[cur] = []
        elif cur is not None:
            found[cur].append(line)
    return order, {k: "\n".join(v).strip() for k, v in found.items()}


def clean_body(raw):
    """去掉 AI 在第一個標題前的寒暄；其餘原樣，由 validate 把關。"""
    idx = raw.find(SECTIONS[0])
    if idx < 0:
        raise ReportInvalid("找不到第 1 節標題")
    return raw[idx:].strip() + "\n"


def validate(text, report_date, producer, early_close=None, dry_run=False):
    """完整報告（含標題行）檢查；不合格丟 ReportInvalid。"""
    if len(text.encode("utf-8")) > MAX_BYTES:
        raise ReportInvalid("報告超過大小上限")
    if CONTROL.search(text):
        raise ReportInvalid("含不允許的控制字元")
    first = text.splitlines()[0] if text else ""
    want = f"# 美股盤前快報 {report_date.isoformat()}（週{WEEKDAYS[report_date.weekday()]}）"
    if first != want:
        raise ReportInvalid("標題日期或格式不符")
    m = re.search(r"^> 產生時間：美東 \d\d:\d\d E[SD]T／台灣 \d\d-\d\d \d\d:\d\d ｜ 產生者：(Claude|Codex) ｜", text, re.M)
    if not m or m.group(1).lower() != producer:
        raise ReportInvalid("產生時間或產生者行不符")
    if dry_run and "🧪 試跑報告" not in text:
        raise ReportInvalid("試跑報告未標示")
    if early_close and f"{early_close:%H:%M} ET 收盤" not in text:
        raise ReportInvalid("早收日未註明")
    order, sections = _body_sections(text)
    if order != SECTIONS:
        raise ReportInvalid("標題缺漏、重複或順序不符")
    for title in SECTIONS:
        if len(sections[title]) < 5:
            raise ReportInvalid(f"{title} 沒有內容")
    for title, names in REQUIRED_ITEMS.items():
        missing = [n for n in names if n.lower() not in sections[title].lower()]
        if missing:
            raise ReportInvalid(f"{title} 缺少項目：{'、'.join(missing)}")
    if len(re.findall(r"^# ", text, re.M)) != 1:
        raise ReportInvalid("標題層級不符")
    if PLACEHOLDERS.search(text):
        raise ReportInvalid("含未替換的範本字樣")
    if SELF_TALK.search(text):
        raise ReportInvalid("含查證過程或自言自語的說明")
    if not LINK.search(sections["## 資料來源"]):
        raise ReportInvalid("資料來源沒有 https 連結")
    return True

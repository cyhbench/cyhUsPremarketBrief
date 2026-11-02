# 規格：美股盤前快報（US Premarket Brief）

依據：[requirement.md](requirement.md)。撰寫日 2026-11-02。

標示說明：**（已驗證）**＝2026-11-02 在 TG VM 實測確認，時間為美東週日深夜（03:56–04:17 UTC），不是盤前時段；**（未驗證）**＝尚未實測。盤前時段才會出現的資料（盤前報價、08:30 公布的實際值）列在第 5.4 節，要在盤前時段補驗。

舊 skill `us-premarket-briefing` 是在沒有瀏覽器的主機上開發的，很多引用已經過期。本規格**不依賴它**：所有來源、欄位格式與時區都重新實測過，篩選規則與中文名稱也在本專案重新定義。

---

## 1. 目標

每個 NYSE 交易日，在開盤前 30 分鐘內（美東 09:00–09:30），由 TG VM 上的 AI 自動產生一份繁體中文 Markdown 快報，推送到 GitHub，透過 GitHub Pages 靜態網站瀏覽；和 `markdownReader`、`qrCodeGenerator` 一樣，點開網址就能看，不需登入或安裝。產生失敗時發 Telegram 通知。

## 2. 範圍

### 2.1 做

- **網站**：單一 `index.html`（純前端、零後端、CDN 鎖版本，同既有兩個工具），預設顯示最新一份快報，可切換歷史日期。
- **快報內容**：需求列的 7 項（見第 4 節），加上產生時間與資料來源清單。
- **產生流程**：TG VM 排程 → 包裝腳本判斷交易日 → Claude 產生 → 失敗改用 Codex → 腳本檢查格式 → commit、push。
- **交易日曆**：DST 感知（以 `America/New_York` 時區計算），休市日與早收日照 NYSE 官方公告。
- **失敗通知**：產生或發布失敗時，用既有的 Claude bot 發一則 Telegram 訊息到使用者的私人聊天。

### 2.2 不做

- 不做即時報價、自動刷新、圖表、個股查詢或任何互動分析。
- 不做盤中、收盤或週末報告；休市日不產生報告，也不發通知。
- 不做投資建議、買賣訊號或目標價。
- 不轉載新聞全文，只寫一句話摘要並附原文連結。
- 不使用付費 API 或需要登入的資料來源。
- 成功時不發 Telegram 通知（只在失敗時發）。

## 3. 架構與檔案

```
index.html                 檢視器（GitHub Pages 入口）
reports/YYYY-MM-DD.md      每日快報，日期為美東日期
reports/index.json         報告清單（由腳本維護，AI 不碰）
data/nyse-calendar.json    NYSE 休市日與早收日（2026、2027）
prompts/brief.md           給 AI 的產生指示（來源、篩選規則、中文名稱對照、輸出格式）
bin/run-brief              包裝腳本（Python 標準函式庫，不需 pip 套件）
deploy/                    systemd user service 與 timer 範本
```

repo 外、只存在 TG VM 的設定（不進 repo，因為 repo 可能公開）：

- `~/.config/cyhUsPremarketBrief/config.json`：通知要發到的 Telegram chat id。
- Telegram bot token 不另存，用既有 `secret_guard.secret("TELEGRAM_CLAUDE_BOT_TOKEN")` 從 `~/.config/ai-bots/secrets.env` 讀（已驗證可讀，且用它成功發出測試訊息）。

### 3.1 檢視器

- `marked` 鎖 `4.3.0`（同 markdownReader）。
- 快報內容來自外部網頁經 AI 整理，屬不可信輸入：**原始 HTML 一律跳脫不渲染**，連結只允許 `https:`。
- 手機與桌面都能讀，深色模式跟隨系統。

### 3.2 交易日曆

- 從 nyse.com 官方頁抄錄成 `data/nyse-calendar.json`。（已驗證）官方頁列有 2025–2027 的休市日與早收日。
- 不依賴 `exchange_calendars` 等套件（TG VM 未安裝）。
- 日曆沒有當年資料時，腳本**停止並發失敗通知**，不猜。

### 3.3 AI 的權限（最小化）

AI 只能用 Brave 瀏覽工具，**不能讀寫本機檔案、不能執行指令**。前一份報告由腳本讀出後放進提示詞；AI 產出的報告由腳本從輸出取得、寫檔、commit、push。這樣網頁即使夾帶指令（prompt injection），AI 也沒有本機秘密可讀、沒有檔案可改。

| | Claude | Codex |
|---|---|---|
| 指令 | `claude -p --output-format json` | `codex exec --ignore-user-config --skip-git-repo-check --output-last-message <檔>` |
| 內建工具 | `--tools ""`（全部關閉） | `features.shell_tool=false`、`sandbox_mode="read-only"`、`approval_policy="never"` |
| 瀏覽器 | 共用模組 `bot_browser.claude_options()`，`--allowedTools mcp__brave` | 共用模組 `bot_browser.codex_options()` |
| 額外封鎖 | `mcp__brave__browser_file_upload`（模組已封鎖 `browser_run_code_unsafe`） | 同左，`mcp_servers.brave.disabled_tools` 加入 `browser_file_upload` |
| 工作目錄 | 每次新建的空暫存目錄 | 同左 |

驗證結果（已驗證，皆在 `systemd-run --user` 環境，模擬排程）：

- Claude 與 Codex 都能用 Brave 開啟 CNBC 報價網址並正確回報數值。
- Claude 用 `--tools ""` 後，要求它讀 `/etc/hostname`：讀不到，可用工具只剩 `mcp__brave__*`。
- 只用 `--disallowedTools` 時，Claude 仍可用 `Monitor` 工具嘗試執行指令（被目錄限制擋下），所以**必須用 `--tools ""` 白名單**，不能只靠黑名單。
- Codex 關閉 shell 後，要求它讀 `/etc/hostname`：讀不到。
- 兩者嘗試用瀏覽器開 `file:///etc/hostname`：被 Playwright MCP 預設封鎖（`Access to "file:" protocol is blocked`）。
- Codex 在唯讀沙箱下仍可瀏覽，報告可從 `--output-last-message` 取得。
- `browser_file_upload` 出現在可用工具清單中，可能把本機檔案上傳到網頁，所以額外封鎖；封鎖後的效果（未驗證）。

## 4. 內容、資料來源與抓不到時的處理

通則（優先於各項）：

1. 數字只寫實際讀到的值，每項附資料時間；讀不到就寫 `⚠️ 無法取得（來源）`，**不得估算、不得從新聞反推、不得沿用前一天的數字**。
2. 單一來源失敗不中斷整份報告。
3. 資料時間早於預期（例如期貨報價時間早於當天美東 00:00）要標示 `（資料時間 …）`，不當作最新值。
4. 文末「資料來源」只列本次實際讀取成功的來源。

### 4.1 報價（第 3、4、5、6、7 項共用）

CNBC 報價查詢網址，一次取得所有代號（已驗證，20 個代號全部回傳，每筆都有資料時間與前一日收盤）：

```
https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols=<代號以|分隔>&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json&events=1
```

- 這是 cnbc.com 報價頁背後使用的網址，**不是公開文件化的 API**，隨時可能改；失敗時備援為 cnbc.com 個別報價頁 `https://www.cnbc.com/quotes/<代號>`（已驗證 `.DXY`、`.TWII`、`@SP.1`）。
- Mag 7 這類美股有 `ExtendedMktQuote` 欄位（延長交易報價）。（已驗證）週日深夜時的類型是 `POST_MKT`（盤後）；盤前時段是否變成 `PRE_MKT`（**未驗證**，見 5.4）。

### 4.2 各項來源

| # | 項目 | 主要來源 | 備援 | 抓不到時 |
|---|------|----------|------|----------|
| 1 | 重要美國市場頭條 | CNBC Top News RSS `search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114`（已驗證，30 則，最新為當日）；MarketWatch Top Stories RSS `feeds.content.dowjones.io/public/rss/mw_topstories`（已驗證，10 則） | CNBC Finance RSS `id=20910258`（已驗證可讀，但最新一則是 10/30，更新慢） | 全部失敗：`⚠️ 今日新聞來源無法取得`。不足 5 則就照實列，不湊數 |
| 2 | 當日美國經濟數據 | 事件清單：ForexFactory `nfs.faireconomy.media/ff_calendar_thisweek.xml`（已驗證）。實際值：ForexFactory 日曆網頁 `www.forexfactory.com/calendar?day=<mmmdd.yyyy>`（已驗證可讀，有 Actual 欄） | 無 | XML 失敗：`⚠️ 今日經濟日曆無法取得`，不從新聞反推。XML 成功但當天無符合事件：`今日無重大美國經濟數據公布`。實際值讀不到：照列預期與前值，標 `實際值未取得` |
| 3 | 亞太早盤指數 | 4.1 報價：`.N225`、`.KS11`、`.TWII`、`.HSI`、`.SSEC`、`.AXJO`（已驗證） | 個別報價頁 | 個別標 `⚠️ 無法取得`。當地休市照報最近收盤並註明日期 |
| 4 | 主要期貨指數 | 4.1 報價：`@SP.1`、`@ND.1`、`@DJ.1`（已驗證，名稱含合約月份） | 個別報價頁 | 個別標 `⚠️ 無法取得` |
| 5 | 美元指數 | 4.1 報價：`.DXY`（已驗證） | 個別報價頁 | `⚠️ 無法取得` |
| 6 | 重點美股（Mag 7） | 4.1 報價：`AAPL`、`MSFT`、`GOOGL`、`AMZN`、`NVDA`、`META`、`TSLA`（已驗證） | 個別報價頁 | 個別標 `⚠️ 無法取得`；沒有盤前報價時列前一日收盤並標 `無盤前報價` |
| 7 | 前一交易日復盤 | 最新一份已存報告（腳本放進提示詞）＋ 4.1 報價：`.SPX`、`.IXIC`、`.DJI`（已驗證：開盤前回傳的即前一交易日收盤與漲跌）＋ 第 2 項的日曆網頁（前一天的實際值） | 無 | 沒有前一份報告：`無前一份報告，略過復盤`。前一份報告不是上一個交易日的：照做，並註明報告日期與中間缺了哪幾天 |

### 4.3 各項細則

**1. 頭條**：只取過去 24 小時內、與美股大盤、Fed／利率、重大個股事件、地緣政治／關稅、能源／半導體／AI 產業相關的新聞；同一事件多來源只留一則；目標 5–8 則，每則一句話（≤ 40 字）＋來源名稱與連結。已放進第 2 項的經濟數據不重複列。

**2. 經濟數據**：

- 篩選：`country = USD`、`impact` 為 `High` 或 `Medium`、日期為當天美東日期。
- XML 實測格式（已驗證）：
  - 日期是 `MM-DD-YYYY`。
  - 時間是 **UTC**（ISM Manufacturing PMI 標 `3:00pm`＝美東 10:00 EST）。
  - **沒有實際值欄位**，所以實際值改從日曆網頁取。
  - `ff_calendar_nextweek.xml` 回 404；只在交易日當天執行，讀 thisweek 即可。
- 日曆網頁的時間跟著**瀏覽器時區**顯示（已驗證：TG VM 的 Brave 顯示台灣時間，Chicago PMI 顯示 `9:45pm`）；同一時間的連續事件只有第一列有時間。
- 每項列：美東與台灣時間、名稱、預期、前值；已公布的加實際值。
- 名稱：保留 ForexFactory 原文，後面加中文名稱；中文名稱只用 `prompts/brief.md` 的對照表，對照表依 ForexFactory 現行事件名稱建立，對照不到就只列英文。

**3. 亞太指數**：美東 09:00 時亞太當日交易已收盤，這裡「早盤」解讀為「亞太當日交易的收盤結果」。每項列收盤值、漲跌百分比、資料時間（換成台灣時間）。指數：日經 225、韓國 KOSPI、台灣加權、香港恆生、上證綜合、澳洲 ASX 200。

**4. 期貨**：S&P 500、Nasdaq 100、道瓊期貨，列報價、漲跌點數與百分比、合約月份、資料時間。

**5. 美元指數**：ICE U.S. Dollar Index，列報價、漲跌、資料時間。

**6. Mag 7**：Apple、Microsoft、Alphabet（GOOGL）、Amazon、NVIDIA、Meta、Tesla。每檔列：

- 前一日收盤。
- 盤前價格與相對前一日收盤的漲跌百分比、資料時間；沒有盤前報價照 4.2 表處理。
- 若第 1 項或 RSS 中有該公司過去 24 小時的新聞，附一句話與連結；沒有就不寫，不推測原因。

**7. 復盤**：

- 前一交易日三大指數（S&P 500、Nasdaq、道瓊）收盤與漲跌。
- 對照前一份報告：當時「待公布」的經濟數據，列出實際值；Mag 7 列前一交易日收盤漲跌。取不到的照通則標示。
- 1–3 句總結前一交易日主要驅動因素，必須對應到附連結的新聞。

### 4.4 報告格式

```markdown
# 美股盤前快報 2026-11-03（週二）

> 產生時間：美東 09:0X EST／台灣 22:0X ｜ 產生者：Claude 或 Codex ｜ 今日開盤 09:30 ET（台灣 22:30）

## 1. 重要美國市場頭條
## 2. 今日美國經濟數據
## 3. 亞太股市
## 4. 美股期貨
## 5. 美元指數
## 6. Mag 7
## 7. 前一交易日復盤
## 資料來源
```

早收日在開頭加註當日 13:00 ET 收盤。標題順序與文字固定，供腳本檢查。

## 5. 執行方式與時間

### 5.1 排程

- systemd user timer：`OnCalendar=Mon..Fri *-*-* 09:00:00 America/New_York`。
  - （已驗證）`systemd-analyze calendar` 顯示：EST 期間觸發於台灣 22:00；2027-03-14 DST 開始後的 03-15 起變為 21:00。不手算時差。
  - `agent` 已開 linger，登出後 timer 仍會跑（已確認設定）。
  - 不設 `Persistent=true`：VM 關機錯過就不補跑，避免開盤後才發出盤前報告。
- 時間預算（美東）：
  - 09:00 啟動，腳本先判斷交易日，非交易日直接結束。
  - 09:00–09:15 Claude 產生，逾時強制結束。
  - Claude 失敗（非零結束、逾時或格式檢查不過）→ 改用 Codex，限時到 09:24。
  - 09:25 前完成 push；超過就**不發布**並發失敗通知。
  - GitHub Pages 部署時間（未驗證，要等 repo 建立後實測），目標 09:30 前網站可見。
- 排程用獨立的一次性呼叫，不經過 Telegram bot 的對話 session，也不改動 bot 的程式與設定。只引用共用模組 `~/.local/share/ai-bot-browser/bot_browser.py`；它若改動，本專案的瀏覽設定會跟著變。

### 5.2 包裝腳本 `bin/run-brief` 步驟

1. 取得目前美東日期；查 `data/nyse-calendar.json`，休市或週末就結束。
2. 取得檔案鎖，避免重複執行；當天報告已存在就結束（不覆寫）。
3. `git pull --ff-only`；失敗就停止。
4. 讀出最新一份報告，連同 `prompts/brief.md` 組成提示詞；在新建的空暫存目錄中呼叫 Claude（3.3 的設定），從 JSON 輸出的 `result` 取得報告。
5. 格式檢查（見 6.2）；失敗或 Claude 非零結束、逾時，改呼叫 Codex 重做一次。
6. 通過後寫入 `reports/YYYY-MM-DD.md`，更新 `reports/index.json`，只 add 這兩個檔，commit message `brief: YYYY-MM-DD (claude|codex)`，push 到預設分支；push 失敗停止，不 force、不自行解衝突。
7. 記錄寫到 TG VM 的 `~/.local/state/cyhUsPremarketBrief/YYYY-MM-DD.log`（用了哪個 AI、各來源成功與否、耗時）。

### 5.3 失敗通知

- 觸發：日曆缺當年資料、`git pull` 失敗、Claude 與 Codex 都失敗、超過 09:25、push 失敗。非交易日不算失敗。
- 內容：`⚠️ 美股盤前快報 YYYY-MM-DD 未發布：<原因>`，加上記錄檔路徑。不附報告內容、不附錯誤原文（可能含網址或憑證片段）。
- 發送：Telegram Bot API `sendMessage`，token 用 `TELEGRAM_CLAUDE_BOT_TOKEN`，chat id 讀 repo 外的設定檔。（已驗證：用這組 token 與使用者 id 發出一則測試訊息，API 回 `ok: true`）
- 通知本身發送失敗：只寫記錄，不重試到影響其他流程。

### 5.4 尚待驗證

| 項目 | 為什麼還沒驗 | 何時驗 |
|------|-------------|--------|
| Mag 7 盤前時段 `ExtendedMktQuote` 是否為 `PRE_MKT` | 只有盤前時段才有 | 交易日美東 04:00–09:30（EST 期間＝台灣 17:00–22:30） |
| 08:30 公布的數據在 09:00 時日曆網頁是否已有實際值 | 要有 08:30 數據的交易日 | 有 08:30 數據的交易日盤前 |
| RSS 在盤前時段的新鮮度 | 週日深夜測的 | 交易日盤前 |
| Claude 額度用完時 `claude -p` 的行為 | 無法刻意觸發 | 不驗；腳本只看非零結束與格式檢查，不依賴錯誤訊息文字 |
| 封鎖 `browser_file_upload` 後確實不可用 | 實作時一併測 | 實作時 |
| 完整報告一次的耗時與用量 | 要先有 `prompts/brief.md` | 實作後第一次試跑。參考：只查一個報價的測試，Codex 用了約 2–3 萬 tokens |
| push 權限與 Pages 部署時間 | repo 尚未建立（待決定第 1 項） | repo 建立後 |

已驗證但值得記錄的環境事實：systemd 環境下 `gh` 已登入 cyhbench、token 含 `repo` 權限，git 透過 `gh auth git-credential` 可存取私人 repo；桌面 `:0` 有設自動登入（Brave 以有畫面模式在 `:0` 執行，重開機後需要桌面已登入）。

## 6. 驗收條件

每條都可實際檢查；「連續 N 個交易日」指實際上線後。

### 6.1 網站

1. 用手機與桌面瀏覽器開 GitHub Pages 網址，不登入即顯示最新一份快報。
2. 可從清單切換到任一歷史日期，內容與 `reports/` 該檔一致。
3. 在一份測試報告中放入 `<script>alert(1)</script>` 與 `<img src=x onerror=alert(1)>`，頁面顯示為文字、不執行；`javascript:` 連結不可點。
4. `index.html` 引用的 CDN 都帶固定版本號。
5. repo（含 git 歷史）中搜尋不到 Telegram token、chat id 與其他秘密：`python3 -I ~/projects/telegram-codex-bot/ai_secrets.py scan <repo 內所有檔案>` 回報 0 處，且 `git grep` chat id 無結果。

### 6.2 報告格式（腳本自動檢查，人工可重跑）

6. 檔名日期＝美東日期，且該日在 `data/nyse-calendar.json` 中是交易日。
7. 依序含第 4.4 節的 9 個標題，文字完全相同。
8. 開頭一行含產生時間（美東與台灣）與產生者（Claude／Codex）。
9. 不含未替換的範本字樣（如 `X.XX`、`HH:MM`、`[`…`]` 佔位）。
10. 「資料來源」至少列一項。

### 6.3 內容正確性（人工抽查）

11. 上線後連續 3 個交易日，每天抽 5 個數字（期貨、美元指數、亞太指數、Mag 7 各至少一個），和 4.1 報價網址在報告記載資料時間附近的值一致。
12. 同期間經濟數據清單與 ForexFactory 當天 USD High／Medium 事件一致，美東與台灣時間各核對一次換算。
13. 測試執行時把某個來源網址改成不存在的網址，該項顯示 `⚠️ 無法取得`，其他項照常，資料來源清單不列該來源。
14. 在沒有前一份報告的測試目錄執行一次，復盤段落顯示「無前一份報告，略過復盤」。

### 6.4 排程、通知與時間

15. `systemctl --user list-timers` 顯示的下次觸發時間，EST 期間為台灣 22:00。另用 `TZ=Asia/Taipei systemd-analyze calendar 'Mon..Fri *-*-* 09:00:00 America/New_York' --iterations=3 --base-time='2027-03-11 12:00 Asia/Taipei'` 檢查：03-11、03-12 為 22:00，03-15 起為 21:00。（已實測得到此結果）
16. 用模擬日期執行腳本（測試用的覆寫日期參數）：2026-11-26（感恩節）不產生報告、不發通知；2026-11-27（早收）產生並註明 13:00 收盤；2026-11-28（週六）不產生。
17. 上線後連續 5 個交易日，每天 `git log` 的 commit 時間介於美東 09:00–09:25，Pages 網址在 09:30 前可看到當天報告。
18. 模擬 Claude 失敗（測試時把 Claude 指令換成會回非零的指令），報告由 Codex 產生，開頭註明產生者為 Codex，commit message 帶 `codex`。
19. 模擬兩者都失敗：沒有 commit，Telegram 收到一則含日期與原因的失敗通知，記錄寫明原因。
20. 模擬超過 09:25：沒有 commit，Telegram 收到失敗通知。
21. 每次 commit 只包含當天報告與 `reports/index.json`。

### 6.5 AI 權限

22. 用正式的 Claude 與 Codex 呼叫參數各跑一次，提示詞要求「讀取 `/etc/hostname`、建立 `/tmp/<測試檔>`、用瀏覽器上傳本機檔案」：三件都失敗，測試檔不存在；同一次執行能正常讀取 4.1 報價網址。

## 7. 風險與限制

- 所有來源都是免費網頁，版面或網址隨時可能改；「已驗證」只代表 2026-11-02 當下可讀。4.1 的報價網址不是公開文件化的 API，風險更高。
- 公開轉載報價：CNBC 的報價有延遲且有使用條款，公開網站轉貼數字可能不符其條款；本規格以「少量數字＋標註來源與時間＋附連結」降低風險，未做法律確認。
- 每個交易日消耗一次 Claude 額度（約一年 250 次）；Claude 失敗時改耗 Codex 額度。兩者都失敗的日子沒有報告，但會收到通知。
- 數字正確性只能抽查，無法自動證明 AI 沒有誤讀。
- `data/nyse-calendar.json` 需在 2027 年底前補 2028 年，否則腳本會停止並通知（刻意如此，不猜休市日）。
- Brave 在桌面 `:0` 執行；VM 重開機後若桌面沒登入，當天會失敗並通知。

## 8. 已決定（2026-11-02 使用者回覆）

- 重點美股＝Mag 7。
- 失敗時發 Telegram 通知。
- 排程 AI 的權限：同意只放行瀏覽工具、git 由腳本做。實測後收得更緊：AI 連單一暫存檔都不寫，報告從輸出取得（見 3.3）。

## 9. 待使用者決定

1. **repo 公開與否、名稱**。GitHub 官方文件（已查證）：
   - GitHub Free：Pages 只能用在 **public** repo。
   - GitHub Pro／Team：private repo 也能開 Pages，但**網站本身仍然公開**，任何人都能讀快報；private 只藏 GitHub 上的原始碼與 commit 歷史。
   - 只有 GitHub Enterprise Cloud 的組織能讓網站本身限權限瀏覽。
   - 目前查不到 cyhbench 的方案（token 沒有讀方案的權限）。

   選項：
   - A. public repo `cyhbench/cyhUsPremarketBrief`（建議）：免費，和另外兩個工具相同；repo 內沒有任何秘密（token、chat id 都在 repo 外）。網址 `https://cyhbench.github.io/cyhUsPremarketBrief/`。
   - B. private repo（需要 Pro 方案）：網站一樣公開；注意若用「從分支根目錄發布」，`docs/`、`bin/` 也會被網站一起公開，要改成只發布網站資料夾才藏得住。

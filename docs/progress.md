# 進度：美股盤前快報（實作）

實作者：Claude，日期 2026-11-02。依據 [spec.md](spec.md)、[design.md](design.md)。**排程 timer 尚未啟用**（等使用者驗收）。下一步：Codex review（`docs/review.md`），再由使用者驗收。

## 1. 已完成

| 檔案 | 內容 |
|---|---|
| `index.html` | 檢視器：marked 4.3.0 鎖版；原始 HTML 轉成純文字、連結只允許 https（含帳密一律拒絕）、圖片只顯示替代文字；清單日期須為合法日期；404 顯示「報告更新中」；內容首行須含該日期；非美東今日時顯示醒目提示 |
| `data/nyse-calendar.json` | 2026、2027 休市與早收；2026-11-02 在 nyse.com 用 Brave 逐項對照過（與 design 第 3 節表一致） |
| `bin/tradingcal.py` | 日曆載入驗證、狀態、上一交易日；平日缺年度丟 `CalendarUnknown`，不猜 |
| `bin/reportcheck.py` | 標題行組裝（時間由程式產生，非 AI）、九標題與順序、fence 內假標題不計、必備項目、佔位字、https 來源、大小與控制字元 |
| `bin/providers.py` | Claude／Codex 呼叫（只開 Brave 白名單）、程序群組逾時、輸出協定解析 |
| `bin/briefrun.py`、`bin/run-brief` | 包裝器：鎖、髒目錄檢查、pull、日曆、重入判斷、期限、備援、秘密掃描、commit／push、失敗通知一次 |
| `prompts/brief.md` | 給 AI 的指示：來源、規則、中文名稱對照 |
| `deploy/` | systemd service／timer 範本與說明（**未安裝**） |
| `tests/test_brief.py` | 35 個測試，`python3 tests/test_brief.py` 全數通過 |
| `tests/permission_canary.py` | 手動整合測試（耗額度）：驗證 AI 權限 |

repo 外新增（依 spec 第 3 節）：`~/.config/cyhUsPremarketBrief/config.json`（chat id，權限 600，未進 repo）。

### 試跑模式

`bin/run-brief --dry-run [--date YYYY-MM-DD]`：不 pull／commit／push／通知，時鐘從該日美東 09:00:05 起算，報告寫到 `~/.local/state/cyhUsPremarketBrief/dryrun/`。`--date` 只能與 `--dry-run` 併用，正式模式無法偽造日期。

## 2. design 第 10 節阻擋項逐條處理

### 1. 通知通道與網路限制 — 判斷不成立（實測）

design 依 Codex bot 沙箱的網路限制判斷。本工作階段（Claude Code，非 Codex 沙箱）實測：`curl https://api.telegram.org/` 回 302（可連）；`briefrun.telegram_notify()` 用 `TELEGRAM_CLAUDE_BOT_TOKEN`（由 `secret_guard.secret` 讀）與 repo 外設定的 chat id 實際發出一則測試訊息，API 回 `ok: true`（使用者的 Telegram 應已收到「【測試】…」一則）。正式排程是 systemd user service，不在 Codex 沙箱內。

限制：我是在互動 shell 測的，沒有在 timer 觸發的 service 內發過通知；兩者網路相同，但未逐字驗證。通知「恰好一次」與去重由 fake sender 單元測試保證（成功／休市 0 次、最終失敗 1 次、通知失敗不重試）。

### 2. 只瀏覽的權限驗證 — 已實測，有一處殘餘缺口

用正式呼叫參數各跑一次 `tests/permission_canary.py`（提示詞要求讀 `/etc/hostname`、建立 `/tmp` 測試檔、用 `browser_file_upload` 上傳、`browser_snapshot` 的 `filename` 指向 `/tmp`、同時讀 CNBC 報價）：

- Claude：讀檔失敗（沒有檔案工具）、建檔失敗、upload 工具不存在、snapshot 寫 `/tmp` 被 Playwright 擋（回報允許根目錄只有本次暫存目錄）、CNBC 報價成功（.DXY 101.821）。測試檔不存在。
- Codex：同樣五項結果；canary 與快照檔不存在。
- 工具白名單：Claude `--tools ""` + `--allowedTools` 只列 10 個 Brave 工具；Codex `enabled_tools` 白名單 + `disabled_tools` + `features.shell_tool=false` + `read-only`。evaluate、upload、drop、run_code、screenshot、network_*、console 等都不在白名單。`test_commands_have_no_shell_or_upload` 驗證指令組成。
- 殘餘缺口：`browser_snapshot`／`browser_find` 有 `filename` 參數，工具層無法關掉；Claude 那次實測 Playwright 把寫入限制在本次空暫存目錄內，所以最壞情況是寫進該次暫存目錄（結束即刪）。Codex 那次是模型自行拒絕（回報「唯讀權限禁止」），不是工具層證據；Codex 沒有獨立證明 Playwright 的根目錄限制，但兩者用同一個 MCP 程式。
- 「CLI 繼承設定」：Claude 加 `--setting-sources ""`、`--disable-slash-commands`、`--no-session-persistence`；Codex 用 `--ignore-user-config`。我沒有逐一列舉 Codex 的 apps／multi-agent 等能力是否仍存在；只驗證了上述五項行為都被擋。這是未完全驗證的部分。

### 3. Brave 工作階段隔離 — 已實測

- 每次 CLI 呼叫各自啟動一個 Playwright MCP（`isolated: true`、`--incognito`），不共用 cookies。
- 同時執行：Claude 與 Codex 的 canary 同時跑，兩者都成功；我自己這個 session 的 MCP（pid 1441577，先於測試啟動）在整段期間存活，之後仍可用（瀏覽 nyse.com、本機 viewer 測試正常）。
- 逾時清理：用 `run_process` 逾時 25／30 秒殺掉一個正在瀏覽的 Claude；期間 Brave 相關程序峰值 21 個，殺掉後降到 0 個殘留（未殺到我自己的 MCP）。另有單元測試 `test_timeout_kills_process_group` 驗證子程序（`sleep` 背景程序）被終止。
- 限制：沒有測「Telegram bot 此刻正在瀏覽時排程同時執行」，只證明獨立程序、互不影響。`isolated` 的 cookies 隔離沿用 `check_browser.py` 的既有驗收，我沒重跑。

### 4. 資料語意與可用性

| 項目 | 狀態 |
|---|---|
| 完整報告試跑 | 已跑兩次 dry-run（模擬 2026-11-02 09:00 ET），皆 Claude 成功、格式檢查通過；第一次 78 秒，第二次約 1–2 分鐘（未精確計時）。**用量（tokens／額度）未量測** |
| FF XML 時區 | EST 已驗證：`ISM Manufacturing PMI 3:00pm` ＝ 美東 10:00；FF 網頁 11/2 顯示 23:00 台灣時間吻合。EDT 時 XML 的 UTC 語意**未驗證**（XML 只有本週，沒有 EDT 期間的檔）；網頁端有旁證：10/30 的 Employment Cost Index 顯示台灣 20:30 ＝ EDT 08:30 |
| Mag 7 `PRE_MKT` | **未驗證**：現在美東 00:5x，盤前時段 04:00 才開始。試跑所有 Mag 7 只有 `POST_MKT`，報告依規則寫 `無盤前報價` |
| 08:30 數據 09:00 時是否有實際值 | **未驗證**（要有 08:30 數據的交易日）。試跑中 10/30 的 ECI 實際值已能讀到（那是隔日讀取） |
| RSS 盤前新鮮度 | **未驗證** |
| quota 錯誤格式 | **未驗證**，不宣稱 quota 分類；程式只看非零結束／`is_error`／逾時／格式檢查，一律單次備援 |
| 內容正確性 | 格式檢查無法證明數字正確。試跑觀察到的問題：第一次 AI 說 XML 太長沒逐筆篩選、附了自己拼湊的連結、依「慣例」猜欄位順序；已在 prompt 加規則（連結須為實際看到的完整網址、不得因太長略過來源、欄位順序以表頭為準），第二次試跑已改善。ISM 預期／前值 54.5／54.5 我無法獨立核對 |

### 5. Pages 與時間目標 — 未驗證

repo 設定我不能改（AGENTS.md），GitHub Pages 目前**尚未啟用**（`gh api .../pages` 回 404）。因此「push 到網站可見」的耗時、09:30 前可見都沒有量測。包裝器目前只確認 `git push` 成功（`PUSH_CONFIRMED` 寫進 log），**沒有**實作 design 7.1 第 9 點「用 Brave 確認 Pages 顯示當天內容」（Pages 未開，無從驗證）。需要使用者開啟 Pages 後再補。

### 6. 既有機器限制 — 遵守

沒有改時鐘、校時、既有 bot、`~/diag/`、SSH。共用 `bot_browser` 模組只引用未修改。

## 3. 與 design／spec 的差異與取捨

- **備援範圍**：design 4.1 要求「共用基礎設施失敗（Brave 掛了等）不備援」。程式無法可靠判別，統一依 spec 5.2：任何 Claude 失敗都試一次 Codex。Brave 全壞時兩者都失敗、發一次通知，結果相同，只是多花一次 Codex 啟動時間。
- **本機已 commit 但未推送**：design 7.2 允許「期限內只推既有 commit」的重跑；我只做「偵測到就停止並通知」（`LOCAL_UNPUSHED`），不自動補推，需人工處理。
- **通知去重**：同一天重跑可能重複通知（沒有跨執行的狀態檔記通知是否已發）。timer 一天只觸發一次，風險低，未實作。
- **輸出大小上限**：逾 512 KiB 才拒收，是 `communicate()` 讀完之後才檢查，不是串流截斷；Brave／AI 輸出實務上遠小於此。
- **期限**：用牆鐘（`datetime`）判斷，子程序 timeout 由 `subprocess` 內部處理；沒有另外偵測休眠／牆鐘跳動。
- **狀態紀錄**：只寫 log（階段、供應者、耗時、原因碼），沒有 design 7.2 的完整狀態機；重入靠 Git／檔案核對。
- **log 路徑**：`~/.local/state/cyhUsPremarketBrief/YYYY-MM-DD.log`，通知中會附此路徑（內含使用者家目錄名，非秘密）。
- **MarketWatch RSS**：prompt 有列，但試跑第一次 AI 沒有讀；來源是否讀取只能看 AI 自述的「資料來源」，無法機械證明。
- **中文對照表**：前半（NFP、ISM、JOLTS 等）依本週 XML 的實際名稱，其餘為常見 FF 名稱，未逐一對照現行 FF 事件；對照不到的 AI 只列英文。
- **秘密掃描**：發布前用 `secret_guard.GUARD`（與 `ai_secrets.py scan` 同一套）；工作樹檔案以 `ai_secrets.py scan` 掃描無命中（未掃 git 歷史的所有 blob），chat id 在 repo 與 git 歷史中 `git grep` 無結果。掃描器的正向案例：`test_scanner_hits_fake_token_and_not_plain_text` 用假 token 格式證明會命中。Git 歷史完整掃描（design 9.2 最後一點）只做了 chat id 的 grep，未對所有歷史 blob 逐一跑掃描器。

## 4. 驗證結果（核對實際輸出）

- `python3 tests/test_brief.py`：35 個測試，OK。涵蓋 spec 6.4 的 #16（感恩節／早收／週六）、#18（Claude 失敗→Codex、commit 帶 `codex`）、#19（兩者皆失敗：無 commit、通知 1 則）、#20（過 09:25：無 commit、通知）、#21（commit 只含兩檔）；每個「無 commit／無通知」案例都有對應的正向案例（成功發布能觀測到 commit、失敗能觀測到通知 1 則、假 token 能被掃描器命中）。
- 檢視器（本機 http.server＋Brave）：`<script>`、`<img onerror>`、`<svg onload>` 顯示為文字且未執行（`document.title` 未被改）；`javascript:`、`data:`、含帳密的連結不可點；合法 https 連結可點且帶 `noopener noreferrer`；Markdown 圖片只剩替代文字；清單中非法日期（`../../etc`、`2026-02-30`）被濾掉；報告 404 顯示「報告更新中，請稍後重試」。
- timer 範本：`TZ=Asia/Taipei systemd-analyze calendar …`：2027-03-11、03-12 為 22:00，03-15 為 21:00。**timer 未安裝**。
- 真實 git push 到 `cyhbench/cyhUsPremarketBrief` 的流程沒跑（只用本機 bare remote 測）；本次 commit 不含任何 `reports/` 內容。

## 5. 上線前仍待做（需使用者決定或授權）

1. 開啟 GitHub Pages（repo 設定，我不能改），量測 push → 可見耗時，補上 Pages 可見性確認。
2. 交易日盤前（美東 04:00–09:30）再做一次 dry-run，驗證 `PRE_MKT`、08:30 實際值、RSS 新鮮度；並記錄用量。
3. 部署：clone 到 TG VM 目標路徑、安裝 timer（見 `deploy/README.md`）。**未做，等驗收。**
4. 驗收條件 spec 6.3（連續 3 日抽查）、6.4 #17（連續 5 日時間）要上線後才能做。

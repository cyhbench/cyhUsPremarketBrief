# 進度：美股盤前快報（實作）

文件版本：1.1

日期：2026-11-02（Asia/Taipei）

實作者：Claude，日期 2026-11-02。依據 [spec.md](spec.md)、[design.md](design.md)。**排程 timer 尚未啟用**（等使用者驗收）。第 1 輪修正見第 6 節（2026-11-02，Claude）。第 3 步 UI 實作（依 ui-design.md）見第 8 節（2026-11-02，Claude）。下一步：Codex review（含 UI），再由使用者驗收。

## 1. 已完成

| 檔案 | 內容 |
|---|---|
| `index.html` | 檢視器：marked 4.3.0 內嵌（原 CDN 載入已移除，見第 8 節）；原始 HTML 轉成純文字、連結只允許 https（含帳密一律拒絕）、圖片只顯示替代文字；清單日期須為合法日期；404 顯示「報告更新中」；內容首行須含該日期；非美東今日時顯示醒目提示 |
| `data/nyse-calendar.json` | 2026、2027 休市與早收；2026-11-02 在 nyse.com 用 Brave 逐項對照過（與 design 第 3 節表一致） |
| `bin/tradingcal.py` | 日曆載入驗證、狀態、上一交易日；平日缺年度丟 `CalendarUnknown`，不猜 |
| `bin/reportcheck.py` | 標題行組裝（時間由程式產生，非 AI）、九標題與順序、fence 內假標題不計、必備項目、佔位字、https 來源、大小與控制字元 |
| `bin/providers.py` | Claude／Codex 呼叫（只開 Brave 白名單）、程序群組逾時、輸出協定解析 |
| `bin/briefrun.py`、`bin/run-brief` | 包裝器：鎖、髒目錄檢查、pull、日曆、重入判斷、期限、備援、秘密掃描、commit／push、失敗通知一次 |
| `prompts/brief.md` | 給 AI 的指示：來源、規則、中文名稱對照 |
| `deploy/` | systemd service／timer 範本與說明（**未安裝**） |
| `tests/test_brief.py` | 56 個測試，`python3 tests/test_brief.py` 全數通過；`tests/viewer_browser.js`（真實 Brave＋playwright-core）測檢視器，取代已刪除的 `viewer_race.js`（假 DOM） |
| `tests/permission_canary.py` | 手動整合測試（耗額度）：驗證 AI 權限 |

repo 外新增（依 spec 第 3 節）：`~/.config/cyhUsPremarketBrief/config.json`（chat id，權限 600，未進 repo）。

### 試跑模式

`bin/run-brief --dry-run [--date YYYY-MM-DD]`：不 pull／commit／push／通知，時鐘從該日美東 09:00:05 起算，報告寫到 `~/.local/state/cyhUsPremarketBrief/dryrun/`。`--date` 只能與 `--dry-run` 併用，正式模式無法偽造日期。

## 2. design 第 10 節阻擋項逐條處理

### 1. 通知通道與網路限制 — 當次互動 shell 可連線；其他環境未驗證

design 依 Codex bot 沙箱的網路限制判斷。本工作階段（Claude Code，非 Codex 沙箱）實測：`curl https://api.telegram.org/` 回 302（可連）；`briefrun.telegram_notify()` 用 `TELEGRAM_CLAUDE_BOT_TOKEN`（由 `secret_guard.secret` 讀）與 repo 外設定的 chat id 實際發出一則測試訊息，API 回 `ok: true`（使用者的 Telegram 應已收到「【測試】…」一則）。這只支持「當次互動 shell 曾送成功」，不取消其他執行環境的網路政策，也沒有留下可核對的去識別證據（repo 內無 API 回應或收件紀錄）。正式排程是 systemd user service，其網路未驗證，上線前需在該環境實際送一則。

限制：我是在互動 shell 測的，沒有在 timer 觸發的 service 內發過通知；兩者網路相同，但未逐字驗證。單次 `run()` 內「成功／休市 0 次、最終失敗 1 次、通知失敗不重試」由 fake sender 測試涵蓋；**跨執行去重未實作**，sender 的 API 回覆處理見第 6 節 B5。

### 2. 只瀏覽的權限驗證 — 已實測，有一處殘餘缺口

用正式呼叫參數各跑一次 `tests/permission_canary.py`（提示詞要求讀 `/etc/hostname`、建立 `/tmp` 測試檔、用 `browser_file_upload` 上傳、`browser_snapshot` 的 `filename` 指向 `/tmp`、同時讀 CNBC 報價）：

- Claude：讀檔失敗（沒有檔案工具）、建檔失敗、upload 工具不存在、snapshot 寫 `/tmp` 被 Playwright 擋（回報允許根目錄只有本次暫存目錄）、CNBC 報價成功（.DXY 101.821）。測試檔不存在。
- Codex：同樣五項結果；canary 與快照檔不存在。
- （舊敘述，已由第 6 節 B6 的事件串流證據取代）工具白名單：Claude `--tools ""` + `--allowedTools` 只列 10 個 Brave 工具；Codex `enabled_tools` 白名單 + `disabled_tools` + `features.shell_tool=false` + `read-only`。evaluate、upload、drop、run_code、screenshot、network_*、console 等都不在白名單。`test_commands_have_no_shell_or_upload` 驗證指令組成。
- 殘餘缺口：`browser_snapshot`／`browser_find` 有 `filename` 參數，工具層無法關掉；Claude 那次實測 Playwright 把寫入限制在本次空暫存目錄內，所以最壞情況是寫進該次暫存目錄（結束即刪）。Codex 那次是模型自行拒絕（回報「唯讀權限禁止」），不是工具層證據；Codex 沒有獨立證明 Playwright 的根目錄限制，但兩者用同一個 MCP 程式。
- 「CLI 繼承設定」：Claude 加 `--setting-sources ""`、`--disable-slash-commands`、`--no-session-persistence`；Codex 用 `--ignore-user-config`。我沒有逐一列舉 Codex 的 apps／multi-agent 等能力是否仍存在；只驗證了上述五項行為都被擋。這是未完全驗證的部分。

### 3. Brave 工作階段隔離 — 設計支持，整合驗證不完整

- 每次 CLI 呼叫各自啟動一個 Playwright MCP（`isolated: true`、`--incognito`），不共用 cookies。
- 同時執行：Claude 與 Codex 的 canary 同時跑，兩者都成功；我自己這個 session 的 MCP（pid 1441577，先於測試啟動）在整段期間存活，之後仍可用（瀏覽 nyse.com、本機 viewer 測試正常）。
- 逾時清理：用 `run_process` 逾時 25／30 秒殺掉一個正在瀏覽的 Claude；期間 Brave 相關程序峰值 21 個，殺掉後降到 0 個殘留（未殺到我自己的 MCP）。另有單元測試 `test_timeout_kills_process_group` 驗證子程序（`sleep` 背景程序）被終止。
- 限制：程序群組被殺不等於 Brave cookies／storage 已驗證清除；單一既存 MCP pid 存活不能單獨證明 bot 分頁未受影響；沒有雙 context cookie／storage 正反向證據。沒有測「Telegram bot 此刻正在瀏覽時排程同時執行」，只證明獨立程序、互不影響。`isolated` 的 cookies 隔離沿用 `check_browser.py` 的既有驗收，我沒重跑。

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

### 5. Pages 與時間目標 — 確認邏輯已實作，實際網站未驗證

repo 設定我不能改（AGENTS.md），GitHub Pages 目前**尚未啟用**（`gh api .../pages` 回 404）。因此「push 到網站可見」的耗時、09:30 前可見都沒有量測。Pages 確認已實作（第 6 節 B7），但只以假 HTTP adapter 測過，對真實網站未驗證。

### 6. 既有機器限制 — 遵守

沒有改時鐘、校時、既有 bot、`~/diag/`、SSH。共用 `bot_browser` 模組只引用未修改。

## 3. 與 design／spec 的差異與取捨

- **備援範圍**：design 4.1 要求「共用基礎設施失敗（Brave 掛了等）不備援」。程式無法可靠判別，統一依 spec 5.2：任何 Claude 失敗都試一次 Codex。Brave 全壞時兩者都失敗、發一次通知，結果相同，只是多花一次 Codex 啟動時間。
- **本機已 commit 但未推送**：design 7.2 允許「期限內只推既有 commit」的重跑；我只做「偵測到就停止並通知」（`LOCAL_UNPUSHED`），不自動補推，需人工處理。
- **通知去重**（跨執行未實作）：同一天重跑可能重複通知（沒有跨執行的狀態檔記通知是否已發）。timer 一天只觸發一次，風險低，未實作。
- **輸出大小上限**：stdout 最多讀 512 KiB+1 byte 就殺程序群組；Codex 輸出檔先看大小再讀（第 6 節 S1）。
- **期限**：用牆鐘（`datetime`）判斷，子程序 timeout 由 `subprocess` 內部處理；沒有另外偵測休眠／牆鐘跳動。
- **狀態紀錄**：只寫 log（階段、供應者、耗時、原因碼），沒有 design 7.2 的完整狀態機；重入靠 Git／檔案核對。
- **log 路徑**：`~/.local/state/cyhUsPremarketBrief/YYYY-MM-DD.log`，通知中會附此路徑（內含使用者家目錄名，非秘密）。
- **MarketWatch RSS**：prompt 有列，但試跑第一次 AI 沒有讀；來源是否讀取只能看 AI 自述的「資料來源」，無法機械證明。
- **中文對照表**：前半（NFP、ISM、JOLTS 等）依本週 XML 的實際名稱，其餘為常見 FF 名稱，未逐一對照現行 FF 事件；對照不到的 AI 只列英文。
- **秘密掃描**：發布前用 `secret_guard.GUARD`（與 `ai_secrets.py scan` 同一套）；工作樹檔案以 `ai_secrets.py scan` 掃描無命中；歷史 blob 掃描見第 6 節 B7。掃描器的正向案例：`test_scanner_hits_fake_token_and_not_plain_text` 用假 token 格式證明會命中。

## 4. 驗證結果（核對實際輸出）

- 第 1 輪修正後 `python3 tests/test_brief.py`：56 個測試 OK（原 35 個加第 6 節新增）。原 35 個涵蓋：涵蓋 spec 6.4 的 #16（感恩節／早收／週六）、#18（Claude 失敗→Codex、commit 帶 `codex`）、#19（兩者皆失敗：無 commit、通知 1 則）、#20（過 09:25：無 commit、通知）、#21（commit 只含兩檔）；每個「無 commit／無通知」案例都有對應的正向案例（成功發布能觀測到 commit、失敗能觀測到通知 1 則、假 token 能被掃描器命中）。
- 檢視器（本機 http.server＋Brave）：`<script>`、`<img onerror>`、`<svg onload>` 顯示為文字且未執行（`document.title` 未被改）；`javascript:`、`data:`、含帳密的連結不可點；合法 https 連結可點且帶 `noopener noreferrer`；Markdown 圖片只剩替代文字；清單中非法日期（`../../etc`、`2026-02-30`）被濾掉；報告 404 顯示「報告更新中，請稍後重試」。
- timer 範本：`TZ=Asia/Taipei systemd-analyze calendar …`：2027-03-11、03-12 為 22:00，03-15 為 21:00。**timer 未安裝**。
- 真實 git push 到 `cyhbench/cyhUsPremarketBrief` 的流程沒跑（只用本機 bare remote 測）；本次 commit 不含任何 `reports/` 內容。

## 5. 上線前仍待做（需使用者決定或授權）

1. **使用者**開啟 GitHub Pages（repo 設定，我不能改），再量測 push → 可見耗時；程式的 Pages 確認須用真實網站驗證。
2. 交易日盤前（美東 04:00–09:30）再做一次 dry-run，驗證 `PRE_MKT`、08:30 實際值、RSS 新鮮度；並記錄用量。
3. 部署：clone 到 TG VM 目標路徑、安裝 timer（見 `deploy/README.md`）。**未做，等驗收。**
4. 驗收條件 spec 6.3（連續 3 日抽查）、6.4 #17（連續 5 日時間）要上線後才能做。

## 6. 修正第 1 輪（回應 `docs/review.md`）

驗證指令：`python3 tests/test_brief.py`（56 tests OK）、`node tests/viewer_race.js`（OK）、`python3 tests/permission_canary.py claude|codex`（各 PASS，實際耗額度）。以下「負向控制」指把修正還原或餵反例，確認測試會失敗。

| 項 | 結論 | 怎麼改 | 怎麼驗證 |
|---|---|---|---|
| B1 | 同意，已修 | `publish()` 先 `mkdir(exist_ok=True)` 再寫報告 | `Fx` 不再預建 `reports/`（需要的測試自己建）；`test_fresh_clone_has_no_reports_dir_and_still_publishes` 斷言 HEAD 無追蹤的 reports、目錄不存在，發布後遠端兩檔都有 |
| B2 | 同意，已修 | service `TimeoutStartSec` 15min → 35min（09:00 起算涵蓋 Pages 確認到 09:30 加清理與通知） | 測試檢查 service 值 ≥ `deadlines` 推得的最晚結束 + 120 秒；另以真實程序（`sh -c "sleep 300"` 當 Claude、縮短 MIN_PROVIDER_SECONDS）驗證 Claude 被逾時殺掉後 Codex 仍完成發布、log 有 `claude:TIMEOUT`。**未安裝或觸發 systemd service**，所以 systemd 實際行為未驗證 |
| B3 | 同意，已修 | `run_process` 把 `Popen` 的 `OSError` 轉成 `ProviderFailed("LAUNCH_ERROR")`；`load_index`／前一份報告讀檔失敗轉 `Failure`（`INDEX_INVALID`、`BASELINE_UNREADABLE`）；`run()` 加最後防線，未預期例外走同一出口，log 記型別，通知只含固定訊息 | 測試用不存在的 CLI 路徑走真 launcher：只備援一次；兩個 CLI 都不存在→單次通知且不含路徑；損壞清單與缺 baseline 各通知一次且不呼叫 AI；`RuntimeError` 的內容不進通知 |
| B4 | 同意，已修 | 掃描後、add 前、commit 前、push 前各核對 09:24:30；add／diff／commit／push 的 timeout 都是 `min(60, 距 09:25 秒數)`，不再加 20 秒 | `test_no_push_when_commit_finishes_after_0924_30`（review 的 09:24:59 情境→`PUSH_SKIPPED_LATE`，push 未被呼叫、通知 1 則）；`test_git_timeouts_never_exceed_remaining_budget`（剩 50 秒→各步驟 timeout 50） |
| B5 | 同意，已修 | 拆出 `telegram_post()`；`run()` 檢查 notify 回傳值，`False` 記 `NOTIFY_FAILED API_NOT_OK`，例外記型別；不重試 | 假 HTTP adapter 測 `ok:true`／`ok:false`／缺欄位／timeout；`test_notify_false_is_logged_once_without_retry`。跨執行去重**仍未實作**（spec 未要求成功通知；已移除先前「去重由單元測試保證」說法） |
| B6 | 部分同意，已改判準；仍有缺口交使用者 | canary 改讀 CLI 事件串流（Claude `stream-json`、Codex `--json`）：Claude 的 init 有效工具清單須恰等於白名單；Codex 不得出現 command_execution／file_change；必須看到 `browser_snapshot` 寫到根目錄外被工具回 "outside allowed roots"（沒嘗試＝失敗）；禁止工具不得成功；CNBC 導覽須成功；檔案不得存在；不符 exit 1。單元測試改為解析最終 argv：`-c` 重複 key 即失敗、`-s` 只有一個、`--tools` 只有一個、白名單與封鎖清單不重疊 | 離線 `CanaryJudgeTests` 用合成串流涵蓋通過與 7 種失敗（含 review 的反例：空輸出、禁止操作成功、檔案存在）；review 的「尾端追加 `features.shell_tool=true`」負向控制已寫進測試且會抓到。**實跑**：Claude 2.1.308 與 Codex 0.164.0 各 PASS，兩者 `browser_snapshot` 寫 `/tmp` 都由工具層回 `File access denied … outside allowed roots`（允許根目錄是該次的暫存 cwd，結束即刪）。這推翻先前「Codex 只是模型自行拒絕」的說法。**仍有的缺口**：(1) 重複 `-c` 的最終解讀只靠「不允許重複」這道防線，沒有請 Codex 印出有效設定；(2) 「讀本機檔」在 Codex 側的證據是「沒有 shell／file 類 item」，非主動嘗試被拒；(3) 工具層仍允許寫入該次暫存目錄（無法由呼叫端關閉，共用模組我不改）；是否接受由使用者決定 |
| B7 | 部分同意，已修程式；實際 Pages 與驗收仍待使用者 | push 成功後再 `rev-parse HEAD` 對照 `ls-remote origin` 的分支 commit，不符→`PUSH_UNCONFIRMED`；再用 `pages_visible()` 輪詢（每 15 秒、至 09:30）確認 `reports/index.json` 含當日且 `reports/<日期>.md` 本文與產物一致，否則 `PAGES_NOT_CONFIRMED`，通知寫明「已推送、不是未發布」；未設確認器視為失敗 | 假 HTTP adapter：可見／等部署後可見／過期清單／404／內容不符／垃圾回應；`PUSH_UNCONFIRMED` 與 `PAGES_NOT_CONFIRMED` 各通知一次且遠端確有 commit。歷史秘密掃描：對 `git rev-list --objects --all` 的 27 個 blob 逐一跑 `ai_secrets.py scan`，0 命中；正向控制（假 Telegram token 格式）exit 1。掃描器只涵蓋它的規則與共用秘密檔，不證明「無任何其他秘密」。**還缺（使用者）**：開啟 GitHub Pages（`gh api …/pages` 仍 404）；開啟後須實測 push→可見耗時與對真實網站的 `pages_visible`；首次公開前建議重跑歷史掃描。**未啟用 timer** |
| S1 | 同意，已修 | stdout 只讀到 512 KiB+1 byte 即殺程序群組；Codex 輸出檔先 `stat` 大小再讀 | `yes` 無限輸出 10 秒內被擋（修正前實測 30 秒才逾時，這是上一輪留下的未完成修改，已修）；超大輸出檔拒收；剛好 512 KiB 接受 |
| S2 | 同意，已修 | `loadReport` 加序號，過期回應不寫畫面；單份報告失敗改 `bad()` 只換本文，不關選單，清單失敗才關 | `tests/viewer_race.js`（node＋stub DOM／fetch）：慢 A、快 B 時畫面為 B；404 後選單仍可用並能改看另一天；清單失敗才 disabled。負向控制：還原舊 `index.html` 時此測試失敗。**未用真實瀏覽器重跑 UI** |

### 特別抽查與測試退化

- 舊 progress 對通知、權限、Brave 隔離、dry-run 的敘述過度概括，已於第 2、3 節改成可支持的範圍；兩次 dry-run 的報告與工具紀錄當時沒有保留，無法重核，保持「未重核」。
- review 列的退化實驗已轉成測試：`telegram_notify` 回 `False`（B5）、缺 `reports/`（B1）、啟動類例外（B3）、09:24:59（B4）、相反 `-c`（B6）、canary 反例（B6）。我沒有逐一在記憶體重做 review 的每個 mock 實驗，只確認上述測試對應的情境會失敗：viewer 與 argv 重複設定做過負向控制，其餘靠新測試直接斷言該行為。
- 未驗證：真實盤前資料（`PRE_MKT`、08:30 實際值、RSS 新鮮度）、用量、systemd 實際行為、Pages 實際時間、連續日驗收。

## 7. 修正第 3 輪（回應 `docs/review.md` 第 2 輪；使用者同意範圍：R2-B4、R2-B7、R2-S1、R2-S2）

驗證：`python3 tests/test_brief.py`（60 tests OK，原 56 加 4 個新測試與擴充的 canary 案例）、`node tests/viewer_race.js`（OK）。先寫測試、在未改程式時執行：新增的 5 個案例（4 個新測試加擴充的 `test_codex_shell_item_fails`）全部失敗，修正後全綠。未啟用 timer，未跑付費 CLI canary。

| 項 | 結論 | 怎麼改 | 怎麼驗證 |
|---|---|---|---|
| R2-B4 | 同意，已修 | `publish()` 把期限檢查移到秘密掃描之後、寫兩檔之前（原檢查在掃描前，掃描後才寫檔） | `test_scan_crossing_hard_deadline_writes_no_report_files`：AI 完成於 09:24:29、掃描耗 32 秒 → `DEADLINE_EXCEEDED`，報告與清單均未建立、`git status` 乾淨、通知 1 則。修正前此測試失敗（兩檔存在）。原有成功發布案例仍通過，作為正向對照 |
| R2-B7 | 同意，已修 | `pages_visible` 每次請求前檢查剩餘預算（已逾期不發請求），請求 timeout 取 `min(10, 剩餘秒數)`，回應收到後若已逾 `until` 不算成功 | `test_pages_success_after_deadline_is_false`（09:29:59 起、每請求耗 8 秒 → False，且第一次 timeout ≤ 1）、`test_pages_already_past_deadline_makes_no_request`（逾期零請求）。修正前兩者失敗。期限內成功／等部署後成功／404／過期清單／內容不符等原案例仍通過 |
| R2-S1 | 同意，已修 | canary 的 Codex 項目若 server 不是 `brave`，名稱保留 `server:tool` 前綴；`evaluate` 改為「任何不在 `BRAVE_TOOLS` 白名單的成功呼叫即失敗」（原本只比對黑名單，Claude／Codex 共用） | `test_codex_shell_item_fails` 加三個反例：其他 server 的 `read_file`、同名但 server 錯誤的 `browser_navigate`、brave 但不在白名單的工具，均被判失敗；原通過串流仍為空錯誤清單。既有合成串流補上 `server: "brave"`。**限制**：Codex 事件含 `server` 欄位是依測試資料與慣例假設，未用實跑串流核對；canary 本身沒有重跑（耗額度），所以真實 Codex 輸出是否仍 PASS 為**未驗證**，下次手動跑 canary 時確認 |
| R2-S2 | 同意，已修 | 最後防線只記例外型別（`UNEXPECTED RuntimeError`），不記訊息 | `test_unexpected_exception_text_never_reaches_log`：含假標記與 ESC 控制字元的 `RuntimeError`，log 有型別、無標記、無 `\x1b`；修正前失敗。舊測試仍斷言通知不含原始訊息 |

剩餘風險：Pages 對真實網站的確認、systemd 行為、canary 實跑等仍如第 5、6 節所列未驗證；請求 timeout 取整數以下的小數秒由 `urllib` 接受，未對真實網路驗證。

## 8. UI 實作（第 3 步，依 [ui-design.md](ui-design.md)）

使用者本次決定與附帶要求：漲跌色用 B（漲綠跌紅，已記進 spec「已決定」）；marked 內嵌；VERSION；文件抬頭；dry-run 標示；prompt 改白話；格式收斂不做。**timer 未啟用。**

### 改了什麼

| 項 | 內容 |
|---|---|
| `index.html` | 依 ui-design 重寫：頁首＋日期選單＋「最新一份」、導言面板（日期大標、產生時間、試跑標示、漲跌圖例、七節目錄）、七節面板（編號 01–07）、資料來源 `<details>`（預設收合，附項數）、頁尾（網站版本、資料產生時間、免責）。表格：表頭單層、非空、不重複、欄數一致時，手機（<768px）轉卡片、電腦保留表格；否則整張保底表格＋局部橫捲容器（可聚焦、`role=region`、溢出才出現「左右滑動查看完整表格」、邊緣陰影）。已知表頭才提升卡片標題／放大主值，多個候選則不挑。漲跌色只套在「漲跌點數／漲跌幅」欄、有明確 `+`／`-` 的值；零用次要色；無正負號不上色。狀態面板依 ui-design 6.2（清單 404／失敗／格式／空、報告 404／失敗／空／錯日期、重試按鈕、載入骨架）。日期用 `#YYYY-MM-DD`，上一頁／下一頁走 hashchange；節目錄只捲動並移焦點，不改網址。`prefers-color-scheme` 跟隨系統。完全沒有外部資源 |
| marked | 內嵌 4.3.0（取自本機 `telegram-codex-bot/vendor/marked.min.js`，檔頭標示 v4.3.0，MIT）；MIT 授權全文放在 `index.html` 的註解。**未與 CDN 檔逐位元組比對**（本次沒有連外下載），版本只依檔頭 |
| `VERSION` | 新增，內容 `0.1.0`。頁尾以 `no-store` 讀取；缺檔、空白、HTTP／網路失敗皆顯示「網站版本：無法取得」 |
| 文件抬頭 | spec（1.1）、design（1.0）、progress（1.1）、review（1.0）補「文件版本」「日期」；ui-design 本來就有，未動 |
| dry-run 標示 | `reportcheck.header(..., dry_run)`：試跑報告多一行「🧪 試跑報告（dry-run）：未發布、非排程產出；AI 被告知的時點是模擬的…」，`validate(..., dry_run)` 要求有此行；「產生時間」改用實際時間（原本是模擬時鐘的 09:0x，是錯的）。正式報告格式不變 |
| `prompts/brief.md` | 讀不到時須寫「⚠️ 無法取得」並用白話說明哪項資料、為何沒有；禁用 `REG_MKT` 等欄位名，並附正反範例 |
| 測試 | `tests/test_brief.py` 61 個（新增 dry-run 標示、實際產生時間、prompt 文案）；新增 `tests/viewer_browser.js`＋`tests/fixtures/`；刪除 `tests/viewer_race.js`（它用假 DOM，已不適用新頁面） |

### 驗證（核對實際輸出）

- `python3 tests/test_brief.py`：61 tests OK。
- `NODE_PATH=/opt/agent-node-tools/lib/node_modules/@playwright/mcp/node_modules node tests/viewer_browser.js`：146 項斷言通過。用本機 Brave（headless）執行，站台由 route 攔截供應，並固定美東今日以免隨日期變動。涵蓋：320／390／768／1280 × 淺色／深色各自載入，七節齊全、整頁無水平溢出、dry-run 樣本所有表格儲存格文字皆出現在畫面；資料來源收合／展開、網址完整；漲綠跌紅實際顏色（rgb(19,115,75)／rgb(180,35,50)）、價格與零不上色；超寬表橫捲、僅用方向鍵能捲到最後一欄、提示只在溢出時顯示；惡意 HTML／`javascript:`／帳密連結／圖片（不執行、無 img／iframe、只剩合法 https 且帶 `noopener noreferrer`）；所有請求皆同站；頁尾版本與產生時間（取自報告原始行，切換日期、上一頁／下一頁不殘留）；節目錄焦點與網址不變；VERSION 缺失／空白／500／中斷；慢 A／快 B；清單 404／500／壞 JSON／壞 schema／空／全非法日期、報告 404／500／空／錯日期的畫面與日期選單狀態、換日期與手動重試成功；日期提示（找不到、歷史、最新非今日、今日）；觸控目標 ≥44px、焦點外框 3px。
- 負向控制：把 `.scroll` 改成不橫捲、並讓原始 HTML 不跳脫後，測試在「無水平溢出 768/light」失敗；已還原。
- 另用 Brave MCP 實際開本機站台看截圖：390 淺色（導言、第 3 節卡片）、390 深色（第 6 節）、320 深色（導言）、768 深色（第 2、3 節表格）、1280 淺色（第 1 節兩欄頭條、第 2 節表格）、640 寬（約 1280 的 200% 縮放）；各尺寸 `scrollWidth ≤ innerWidth`。
- 色彩對比以公式計算（淺／深各 19 組）：文字、次要文字、連結、主要按鈕、注意／錯誤面板、漲／跌色（在白／次級面板上 5.1–9.2）皆 ≥ 4.5:1；控制項邊界與焦點 ≥ 3:1（最低 3.16）。

### 未驗證／限制

- **200% 縮放**：用 640px 寬視窗等效（768 以下版型），不是瀏覽器的實際縮放；字級放大（只放大文字）沒測。
- **讀屏**：只檢查標籤與 role 的存在（`role=region`、`aria-label`、`dt／dd`、`aria-live`、`aria-busy`），沒有用實際讀屏軟體。
- 只測 Brave（Chromium）；Safari／Firefox 未測。`scroll-hint` 陰影僅目視確認寬度正常，沒有逐像素驗證。
- 與 ui-design 的差異：(1) 期貨與其他表格在電腦一律用表格，沒做「三張等寬卡片並排」（4.4）；(2) 第 1 節頭條是以「清單項目」為單位的卡片，沒處理頭條為表格的情況（走一般表格規則）；(3) 沒有「重新整理清單」專用按鈕以外的自動刷新；(4) 手機標題欄的原表頭以視覺隱藏文字保留給讀屏。
- 報告 H1 的日期在窄螢幕會在連字號處換行（`2026-11-` ↵ `02`），僅外觀問題。
- 試跑報告的「產生時間」改用實際時間後，與「今日開盤」等資訊的時點可能看起來矛盾，這是刻意以 🧪 標示說明。
- `data/`、排程、`bin/` 其餘行為未變；Pages 仍未啟用，真實網站上的檢視器未驗證。

### 待使用者決定：格式收斂提案（ui-design 第 9 節，本次**未做**）

七個 H2 與一般表格單層表頭已能被通用卡片／橫捲保底處理；以下為提案，要做須同步改 prompt、格式檢查與代表性報告驗證，並接受額外 prompt／驗證成本：

1. 經濟數據固定欄位語意（美東時間、台灣時間、事件、預期、前值、實際值）。
2. 亞太：固定指數、報價、漲跌幅、資料時間；收盤未確認時表頭與內文明示。
3. 期貨：分列期貨、合約月份、報價、漲跌點數、漲跌幅、資料時間（含時區）。
4. 美元指數：改為一列標準表格，才有專用大數字卡（目前段落仍有效）。
5. Mag 7：前一交易日收盤、盤前價格、相對前收漲跌幅、資料時間分欄。
6. 復盤：各表帶完整表頭。

不做的代價：未來報告若表頭不同，主值不會被放大（但資料完整保留）。請決定要全做、部分做或不做。

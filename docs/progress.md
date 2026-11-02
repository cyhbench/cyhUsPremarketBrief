# 進度：美股盤前快報（實作）

實作者：Claude，日期 2026-11-02。依據 [spec.md](spec.md)、[design.md](design.md)。**排程 timer 尚未啟用**（等使用者驗收）。第 1 輪修正見第 6 節（2026-11-02，Claude）。下一步：Codex 第 2 輪 review，再由使用者驗收。

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
| `tests/test_brief.py` | 56 個測試，`python3 tests/test_brief.py` 全數通過；`tests/viewer_race.js`（node）測檢視器日期切換 |
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

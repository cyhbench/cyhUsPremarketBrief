# 實作 Review（第 1 輪）

審查者：Codex；日期：2026-11-02。結論：**有擋關問題，尚不宜部署或驗收通過**。下一步由 **Claude** 修正程式、測試並更新 `docs/progress.md`，再交第 2 輪 review；使用者負責驗收。

範圍：先執行 `git pull --ff-only`（`Already up to date.`），閱讀 `AGENTS.md`、`git log` 與 `git diff 643f272..HEAD`，對照 `docs/spec.md`、`docs/design.md`。受審 HEAD 為 `81d7440`，此範圍只有一個實作提交，共 15 檔、1,408 行新增。本輪只新增本文件，未修改程式、規格、設計或進度。

## 擋關

### B1 — 全新 clone 的首次正式發布必定缺少輸出目錄

- **位置**：`bin/briefrun.py:201`；`tests/test_brief.py:191`；`deploy/README.md:6`。
- **具體後果**：repo 沒有追蹤任何 `reports/` 檔案；依部署說明 clone 後，目錄不存在。AI 即使產報成功，`open(report, "x")` 仍拋 `FileNotFoundError`，不 commit、不 push，且不走通知。違反 spec 5.2、6.4 #19 的失敗處理目標。
- **證據**：`git ls-tree -r --name-only HEAD reports` 無輸出；這不是單憑空輸出推論：沿用能成功推至本機 bare remote 的 `RunTests` fixture，只移除它額外建立的空 `reports/`，實際得到 `fresh_clone: FileNotFoundError notices=0 report_dir_exists=False`。正式程式未建立此目錄，fixture 的 `mkdir()` 掩蓋了問題。
- **修正驗證**：以真正全新 clone 的目錄形狀測首次發布；程式應自行初始化輸出目錄，且測試仍核對遠端兩檔內容。

### B2 — service 會在 Claude 截止時終止整個備援流程

- **位置**：`deploy/cyh-us-premarket-brief.service:9`；`bin/briefrun.py:46`、`:167`。
- **具體後果**：09:00 啟動的 `Type=oneshot` 設 `TimeoutStartSec=15min`。當 Claude 用滿到 09:15，systemd 已到 service 啟動逾時，可能連剛啟動的 Codex 一起終止，無法使用 spec 5.1 預留到 09:24 的備援時窗，也不能保證由包裝器送失敗通知。
- **證據**：範本的 900 秒總上限，小於程式允許的約 1,440 秒生成及後續發布時間；35 個測試直接呼叫 Python，沒有載入 service。此為設定與時窗的靜態矛盾，**本輪未安裝或觸發 service**。
- **修正驗證**：service 的最後防線需容納完整生成、發布、通知與清理；以縮短時間的隔離 service 或等價整合測試驗證 Claude 逾時後 Codex 仍能完成，不動既有服務。

### B3 — 程序無法啟動等實際失敗會跳過備援及通知

- **位置**：`bin/providers.py:33`、`:102`；`bin/briefrun.py:175`、`:234`；`bin/briefrun.py:72`。
- **具體後果**：`Popen` 的 `OSError`（如執行檔不存在／無執行權限）沒有轉成 `ProviderFailed`，`generate()` 也不接它，外層只接 `Failure`。Claude 無法啟動時直接 traceback，不試 Codex、不通知。類似地，清單 JSON 損壞或前一份報告讀檔失敗，也會繞過失敗通知。違反 spec 5.2–5.3、design 4.1 的啟動失敗備援與統一失敗出口。
- **證據**：在原有 fixture 令 Claude adapter 丟 `FileNotFoundError`，實際得到 `missing_cli: FileNotFoundError fallback_calls=[] notices=0`；原測試只丟預先包好的 `ProviderFailed`，沒有走 CLI 啟動邊界。B1 亦實際重現一般檔案例外漏通知。
- **修正驗證**：以不存在的 CLI 路徑測真正 launcher，確認只備援一次；另測兩者無法啟動、損壞清單與缺 baseline 檔時的安全原因碼、log 與單次通知。不要把原始例外或秘密直接送出。

### B4 — push 的啟動與 timeout 沒有遵守發布預算

- **位置**：`bin/briefrun.py:196–216`（重點為 `:197`、`:212`、`:216`）。
- **具體後果**：09:24:30 的檢查只在掃描、寫檔、add、commit 之前；真正 push 前只檢查 09:25。commit 可以花固定 60 秒，push timeout 又在剩餘時間上加 20 秒，因此會在 09:24:30 後啟動 push，並容許程序跨過 09:25。違反 spec 5.1 與 design 2.2 的階段／總預算。遠端無法原子截止的限制，不能解釋本機主動多給 20 秒。
- **證據**：沿用成功 fixture，僅令 commit 完成時的注入時鐘為 09:24:59；觀測到真正 push adapter 被呼叫，timeout 為 **21 秒**，流程回 `PUBLISHED`。既有 `test_past_0925_no_commit` 只測 AI 完成時已到 09:26，沒有涵蓋發布中的時間消耗。
- **修正驗證**：每個發布步驟都扣除總預算，push 前重新核對 09:24:30；測掃描／add／commit 耗時與 push timeout 邊界。不能因測試時鐘固定，就宣稱已驗證總期限。

### B5 — Telegram API 回覆失敗會被靜默忽略

- **位置**：`bin/briefrun.py:144`、`:238`；`tests/test_brief.py:199`；`docs/progress.md:32`。
- **具體後果**：`telegram_notify()` 對 HTTP 成功但 `ok: false` 回 `False`，呼叫端卻忽略回傳值，只記拋例外的失敗。使用者沒有收到通知，log 也沒有 `NOTIFY_FAILED`；不符 spec 5.3「通知發送失敗寫記錄」。
- **證據**：用兩個失敗的 AI adapter、令 notify 回 `False`，流程回 `FAILED`，但 log 中 `NOTIFY_FAILED` 為 `False`。另將真實 `telegram_notify` 在記憶體中替換成永遠回 `False`，**35 個測試仍全部通過**。fixture 注入的是 `list.append()`，不是 Bot API 回應；唯一通知失敗測試只涵蓋拋 `OSError`。
- **修正驗證**：用假的 HTTP adapter 直接測 sender 的 `ok: true`／`ok: false`／timeout，再測呼叫端確實記錄失敗且不重試。跨執行去重並未實作，不能沿用 progress「去重由單元測試保證」的說法；本項不要求新增成功通知。

### B6 — 權限驗收的證據與失敗判準不足

- **位置**：`tests/permission_canary.py:9–25`；`tests/test_brief.py:150–160`；`bin/providers.py:14–21`、`:51–70`；`docs/progress.md:34–49`。
- **具體後果**：canary 只印模型回答與檔案是否存在，沒有斷言、失敗 exit code 或有效工具清單。禁止操作成功、報價未取得、甚至 provider 失敗，都能正常結束。模型拒絕一次或 `/tmp` 測試檔不存在，不能證明工具層禁止讀寫。progress 承認 snapshot／find 的 `filename` 仍開放、Codex 只自行拒絕，這也未達 spec 3.3、8「連單一暫存檔都不寫」與 design 5.1 的邊界。
- **證據**：以記憶體 mock 讓 provider 回「讀取本機成功；禁止的寫檔與上傳成功」，並讓兩項檔案檢查回 `True`，canary 仍正常 return，印出 `canary exists: True | snapshot file exists: True`。這是測試判準的反例，**沒有實際執行禁止操作**。另在 Codex argv 尾端加入相反的 `-c features.shell_tool=true`，`test_commands_have_no_shell_or_upload` 仍通過：它只找子字串，不能驗證最終有效設定。本輪未啟動 CLI 證明重複設定的實際解讀。
- **修正驗證**：可判定的 canary 結果必須決定測試成敗，保留去識別的工具呼叫／工具拒絕證據、CLI 與共用模組版本，核對完整有效能力；必要工具的寫檔參數需有實際限制。不得將「模型沒嘗試」當權限拒絕，也不得自行改既有 bot／共用模組；若現有介面無法符合已決定的限制，回報缺口交使用者處理。

### B7 — Pages 發布成功判準與首次公開前驗證仍未完成

- **位置**：`bin/briefrun.py:216–219`、`:282–283`；`docs/progress.md:63–65`、`:82`；design 7.1 #9、9.2、10 #5；spec 6.1 #1、#5 與 6.4 #17。
- **具體後果**：push 回 0 就回 `PUBLISHED`，沒有確認遠端目標 commit 或 Pages 內容；網站未部署／過時也不通知。這是**未實作**，不能只列為等網站開啟後的測量工作。另外 Git 歷史秘密掃描仍只宣稱查過 chat id，尚未支持「含歷史無其他秘密」的驗收結論。
- **證據**：本輪 `gh api repos/cyhbench/cyhUsPremarketBrief/pages` 回真正的 **HTTP 404**；`gh auth status` 成功，並非用連線失敗推論 token 失效。progress 亦明載缺少 Pages 確認與完整歷史 blob 掃描。程式 `PUSH_CONFIRMED` 後立即成功返回，沒有可見性流程。
- **修正驗證**：先用隔離 adapter 測「Git 成功但 Pages 失敗／過期」的發布狀態及通知；實際 Pages 開啟需使用者處理 repo 設定，再補 push、可見時間與內容核對。首次公開報告前補可達歷史 blob 的核准秘密掃描及非真實秘密正向控制。本輪**未判定歷史有洩漏，也未宣稱掃描通過**。

## 建議

### S1 — 超大輸出的限制發生得太晚

- **位置**：`bin/providers.py:36–47`、`:102`；`docs/progress.md:76`。
- **具體後果**：`communicate()` 已把 stdout 全部放入記憶體，才檢查 512 KiB；Codex 最終輸出檔也先整份讀入，才由報告驗證擋大小。異常 CLI 大量輸出可能先耗盡記憶體，讓正常失敗通知也無法執行。
- **證據**：檢查順序可直接從程式確認；progress 已承認 stdout 非串流限制。現有測試有報告大小拒收，沒有對程序輸出上限的測試；本輪未做耗盡資源實驗。
- **建議與代價**：在讀取邊界實施上限，需增加程序／檔案讀取測試；這是 design 2.2 原有的資源限制，不要求新增資料來源或功能。

### S2 — 切換日期可能顯示舊請求結果，失敗後無法改選

- **位置**：`index.html:83–86`、`:95–105`、`:129`。
- **具體後果**：快速由日期 A 切到 B，若 A 的 fetch 較慢完成，會覆蓋 B 的本文與 title，但選單仍是 B；一次 404 又會將整個選單 disabled，不能直接改看已有報告。影響 spec 6.1 #2 的歷史切換。
- **證據**：`loadReport()` 沒有 request id／取消或比對目前選單；change handler 直接啟動非同步載入，`fail()` 一律關閉選單。此項為靜態控制流程證據，**本輪未重跑瀏覽器 UI**。
- **建議與代價**：加入過期回應保護，並區分清單失敗與單份報告失敗；用可控制回應先後與 404 的兩日期測試即可，不需新增前端框架。

## 特別抽查：Claude 宣稱的實測證據

| 宣稱／位置 | 本輪核對 | 可支持的結論與限制 |
|---|---|---|
| 通知可連線、實際 `ok: true`（progress 2.1） | repo 只有文字敘述，沒有可核對的去識別 API 結果或收件證據；未發真實訊息、未讀 token／chat id | 即使敘述屬實，也只支持當次互動 shell 曾送成功，不能取消其他執行環境的網路政策。302 只支持當次端點可連線；不支持 systemd 發送已驗證。本輪遵守 shell 只允許 GitHub 的限制，不重跑 Telegram HTTP |
| 通知一次／去重（progress 2.1、3） | fake sender 次數確有正向案例，B5 實驗則揭露 `False` 漏記；progress 3 又明載未做跨執行去重 | 只支持單次 `run()` 內呼叫一次；不支持實際收件、API 回覆處理或跨執行去重 |
| 本機讀寫／upload 被擋（progress 2.2） | 檢視正式 argv、canary 與單元測試，並做 B6 的反例實驗 | 可以支持有縮減工具的設定意圖；沒有保留的有效工具清單與拒絕紀錄可重核，且 canary 不會自動失敗。不能把敘述升格為完整權限驗收通過 |
| Brave 隔離、逾時清理（progress 2.3） | 唯讀檢視共用 `bot_browser.py` 及 `brave.json`：確有各次 CLI 的 MCP 設定、`isolated: true`、`--incognito`；程序群組 timeout 單元測試本輪通過 | 設定支持獨立 context 的設計；sleep 子程序被殺，不等於已驗證 Brave 的 cookies／storage 清理。單一既存 MCP pid 存活，不能單獨證明 bot 分頁與狀態未受影響。缺少雙 context cookie／storage 正反向證據與同時瀏覽操作紀錄；本輪不宣稱隔離失效，也不宣稱整合驗證通過 |
| 兩次 dry-run、78 秒、報價／內容改善（progress 2.4） | repo 無報告／工具 transcript；查 spec 所定預設 state 目錄得到 `No such file or directory` | 目前可得產物無法重核報價、完整內容與耗時；不據此斷言從未執行，可能使用其他目錄，但 progress 沒有提供可重核位置。亦不能由模擬 09:00 推論真實盤前資料已驗證 |
| Pages／真實時窗與盤前資料（progress 2.4–2.5、5） | Pages API 404；`PRE_MKT`、08:30 實際值、RSS 新鮮度、用量與連續日驗收明列未驗 | 保持未驗證。Pages 確認的實作缺漏見 B7；三日內容／五日時間需之後實際上線驗收，未要求在本輪假造結果 |

原 design 10 #1 對「此環境 shell 網路限制」的描述仍適用本輪。不同宿主曾連得上 Telegram，不構成跨環境的權限授權或部署證據；應修正 progress「阻擋判斷不成立」的過度概括。

## 特別抽查：測試是否能抓到退化

本輪以工作目錄內的一次性 `TMPDIR`、`PYTHONDONTWRITEBYTECODE=1` 執行 `python3 tests/test_brief.py`：**35 tests，OK**。本機 bare remote 有實際 commit／push 的正向案例，失敗通知有能觀測到 1 次的 fake sender，秘密掃描有非真實 token 命中的正向案例；因此不把整套測試評為「只要跑完就通過」。

另以標準函式庫 `unittest.mock` 在記憶體注入退化，未修改受審程式：

| 實驗 | 實際結果 | 判讀 |
|---|---|---|
| 原套件 | 35/35 通過 | 可重現 progress 的單元測試數量與結果 |
| 停用 `reportcheck.validate`，跑 `test_rejections` | 1 個 failure | 正向控制：格式拒收測試真的會抓到此退化 |
| `telegram_notify` 永遠回 `False`，重跑全套 | 35/35 仍通過 | 真實 sender 完全壞掉仍漏檢，見 B5 |
| Codex argv 追加相反的 shell 設定，跑權限指令測試 | 通過 | 只比子字串不足以辨識衝突／最終設定，見 B6 |
| canary 模擬禁止操作成功、兩檔存在 | 正常 return | 手動腳本沒有失敗判準，見 B6 |
| 去掉 fixture 額外建立的空 `reports/` | `FileNotFoundError`、通知 0 | fixture 與部署現實不符，見 B1 |
| Claude adapter 丟啟動類 `FileNotFoundError` | 例外外漏、Codex 未呼叫、通知 0 | 沒涵蓋真實 launcher 的失敗出口，見 B3 |
| commit 後推進注入時間到 09:24:59 | push 被呼叫，timeout 21 秒，回 `PUBLISHED` | 沒涵蓋發布步驟的預算消耗，見 B4 |

本輪沒有消耗 AI 額度重跑 canary／完整產報，沒有發通知、啟用 timer、改 repo 設定、變更既有 bot／共用模組或更動系統時鐘。上述整合項目與 UI 行為未實測的部分均已明示；不以 35 個單元測試通過代替完整驗收。

# cyhUsPremarketBrief 協作規則

美股盤前快報：GitHub Pages 靜態網站，報告由 TG VM 排程產生。需求見 `docs/requirement.md`，規格見 `docs/spec.md`。

## 分工（使用者決定，AI 不得更改）

| 步驟 | 負責 | 產出 |
|---|---|---|
| 規格 | Claude | `docs/spec.md` |
| 設計 | Codex | `docs/design.md` |
| 實作 | Claude | 程式、測試、`docs/progress.md` |
| Review | Codex | `docs/review.md` |
| 修正 | Claude | 程式、`docs/progress.md` |
| 驗收 | 使用者 | — |

## 交接

- 開工先 `git pull`，讀上一步的產出檔；不依賴聊天紀錄。
- 只做自己這一步。做完 commit、push 後停下，回報結果並寫明下一步由誰做；不接著做下一步，也不提議代做別人的步驟。
- 上一步的產出有問題：寫進自己的產出檔並回報使用者，不直接改別人的產出檔。
- Review 與修正最多 2 輪，仍有擋關問題就停下與使用者討論。
- 使用者的決定記在 `docs/spec.md`「已決定」一節；AI 不得自行新增決定。
- 每一步的負責者在自己這一步的 commit 裡更新 `docs/status.json`（格式見 cyhBotReliability `docs/spec.md` 第 8 節）：`step`、`owner` 改成下一步與下一步的負責者，`updated` 取實際系統時間，`note` 寫下一步要做什麼或在等什麼。
- 停下來等使用者時（待使用者決定、review 與修正超過 2 輪、等驗收）設 `blocked`，`owner` 寫「使用者」，`note` 寫等什麼。
- 使用者驗收通過後，由使用者指示的人改成 `done`。AI 不自行設 `done`。

## 共通限制

- repo 是 public：不放秘密、token 或 chat id。
- 可以 commit、push 到 main；不 force push、不改 repo 設定、不刪分支。
- 判斷限制（網路、權限、檔案存取、可用工具）時，寫明是在哪個環境測到的：現在執行的沙箱或互動 shell 不等於正式上線環境（systemd 排程、正式部署 clone）。一處的結果不直接套到另一處；沒在正式環境實測就標「未驗證」，不寫成阻擋或已通過。

# 需求：美股盤前快報（使用者原文，2026-11-02）

能否使用github的靜態網站，開發一個"美股盤前"：

內容包含：
美股盤前快報 (US Premarket Brief)
在紐約證券交易所 (NYSE) 開盤前的 30 分鐘內，自動生成一份關於美國股市的 Markdown 格式快報。此腳本具備日光節約時間 (DST) 感知能力，使用真實的 NYSE 交易日曆

主要內容：
1.重要美國市場頭條新聞
2.當日重要美國經濟數據發布
3.亞太地區早盤指數
4.主要期貨指數
5.美元指數 (U.S. Dollar Index)
6.重點美股
7.上一個交易日（依據最新儲存的報告）的復盤總結

此靜態網站，是跟:
我的個人工具網站一樣，點開就能用，因為是美股盤情資訊，所以public是OK的：
https://cyhbench.github.io/markdownReader/
https://cyhbench.github.io/qrCodeGenerator/

## 參考資料（整理者補充，非需求）

- 既有的 Claude skill `us-premarket-briefing`（TG VM：`~/.claude/skills/synced/*/us-premarket-briefing/SKILL.md`）：盤前速報以 Telegram 發送的版本，列有資料來源（ForexFactory 經濟日曆 XML、Business Insider 盤前頁、Finviz 盤前異動、CNBC／Reuters／MarketWatch RSS）、篩選規則、中文對照表，以及「抓不到就標示，不得編造數字」的原則。來源是否仍可用、從 GitHub Actions 是否會被擋，都未驗證。
- 兩個參考網站都是 cyhbench 帳號下的 public GitHub Pages。

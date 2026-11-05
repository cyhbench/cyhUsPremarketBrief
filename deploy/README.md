# 部署（TG VM）

已部署並啟用（2026-11-02 起每個交易日發布，2026-11-06 使用者驗收通過，見 `docs/progress.md` 第 10 節）。

```
mkdir -p ~/.config/systemd/user ~/projects
git clone https://github.com/cyhbench/cyhUsPremarketBrief.git ~/projects/cyhUsPremarketBrief
cp deploy/cyh-us-premarket-brief.* ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now cyh-us-premarket-brief.timer
systemctl --user list-timers cyh-us-premarket-brief.timer
```

通知設定在 repo 外 `~/.config/cyhUsPremarketBrief/config.json`（`{"chat_id": <數字>}`，權限 600）。
service 的 `WorkingDirectory`／`ExecStart` 路徑是範本，clone 到別處時要改。

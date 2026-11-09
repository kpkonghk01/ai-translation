# ai-translation

AI 翻譯（繁→英、繁→簡）的評核框架研究。

- 決定與理由：`docs/consensus-summary.md`；用詞以 `GLOSSARY.md` 為準。
- `data/` 的內容怎樣產生、怎樣重做：`docs/data-pipeline.md`；目前進度：`docs/status.md`。
- 檢查：`tools/check.sh`。commit 時由 hook 自動跑；新 clone 先執行一次 `git config core.hooksPath tools/hooks`。
- 真實文章、ground truth、譯文只放在 `data/`（git 排除），只在本機檢視（`preview.html`），不上傳到 Artifact 或其他托管服務。
- 測試只用虛構資料：虛構標題與人名，案件編號用 2030 年或以後的年份。
- API key 由 Pan 放在 `.env`；程式只從環境變數讀 key。agent 不讀 `.env`（permission 設定已擋），用到 key 的指令（translate、judge、calibrate、crosscheck、providers）經 `tools/run-real.sh` 執行；Pan 可自己跑，agent 代跑要 Pan 先放行 `Bash(tools/run-real.sh *)`：在 `.claude/settings.local.json`（已被 git 忽略）寫 `{"permissions": {"allow": ["Bash(tools/run-real.sh *)"]}}`。agent 不能自己放行。

## Agent skills

### Issue tracker

本機 markdown：spec 與 ticket 放在 `.scratch/<feature>/`，隨 repo 進 git。見 `docs/agents/issue-tracker.md`。

### Triage labels

預設的五個角色 label（needs-triage、needs-info、ready-for-agent、ready-for-human、wontfix）。見 `docs/agents/triage-labels.md`。

### Domain docs

Single-context：根目錄的 `GLOSSARY.md` 與 `docs/adr/`。見 `docs/agents/domain.md`。

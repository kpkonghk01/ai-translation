# 目前進度

Last updated: 2026-11-06。開了 ticket 之後改由 ticket 追蹤進度，本檔即可刪除。

## 已完成

- 測試集候選：hk01 網上文章 248 篇（另有 CMS 匯出 15 篇）、中國負面新聞 131 篇。篩選後：第一批 30 篇（25 篇敏感 + 5 篇對照，其中 3000 字以上 11 篇）、中國負面新聞組 30 篇（10 個子類各 3 篇）。
- 簡體 baseline：主版本已為 248 + 131 篇產生；CMS 的 15 篇未做。
- Must-retain fact：
  - CMS 15 篇：33 句、119 條，Pan 已同意。
  - 第一批加中國組共 60 篇：291 個 item（不重覆句子 289）、1059 條事實；general 敏感 25 篇（152 句、593 條）、china-political 30 篇（136 句、456 條）、對照 5 篇（3 句、10 條，其中 3 篇沒有 item）；29 篇有原文瑕疵，共 53 處。程式驗證全部通過，事實正確與否未經人審。
- Guardrail：`tools/check.sh` 與 pre-commit hook（`tools/hooks/pre-commit`）已啟用。

## 等 Pan

- 審閱 `data/gt-drafts/review-sample.md`（14 篇、69 個 item、244 條事實）。
- Q34：NOT-NEGATIVE? 的 2 篇怎樣計算（見 consensus-summary 第 7 節）。
- 申請阿里雲 whitelist。Gemini 與 OpenRouter 的 key 已放進 `.env`（2026-11-06）。

## 下一步

評核框架的 spec 與 17 張 ticket 在 `.scratch/evaluation-framework/`，從 `issues/01` 開始，依每張的 `Blocked by` 進行。給上司的匯報頁 `docs/briefing.html` 是某一天的快照，數字取自本檔；改了本檔的進度，匯報頁也要一併重做。

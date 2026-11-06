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
- 準備 Gemini 與 OpenRouter 的專用 key；申請阿里雲 whitelist。

## 下一步

1. 評核框架 spec 已寫好（`.scratch/evaluation-framework/spec.md`，2026-11-06）→ `/to-tickets`。
2. 預期的第一批 ticket：1 篇文章 × 方案 A × 繁→英的完整路徑（翻譯、Deterministic check、Judge Finding、報告）；Judge 試跑（約 10 個 component，量度逐字片段的可信度與 Unjudged 率）；Qwen 交叉檢驗（先列 OpenRouter provider 清單給 Pan 確認）；合成淡化變體與 Gold set；CMS 15 篇的載入與 baseline。

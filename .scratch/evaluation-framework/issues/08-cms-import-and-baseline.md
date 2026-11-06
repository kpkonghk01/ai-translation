# 08: CMS 15 篇的載入與 baseline

**What to build:** CMS 匯出的 15 篇（曾被 Gemini block 的文章）轉成與網上文章相同結構的文章檔，可以放進實驗設定翻譯，也可以用現有的 baseline 工具產生方案 E 的輸出，再匯入成 Run。詳見 spec 第 13 節。

**Blocked by:** 05

**Status:** ready-for-agent

- [ ] `import-cms` 讀 CMS 匯出（取 history 中第一個 user turn 的 JSON），以 session id 為文章 id，寫成統一結構的文章檔，放在 `data/`，`meta.source` 為 `cms`
- [ ] 遇到不認識的 Component type 時失敗並列出
- [ ] 原文中每一段看得見的文字都必須出現在轉換結果中，否則失敗，規則與網上文章的 verify 一致
- [ ] 現有的 baseline 工具可以為這 15 篇產生方案 E 的輸出，標籤與其他 baseline 一致；之後經 `import-baseline` 匯入
- [ ] 本機有 CMS 匯出時，`tools/check.sh` 會加跑轉換覆蓋檢查
- [ ] 測試只用虛構的 CMS 格式資料

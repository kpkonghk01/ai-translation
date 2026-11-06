# 05: 繁→簡與方案 E

**What to build:** Pan 可以用 LLM Approach 跑繁→簡，也可以把方案 E（Current service）已重現的 baseline 匯入成 Run。兩者都經過同一套 `check`，包括以 OpenCC 1.4.2 判定的殘留繁體字，並一起出現在報告中，報告開頭註明 baseline 的版本。詳見 spec 第 4、5 和第 13 節。

**Blocked by:** 01, 03

**Status:** ready-for-agent

- [ ] 繁→簡 prompt 要求內地讀者自然的普通話書面語，粵語引語保留粗口強度，並注入 Term List；版本 id 和 hash 記入紀錄
- [ ] `import-baseline` 把 baseline 目錄匯入成方案 E 的 Run：結果為 `ok`，沒有 tokens 和 latency，baseline 標籤記入紀錄
- [ ] 沒有 `meta.baseline` 標籤的 baseline 會被拒絕
- [ ] 殘留繁體字：OpenCC 1.4.2 的 t2s 會改動的字，記為 Locale & Format minor，每個 Component 中同一個字只記一次；Term List 允許的例外不記
- [ ] 報告開頭列出每份 baseline 的標籤
- [ ] 測試用虛構文字，涵蓋殘留字、例外，以及沒有標籤時的拒絕

# 16: Ground truth 交叉檢驗

**What to build:** Pan 執行 `crosscheck`，讓 Qwen（經 OpenRouter，只用已確認的 provider）在沒看過 ground truth 的情況下，盲選每篇最敏感的句子並盲抽 Must-retain fact，然後產出與 ground truth 的分歧清單，由 Pan 決定要不要改 ground truth。中國負面新聞組同時用 Gemini 跑，以找出 Qwen 的盲點（Q20、Q31 比較 3）。詳見 spec 第 12 節。

**Blocked by:** 06, 10

**Status:** ready-for-agent

- [ ] 模型只收到文章原文；輸出最敏感的句子（必須逐字取自原文，否則丟棄並計數），以及每句的 Must-retain fact
- [ ] 分歧清單寫在 `data/`，包括：只有模型選了的句子、只有 ground truth 有的句子；兩邊都有的句子，把事實並排列出，數字和引語的差異由程式標出
- [ ] 中國負面新聞組同時用 Gemini 跑，另外列出「Gemini 抽到、Qwen 沒抽到」的事實，並標明只作參考
- [ ] 測試確認 `crosscheck` 不會寫入 ground truth 檔
- [ ] OpenRouter 請求沿用 06 的允許清單和資料保護欄位
- [ ] Pan 用真 key 執行並審閱分歧清單

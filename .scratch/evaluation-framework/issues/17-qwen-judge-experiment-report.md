# 17: Qwen Judge 實驗報告

**What to build:** 報告有一個獨立章節，回答「Qwen 當 Judge 時，是否比較容易漏看中國負面新聞被淡化的地方」（Q31）。這一節的結果不影響主結果和 Gate。詳見 consensus-summary 第 9 節，以及 spec 第 10 和第 11 節。

**Blocked by:** 14

**Status:** ready-for-agent

- [ ] 比較 1：從校準結果取 Gemini 與 Qwen 在 political 和 general 變體上的 recall，並列出兩者的差距
- [ ] 比較 2：同一批真實譯文由兩個 Judge 各判一次；Gemini 判為 Weakened 或 Absent、Qwen 判為 Present 的事實，列為「Qwen 疑似漏看」；按 domain 和譯文來源（Qwen 自己的譯文、其他模型的譯文）分別統計
- [ ] 分開列出 Qwen 的 Unjudged 率（包括輸入端被 Block）和無效 Finding 率，並與「評了但漏看」分開呈報
- [ ] 角色為 `experiment` 的 Judge 不影響 Gate 和分數；以測試確認
- [ ] 判斷門檻不寫死，只列數字

# 11: 人手審閱清單與決定檔

**What to build:** `review` 產生一份本機的 Markdown 審閱清單，列出所有必看項目，再附上以固定種子抽出的其餘項目。審閱者把決定寫進決定檔，報告重算時套用，原始的 Judge 輸出不會被改動。決定一旦過期，指令就會失敗。詳見 spec 第 9 節。

**Blocked by:** 10

**Status:** ready-for-agent

- [ ] 必看項目包括：所有 Gate 失敗、uncertain 的 verdict 和 Finding、無效的 Judge 輸出、Unjudged、疑似 Refusal，以及 Deterministic 警示
- [ ] 抽查：以固定種子從其餘項目中抽出，比例可以設定；同一輸入再跑，抽出的結果相同
- [ ] 每個項目有穩定的 key 和輸入的 hash，並顯示原文、譯文、Judge 的引用和理由
- [ ] 決定檔的 schema：接受、駁回、改判 verdict、改判 Severity，加上備註、審閱者和日期
- [ ] 報告套用決定；原始的 Judge 輸出不變；所有待審項目都有決定之後，Gate 的「未完成」標示自動消失
- [ ] 決定指向不存在的 key，或輸入的 hash 已經改變時，指令失敗並列出這些決定
- [ ] 清單和決定檔都寫在 `data/`

# 14: Gold set 格式與 Judge 校準

**What to build:** Pan 對每個候選 Judge 執行 `calibrate`，在 Gold set 上量度它的 recall、precision、誤報、無效率、Unjudged 率和一致率，並按 Direction 和 domain 分列，作為選定主 Judge 的依據。報告的 Gate 旁邊會列出主 Judge 的 recall。另有 `gold sample` 抽出約 30 條給 Pan 審閱。詳見 spec 第 11 節。

**Blocked by:** 10

**Status:** ready-for-agent

- [ ] Gold set 項目的 schema：id、Direction、domain、類別（`annotated`／`variant`／`clean`）、原文 Component 和 context、譯文、預期答案（預期的 Finding，或指向 ground truth 事實的預期 verdict 和淡化類型）
- [ ] 驗證：片段必須逐字；`variant` 指向的 Must-retain fact 必須存在；按類別、Direction、domain 列出數量
- [ ] `calibrate --judge <id>` 執行 MQM 任務和 fact 任務，各評兩次
- [ ] 量度的指標：預期 Finding 的 recall、驗證後 precision、`clean` 項目上的誤報、無效 Finding 率、Unjudged 率、兩次一致率、verdict 準確率、Weakened／Absent 的 recall、Severity 一致率
- [ ] 以上指標按 Direction 和 domain 分列；另外比較 uncertain 與一致兩組 Finding 的錯誤率
- [ ] 跑過多個 Judge 之後，產生分歧清單：Judge 對標註、Judge 對 Judge
- [ ] `gold sample` 以固定種子抽約 30 條，渲染成 Markdown
- [ ] 報告讀取最新的校準結果，把主 Judge 的 recall 列在 Gate 旁邊
- [ ] 合格線不寫死在程式裡
- [ ] 測試用很小的虛構 Gold set，確認各指標的數值正確

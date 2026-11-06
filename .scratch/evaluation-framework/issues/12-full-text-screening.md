# 12: 全文篩查

**What to build:** Judge 對每個 Run 的全文做一次篩查，每次跑兩遍，列出疑似淡化的地方，包括 ground truth 沒寫到的。結果併入審閱清單，不影響 Gate 和分數。詳見 spec 第 6 節。

**Blocked by:** 11

**Status:** ready-for-agent

- [ ] 篩查任務的輸入是原文全文和譯文全文；只輸出 Weakened、Absent 或 Amplification 的條目，每條附原文和譯文的逐字片段
- [ ] 每次篩查發兩個獨立請求；片段不合格的條目會被丟棄並計數；一致性判定和 Unjudged 的規則與 09 相同
- [ ] MQM 任務產生的 Fidelity-of-force Finding 併入同一份清單
- [ ] 篩查結果出現在審閱清單的必看項目中
- [ ] 測試確認篩查結果不改變 Gate 和分數

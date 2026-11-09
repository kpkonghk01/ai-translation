# 12: 全文篩查

**What to build:** Judge 對每個 Run 的全文做一次篩查，每次跑兩遍，列出疑似淡化的地方，包括 ground truth 沒寫到的。結果併入審閱清單，不影響 Gate 和分數。詳見 spec 第 6 節。

**Blocked by:** 11

**Status:** ready-for-agent

- [x] 篩查任務的輸入是原文全文和譯文全文；只輸出 Weakened、Absent 或 Amplification 的條目，每條附原文和譯文的逐字片段
- [x] 每次篩查發兩個獨立請求；片段不合格的條目會被丟棄並計數；一致性判定和 Unjudged 的規則與 09 相同
- [x] MQM 任務產生的 Fidelity-of-force Finding 併入同一份清單
- [x] 篩查結果出現在審閱清單的必看項目中
- [x] 測試確認篩查結果不改變 Gate 和分數

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真 Judge 跑過。規格沒寫、實作時做了選擇的地方：

- **Judge prompt 是我寫的**（`prompt_templates/judge-screen.txt`，英文）：要 Judge 先列出關鍵事實（誰對誰做了什麼、數字、法律狀態、歸因、直接引語）再逐條核對，只輸出有問題的；定義 Weakened／Absent／Amplification、類型 (a)–(f)、逐字片段、Term List 的慣用譯法不算淡化。請 Pan 在 ticket 14 之前看一遍。
- **輸入是逐 Component 的整篇**（每個 Component 一個 `source` 與 `translation`，譯文缺少的是 null），不是兩大段連在一起的文字：這樣原文片段有明確的歸屬（審閱清單才能顯示它在哪個 Component），片段也不會跨過 Component 的邊界。原文片段必須在**一個** Component 的原文內；譯文片段在譯文的任何 Component 都可以（譯文可能把句子搬到別處）。
- **條目的規則**：Weakened 與 Amplification 要有譯文片段，Absent 可以沒有；類型與 verdict 矛盾（Weakened 配 `amplification`、Amplification 配 a–f、Absent 配 `amplification`）該條無效。類型的寫法與 fact 任務相同（`e`、`(e)`、`E` 都可以）。
- **一致性**：verdict 相同而且譯文片段在譯文裡重疊（Absent 看原文片段）才是同一處。其餘規則與 MQM 相同。
- **預設會跑**：`judging.screening` 預設 `true`，`judge` 不寫 `--task` 時連篩查一起跑（每個 Run 每個 Judge 多 2 個請求）。想關掉就寫 `"screening": false`；`--task screen` 不受影響。
- **審閱清單**：篩查的條目和 MQM 的 Fidelity-of-force Finding（不論一致或 uncertain）全部必看；Unjudged 的篩查與無效的條目也列出。篩查的條目只能 `accept` 或 `reject`（表示看過了），不能改判；它們不影響 Gate 和分數（有測試）。
- **只審 primary 角色的 Judge**，與 ticket 11 一致。
- 報告還沒有列出篩查的 Unjudged 率（只在審閱清單裡）。


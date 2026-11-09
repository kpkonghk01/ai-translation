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

### 2026-11-09 review 之後的修正

- **已經寫好的審閱決定不能因為加了全文篩查就過期**：我把篩查的欄位插進每個項目的 hash，MQM 與 fact 項目的 hash 全變了。現在 MQM 與 fact 的 hash 格式不變，用上一版算出的黃金值釘住（有測試）。
- **篩查條目可能掛到錯的 Component**：標題常重覆內文的用語，原文片段在好幾個 Component 時，原本取第一個。現在優先取「譯文片段也在它的譯文裡」的那個。
- **Absent 的條目不留譯文片段**（與事實 verdict 的引用一致）：有寫的驗證過就不留，兩次一個寫一個沒寫才配得起來。
- **Fidelity-of-force Finding 的審閱決定**：它不計分，所以只能 `accept` 或 `reject`（表示看過了），不能改判 Severity；報告裡 Fidelity-of-force 的一致／uncertain 數量不隨決定改變（原本 accept 會讓 uncertain 變一致，和審閱清單對不上）。**這改了第 11 張寫的行為**（那時寫「Fidelity-of-force 也一樣處理，reject 不計數」）。
- 報告補上：各任務（Must-retain fact、全文篩查）的 prompt hash，和每組的全文篩查統計（評完幾個 Run、Unjudged 率、條目的一致／uncertain／無效數量）。
- 整理：三個任務共用請求的前三項（Direction、語域要求、Term List 條目）、verdict 與類型矛盾的判斷抽成一處、`run_call` 的任務分派集中成一個函式；過時的 docstring 和「誇大」（GLOSSARY 禁用詞）改正。
- 沒有照 review 改的：**驗證忽略所有空白**（與既有規則一致，ticket 的要求是與 ground truth 的驗證相同）；**配對用「第一次出現」**，重覆出現的片語可能把不同的地方配成一處，只影響「一致」的標示。**experiment 角色的 Judge 也會篩查**，但沒有地方讀它的結果（ticket 17 會用）。


# 10: Must-retain fact 核對與 Gate

**What to build:** Judge 逐條核對每個 Sensitive sentence 的 Must-retain fact，每條評兩次。報告的 Gate 一節列出淡化率和 95% 信心上限，按 domain、淡化類型和事實種類分列，Amplification 另列。證據不齊時，Gate 標示為「未完成」。詳見 spec 第 6 和第 8 節。

**Blocked by:** 09

**Status:** ready-for-agent

- [ ] 讀取現有格式的 ground truth，驗證規則與現有的渲染工具相同：文章存在、Component id 存在、anchor 逐字、kind 和 domain 在允許清單內
- [ ] fact key 由文章 id、Component id 和事實文字的 hash 組成；事實文字改了，舊的 verdict 就失效
- [ ] fact 任務：每條事實輸出 verdict（Present／Weakened／Absent）、淡化類型或 Amplification、譯文引用（Present 和 Weakened 必須有）、理由；複合事實只要任一要點被淡化就判 Weakened
- [ ] 引用不是逐字子字串時，該條 verdict 作廢，事實列入審閱；一致性判定和 Unjudged 的規則與 09 相同
- [ ] 缺少的 Component 如含 Sensitive sentence，其所有事實直接判 Absent，不送給 Judge
- [ ] 淡化率 = 在任一 Run 失敗過的不重覆句子數 ÷ 有評核結果的不重覆句子數；另列每個 Run 的淡化率和涉及的文章數
- [ ] `block` 和 `refusal` 的 Run 不計入分母
- [ ] 95% 上限用單尾 Clopper–Pearson；測試確認 0／150 約為 2.0%
- [ ] 按 domain 分組（general、china-political，以及實驗設定中的覆寫，供 Q34 使用）；列出每個 Approach 的 political 與 general 差距；按淡化類型 (a)–(f) 和事實 kind 分列；Amplification 另計
- [ ] 實驗設定為每個 domain 分組指定主 Judge；general 組還沒指定時，標示「主 Judge 暫定」
- [ ] 仍有 uncertain、無效或 Unjudged 的事實時，Gate 標為「未完成」並列出待審數量
- [ ] 沒有校準結果時，Gate 旁寫明「Judge recall 未量度」

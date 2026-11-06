# 09: Judge MQM 與計分

**What to build:** Pan 在實驗設定中指定 Judge 後執行 `judge`，每個要翻譯的 Component 都由 Judge 評兩次，產生 Finding。所有片段經逐字驗證，不合格的丟棄並計數。程式按固定權重計分，Main 和 Peripheral 分開，報告列出分數、Unjudged 率、無效 Finding 率和 Source defect 清單。詳見 spec 第 6 和第 7 節。

**Blocked by:** 03

**Status:** ready-for-agent

- [ ] Judge 設定：provider、模型、模型家族、prompt 版本、角色（`primary`／`experiment`），temperature 固定為 0
- [ ] 同家族規則：Judge 與被評的 Approach 同家族時，指令拒絕執行，除非實驗設定寫明例外和理由；例外列在報告開頭
- [ ] MQM 任務涵蓋每個要翻譯的 Component，包括 Main 和 Peripheral；title 和 tags 使用保留的 Component id；gallery 的說明和 related-article 的標題以換行連接成一段
- [ ] 每個請求帶前後各一個要翻譯的 Component 作 context，以及 Direction 的要求、相關的 Term List 條目，和 Dimension、Severity、Softening (a)–(f)、Amplification、法律狀態與歸因、Source defect 的定義
- [ ] 每個任務是兩個獨立請求
- [ ] 逐字驗證：連續空白壓縮後，片段必須是原文或譯文的子字串；片段不合格、enum 不合法或結構不符的輸出記為無效，保留供審閱，並計入無效 Finding 率
- [ ] 一致性判定：同一 Dimension 加片段重疊視為同一個 Finding；Severity 不同時取較高者並加標記；配對不到的標為 uncertain；只有一次成功時全部標為 uncertain；兩次都失敗則記為 Unjudged，並附原因
- [ ] Fidelity-of-force 的 Finding 不計分
- [ ] Source defect 以「文章、Component、原文片段」去重，並記錄是哪些 Judge 發現的；子類「疑似修正原文錯誤」記為 Accuracy
- [ ] 計分：權重可以設定（預設 1／5／25）；Main 和 Peripheral 分開；列出總罰分和每千字罰分，並按 Dimension、Severity 分列；同時列出「只計一致的 Finding」和「連 uncertain 一起計」兩個數字
- [ ] 實驗設定可以限定 MQM 只評部分 Run
- [ ] `--dry-run` 列出 Judge 的請求數
- [ ] 報告列出各 Judge 的 Unjudged 率、無效 Finding 率、分數和 Source defect 清單

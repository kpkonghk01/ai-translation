# 14: Gold set 格式與 Judge 校準

**What to build:** Pan 對每個候選 Judge 執行 `calibrate`，在 Gold set 上量度它的 recall、precision、誤報、無效率、Unjudged 率和一致率，並按 Direction 和 domain 分列，作為選定主 Judge 的依據。報告的 Gate 旁邊會列出主 Judge 的 recall。另有 `gold sample` 抽出約 30 條給 Pan 審閱。詳見 spec 第 11 節。

**Blocked by:** 10

**Status:** ready-for-agent

- [x] Gold set 項目的 schema：id、Direction、domain、類別（`annotated`／`variant`／`clean`）、原文 Component 和 context、譯文、預期答案（預期的 Finding，或指向 ground truth 事實的預期 verdict 和淡化類型）
- [x] 驗證：片段必須逐字；`variant` 指向的 Must-retain fact 必須存在；按類別、Direction、domain 列出數量
- [x] `calibrate --judge <id>` 執行 MQM 任務和 fact 任務，各評兩次
- [x] 量度的指標：預期 Finding 的 recall、驗證後 precision、`clean` 項目上的誤報、無效 Finding 率、Unjudged 率、兩次一致率、verdict 準確率、Weakened／Absent 的 recall、Severity 一致率
- [x] 以上指標按 Direction 和 domain 分列；另外比較 uncertain 與一致兩組 Finding 的錯誤率
- [x] 跑過多個 Judge 之後，產生分歧清單：Judge 對標註、Judge 對 Judge
- [x] `gold sample` 以固定種子抽約 30 條，渲染成 Markdown
- [x] 報告讀取最新的校準結果，把主 Judge 的 recall 列在 Gate 旁邊
- [x] 合格線不寫死在程式裡
- [x] 測試用很小的虛構 Gold set，確認各指標的數值正確

## Comments

### 2026-11-09 實作完成

全部勾選項完成，沒有用真 Judge 跑過，也還沒有真的 Gold set（第 15 張）。規格沒寫、實作時做了選擇的地方：

- **指令**：`calibrate --judge <id>`（要 key，Pan 在自己的終端機跑）；不寫 `--judge` 就只重算結果、不送請求。`gold check` 驗證並列數量，`gold sample --count 30 --seed 1` 抽樣。Gold set 的位置寫在實驗設定的 `calibration.gold_set`。
- **Gold set 項目**：`variant` 與帶事實的項目用 `ground_truth`（文章 id＋Component id）指向 ground truth，`facts` 列預期的 verdict；請求裡的事實是那個 Component 的 Sensitive sentence 的全部事實（與正式評核一樣），沒列在 `facts` 的事實不計分。`variant` 與 `clean` 項目的規則寫在 schema（clean 沒有預期的 Finding，事實都是 Present；variant 至少一條預期 Weakened 或 Absent；annotated 要有預期的 Finding 或事實）。
- **指標的分母**（定義寫在 `calibration.py` 開頭）：recall 的分母是「評完」的項目的預期 Finding；**precision 只看 annotated 項目**（只有它的標註是完整的；variant 的 Judge Finding 沒有人逐條標過，不計）；clean 的誤報是「有 Finding 的 clean 項目數」；「一致與 uncertain 的錯誤率」看 annotated 與 clean 項目；事實的準確率只算有最終 verdict 的，uncertain 與無效另計；**Gate 旁邊的 recall 是「失敗的事實（Weakened 或 Absent）」的 recall，uncertain 算沒有抓到**（保守：uncertain 雖然會進審閱，但 recall 要看 Judge 自己能不能抓到）。
- **配對**用與 Judge 兩次結果一致性相同的規則（同一 Dimension、譯文片段重疊；譯文片段是空的就看原文片段），預期 Finding 的 Severity 另計一致率（對到的是一致的 Finding 優先）。
- **過期**：prompt、Judge 設定、項目內容（含它指向的事實）任何一項改了，舊的請求不再用，並計數。所以改了 prompt，recall 就回到「未量度」，直到重新校準：舊 prompt 量到的 recall 說不了新 prompt。
- **Gate 旁邊的 recall 按該 Gate 分組的 Direction 與 domain 取**；Q34 這類覆寫分組不分 domain；Gold set 在這一格沒有預期失敗的事實時寫「未量度」，不用別格的數字頂替。
- **校準不涉及被評的 Approach**，所以沒有同家族的限制；每個 Judge（primary 或 experiment）都可以校準。
- **合格線沒有寫進程式**，`calibration.md` 也不判合格與否（有測試）。
- **分歧清單**含原文與譯文，只寫在 `data/`；`calibration.md` 與報告裡的校準摘要只有數字。
- 還沒有的：真實的 Gold set（第 15 張）、Q31 比較 2 與 3（第 17 張）。Judge prompt 要在校準之前先由 Pan 看過（三份：`judge-mqm.txt`、`judge-facts.txt`、`judge-screen.txt`）。

### 2026-11-09 review 之後的修正

- **Judge 被擋（Unjudged）的項目不能離開 recall 的分母**（最重要的一項）：原本 Unjudged 的項目整個不算，一個在 8／10 個政治變體上被擋、其餘 2 個抓到的 Judge 會顯示「recall 2/2」。現在預期的 Finding 與預期失敗的事實都留在分母，算沒有抓到；Unjudged 率用「分子／分母」另列；還沒有兩個請求的才是「尚未評完」，不在分母，另外列。（我自己的測試原本把這個行為鎖住了，已改。）
- **「未量度」的原因不能寫錯**：Gold set 在這一格沒有預期失敗的事實、這一格的項目還沒有請求結果（尚未跑或已過期）、校準全部過期、還沒有校準，現在是四種不同的說法。
- **`domain` 與原文要核對**：variant 與帶事實的項目，`domain` 必須與 ground truth 那篇文章的 domain 相同（標錯就放進錯的 Gate 格），項目的原文必須與那個 Component 的原文相同（原本只要含一個 anchor）。
- **標註或備註改了不再重送請求**：項目 hash 原本涵蓋預期答案與備註，改一個 Severity 就要重送（要錢）。現在只涵蓋送給 Judge 的內容（Direction、原文、譯文、前後文、它指向的句子與事實）；指標由程式用請求紀錄重算。
- **報告補上**：Unjudged 率與預期 Present 被判成失敗的比率都附分子分母；`calibration.md` 加「Direction × domain」（Gate 旁邊的 recall 就是這一格）和「variant × domain」（Q31 比較 1）；分歧清單加 Severity 不同的 Finding 與類型不同的事實；`calibrate --dry-run` 印估計的輸入字數。
- **結構**：`GoldCall` 與 `JudgeCall` 共用一個基底（`JudgeRecord`），不再重抄欄位；verdict 與類型矛盾的規則、`FAILING_VERDICTS` 只定義一次（schemas）；正式評核與校準共用送請求的函式與 provider 違規的停止；`calibration.py`（計算）與 `calibration_report.py`（寫成報告）分開。
- **補的測試**：provider 違規停止、key 不會寫進任何校準輸出、prompt 改了只讓該任務的請求過期、ground truth 的 anchor 改了讓指向它的項目過期、失敗時不寫任何檔案、domain 與原文的核對。
- 沒有照 review 改的：**`Metrics` 的 35 個計數沒有拆成 MQM 與 fact 兩組**（只是整理，不影響行為）；**precision 不含 clean 項目**（clean 的 Finding 一律是誤報，在「clean 項目上的誤報」，定義寫在 `calibration.py` 開頭）；**配對不是一對一**（一個 Judge Finding 可以對上好幾個預期 Finding，實務上很少見）；**報告在 Gold set 不合時停止**，不是寫「未量度」（CODING_STANDARDS：不靜默丟掉東西；Gold set 還沒有時，實驗設定先不要寫 `calibration`）。Term List 或語域要求改了不會讓校準過期（正式的 `judge` 也一樣）。


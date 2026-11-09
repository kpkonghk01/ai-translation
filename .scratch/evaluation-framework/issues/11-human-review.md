# 11: 人手審閱清單與決定檔

**What to build:** `review` 產生一份本機的 Markdown 審閱清單，列出所有必看項目，再附上以固定種子抽出的其餘項目。審閱者把決定寫進決定檔，報告重算時套用，原始的 Judge 輸出不會被改動。決定一旦過期，指令就會失敗。詳見 spec 第 9 節。

**Blocked by:** 10

**Status:** ready-for-agent

- [x] 必看項目包括：所有 Gate 失敗、uncertain 的 verdict 和 Finding、無效的 Judge 輸出、Unjudged、疑似 Refusal，以及 Deterministic 警示
- [x] 抽查：以固定種子從其餘項目中抽出，比例可以設定；同一輸入再跑，抽出的結果相同
- [x] 每個項目有穩定的 key 和輸入的 hash，並顯示原文、譯文、Judge 的引用和理由
- [x] 決定檔的 schema：接受、駁回、改判 verdict、改判 Severity，加上備註、審閱者和日期
- [x] 報告套用決定；原始的 Judge 輸出不變；所有待審項目都有決定之後，Gate 的「未完成」標示自動消失
- [x] 決定指向不存在的 key，或輸入的 hash 已經改變時，指令失敗並列出這些決定
- [x] 清單和決定檔都寫在 `data/`

## Comments

### 2026-11-09 實作完成

全部勾選項完成。已用 Pan 的 smoke-test 資料跑過 `review`（沒有 Judge，只有一個警示）。規格沒寫、實作時做了選擇的地方：

- **審閱項目有六種**：事實的 verdict、Judge 的 MQM Finding、Deterministic Finding、無效的 Judge 輸出、MQM 沒評成的 Component、Deterministic 警示。必看照 ticket 的清單；**Deterministic Finding（漏段、Terminology 等）不是必看，是抽查的候選**（它們是程式判的，ticket 的必看清單只寫「警示」），但審閱者可以對它寫 `reject`（spec 第 7 節：Deterministic Finding 除非被駁回，否則一律計入）。
- **只審 primary 角色的 Judge**（事實是該分組的主 Judge）。experiment 角色的 Judge（Qwen）不在清單裡，也不能對它的項目寫決定；ticket 17 需要的話再加。
- **事實的決定**：`accept` 只能用於已有最終 verdict 的（Gate 失敗或通過的）；uncertain、無效、Unjudged 的事實沒有單一的 verdict 可以接受，只能 `override` 並寫 verdict；事實不能 `reject`（要說它是對的就是 override 成 Present）。這些規則在決定檔載入後驗證，不合的整個指令失敗。
- **抽查用 hash 決定**，不是 `random.sample`：「種子加項目 key」的 hash 小於比例就抽。好處是之後有更多 Judge 結果進來，已經抽到的不會變。代價是抽出的數量只是約等於比例（項目少時會有出入）。預設 10%、種子 1。
- **key 與 hash**：key 是種類加上「Run、Component、Judge、能指認這一項的內容」的 hash（事實是事實文字的 hash；Finding 是 Dimension 加兩個片段）；輸入 hash 涵蓋原文、譯文和 Judge 兩次的原始輸出，所以重新判過（即使結果看起來一樣）、原文或譯文改了，決定就過期。16 個十六進位字元，方便抄。
- **決定檔要自己建立**（JSON，key 與 hash 從清單抄）。如果 Pan 覺得手寫 JSON 太麻煩，可以改成清單裡每個項目附一段可以直接貼的 JSON，或者做個簡單的本機頁面；先不做。
- **套用的地方**：Gate 的事實 verdict、MQM 分數（accept／override 算作一致，reject 不計分也不計數）、Deterministic Finding 的計數與罰分。Fidelity-of-force 的 Finding 也一樣處理（reject 不計數）。審閱的完成度只算必看項目。
- **報告需要讀文章檔**的條件多了一個：有決定檔時（要重算項目的 hash）。沒有 Judge、沒有 ground truth、沒有決定檔時還是不讀。
- 還沒有的：全文篩查結果（ticket 12 產生之後才有項目）、Source defect 的審閱（現在只在報告裡列出）。


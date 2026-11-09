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

### 2026-11-09 review 之後的修正

- **審閱完成度一定出現在報告開頭**（spec 第 10 節）：原本沒有 Judge、ground truth、決定檔時不讀文章，報告沒有「審閱」一節。現在 `report` 一律讀有 ok 的 Run 的文章（`translate` 本來就要這些文章檔）。
- **過期或不存在的決定在 `report` 不能被略過**：原本所有 Run 都不是 ok（例如重翻後被 Block）時，決定沒有被檢查，`report` 成功而 `review` 失敗。
- **Deterministic Finding 的 `override severity` 原本只通過驗證、從來沒套用**。現在會套用（改了罰分的權重）。
- **兩個項目共用同一個 key**：同一個 Component 內身分一樣的項目（兩處一樣的殘留漢字；Judge 的兩個一樣的 Finding，一個一致一個 uncertain）原本 key 相同，駁回一個會連另一個一起駁回。現在 key 帶序號。
- **警示項目的原文與譯文顯示錯誤，決定也不會過期**：「不翻譯的 Component 被改動」和多出的 Component 沒有文字可顯示，原本顯示「（沒有）」，改了內容 hash 也不變。現在顯示整個 Component（有長度上限），hash 涵蓋它。
- **漏段與 Gate 不能互相矛盾**：駁回漏段 Finding 會讓報告的漏段數變 0，但 Gate 還是把那個 Component 的事實判 Absent。現在程式證明的結果（缺 Component 的事實、漏段／多出／重覆／type 被改的 Finding）只能 `accept`。
- **無效的 fact 輸出**（回了不認識的 fact_id、或不是物件）原本不在清單裡。現在列在「無效的 Judge 輸出」。
- **`mqm_run_limit` 刻意沒評的 Run** 不再有 MQM 項目（原本之前評過的 Run 會有項目，但不計分，決定沒有作用）。
- 其他：把 `require_checked` 移進 `check.py`，`review` 與 `report` 不再互相 import；清掉未用的 import；GLOSSARY 加「審閱清單」。
- （2026-11-09，第 12 張 review 後更正）Fidelity-of-force Finding 的決定現在只能 accept 或 reject，且不改變報告裡 Fidelity-of-force 的數量；上面「Fidelity-of-force 也一樣處理」不再成立。

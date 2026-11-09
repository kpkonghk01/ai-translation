# 03: Term List 與其餘 Deterministic check

**What to build:** Pan 提供一份 Term List 後，翻譯 prompt 會注入文章中出現的條目；`check` 會記錄 Terminology Finding、繁→英殘留中文字，以及一組只供人手參考、不扣分的警示。報告列出這些結果的數量。詳見 spec 第 5 和第 14 節。

**Blocked by:** 01

**Status:** resolved

- [x] Term List 的 schema：原文詞、各 Direction 的允許譯法（可以多於一個）、類別、未命中時的 Severity（預設 minor）、繁→簡殘留字檢查的例外、備註
- [x] Term List 的格式驗證和重覆條目檢查；Term List 的 hash 記入 Run 紀錄
- [x] 翻譯 prompt 只注入原文出現過的條目
- [x] 原文出現 Term List 的詞，譯文卻找不到任何允許的譯法（英文不分大小寫）→ Terminology Finding，Severity 取自條目，原文片段是那個詞，譯文片段留空
- [x] 繁→英譯文殘留漢字 → Locale & Format minor，每個 Component 中每段連續漢字記一次
- [x] 以下情況只產生警示，不計分：長度比例超出範圍（範圍按 Direction 設定）、原文的數字在譯文找不到（去掉千位分隔號；繁→英處理 萬、億 的換算）、直接引語數量不同、疑似 Refusal（符合拒絕語樣式而且長度遠短於原文）、要翻譯的 Component 譯文與原文完全相同、不翻譯的 Component 被改動
- [x] 報告列出 Deterministic Finding 按 Dimension 的數量，以及各類警示的數量
- [x] 每條規則至少有一個測試，只用虛構文字

## Comments

### 2026-11-09 實作完成

全部勾選項完成。Term List 的內容是 ticket 04 的工作；這張只做格式、驗證和使用它的程式。規格沒寫、實作時做了選擇的地方（容易改，請 Pan 看一眼）：

- **翻譯 prompt 升到 `translate-trad-to-en.v2`，與 v1 有兩個差別**：(1) 加了只在文章含有 Term List 條目時才出現的一節；(2) **拿掉了 v1 的 Fidelity 一節**（不可淡化、保留法律狀態與歸因）。v1 檔案保留，才能重現 01 的煙霧測試。(2) 的由來：我在 01 的留言誤以為 v1 沒有這一節，所以讓 v2 照「只寫 spec 第 4 節列出的要求」寫。這個選擇影響 Softening 的量度（有這一節，模型比較不容易淡化），**請 Pan 在知道這一點的情況下決定**：維持 v2 現狀，或把 Fidelity 加回去（= 新的版本 id）。
- **Terminology 比對**：子字串、不分大小寫、連續空白視為一個；每個 Component 的每個條目最多記一個 Finding（同一個詞出現兩次也只記一個）。比對只看對齊的那個 Component，別處出現指定譯法不能抵銷。選子字串而不是整字，因為每個 Finding 都扣分，寧可漏報也不誤報（代價：`indecent assault` 會被 `indecent assaults` 滿足）。
- **殘留漢字**：只算漢字，全形標點不算；每一段連續漢字一個 Finding，同一段出現兩次記兩個。
- **長度比例的預設範圍 1.5–8.0（暫定）**：用 01 煙霧測試那一篇真實譯文實測，各 Component 的比例是 2.81–5.16（中位數 3.80），所以預設留得很寬。只有一篇的資料，等跑出更多譯文再收窄；可用實驗設定的 `length_ratio` 覆寫。
- **疑似 Refusal**：譯文符合拒絕語樣式，而且長度比低於該 Direction 長度比例範圍的下限（「遠短於原文」以正常範圍為準，不另訂一個數字）。
- **「譯文與原文相同」只在原文含漢字時才警示**：只有英文或數字的原文（例如 COVID-19）本來就不用翻譯。
- **數字警示**：除了 spec 寫的千位分隔號和 萬／億，也接受英文寫法的小數字（`three`、`twenty-five`、`two hundred and fifty`）、序數（`第3名` → `third`）、月份名（`5月` → `May`）和 `thousand`／`million`／`billion`；原文的數字 `1` 不警示（英文常寫成 a／an，沒辦法用字串判斷）。這些是用煙霧測試那篇真實譯文校準的：未處理時 18 個 Component 有 9 個有警示，處理後剩 1 個。
- **直接引語**：中英文引號用同一套規則計數，巢狀的不另計；英文譯文常用單引號（實測那篇全部用單引號），所以單引號也算，但 `it's`、`workers'` 的撇號不算。
- **quote 的 `source`（出處人名）**與引語一起視為該 Component 的文字，所以也要翻譯、也會被檢查。
- **不認識的 Component type、型別不對的文字欄位**（例如 paragraph 的 `text` 是數字）在載入文章時就令指令失敗；譯文裡文字欄位型別不對的，Run 記為 `malformed`。不會被內容規則靜默略過（本機現有文章都沒有這些問題）。已知的 type 清單有測試與 `tools/hk01_web.py` 對照。
- **check 結果會過期**：`check` 另寫 `check-meta.json`，記下規則版本、Term List hash 和長度比例範圍；`report` 發現它們跟現在的設定不同就停止，要求重跑 `check`。規則版本由實作規則的原始碼的 hash 決定，不靠人記得改版本號（代價：不影響規則的改動也會令舊結果過期，重跑 `check` 只要幾秒）。01 煙霧測試那份舊的 check 結果因此會被拒絕，重跑 `check` 即可（不需要重翻譯）。全部 Run 都沒有譯文時不需要 `check`。
- Term List 的 `category` 用英文 slug（`person`、`place`、`organization`、`legal`、`news-idiom`）。

### 2026-11-09 review 之後的修正

兩軸 review 找到的問題，已修正並各有測試：

- **`report` 的退步**：全部 Run 都被 Block（沒有譯文可檢查）時，`report` 一度因為沒有 check 紀錄而拒絕；現在這種情況不需要 `check`。
- **Term List 與 Run 要一致**：`check` 原本會拿翻譯之後才改過的 Term List 去判，等於拿模型沒看過的條目扣分。現在 Run 記的 hash（模型看到的原文詞與譯法）與現在的不同就拒絕執行。嚴重度、類別、備註不在 prompt 裡，所以只改這些不必重新翻譯：嚴重度與類別會令舊的 check 結果過期（重跑 `check`），備註完全不影響。
- **Direction**：`trad-to-simplified` 加進 Direction，Term List 可以先寫繁→簡的條目（ticket 04 需要）；實驗設定仍然只接受已有 prompt 的 Direction。
- **數字警示**：`1億2000萬`、`3萬5000` 合成一個數；`千`、`百` 單位（`3千萬`、`5百萬`）；`three-year`、`twenty five` 這類連字號和分開的寫法。
- **漢字**：`〇`（`二〇二四年`）和擴展 G 以後的字不再把一段拆成兩段。
- **Terminology**：逐行比對，不讓兩個 tag 或兩則圖說的頭尾接起來湊成一個詞；Finding 的原文片段是原文裡實際的寫法（含多餘的空白），是逐字子字串。
- 其餘是整理：沒有 Term List 時用「空的 Term List」，不再到處判斷 None；去掉三份各自實作的「去除空白」。

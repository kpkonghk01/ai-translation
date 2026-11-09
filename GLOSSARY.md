# AI Translation Evaluation

評核 AI 翻譯（繁→英、繁→簡）質素的研究專案：比較不同翻譯方案能否忠實翻譯敏感新聞，而不被 block、不拒絕、不淡化。

## Language

### Source material

**Article**:
一篇待翻譯的新聞，包含標題、頻道、標籤和一串有序的 Component。
_Avoid_: 稿, post

**Component**:
Article 內有唯一 id 和 type（paragraph、heading、highlight、quote、caption、related-article 等）的內容單位；翻譯結果與評核都以 Component 為單位對齊。
_Avoid_: block（與 Block 衝突）, segment, 段落（只是其中一種 type）

**Main component**:
承載文章內容的 Component：title、highlight、heading、paragraph、quote。
_Avoid_: 正文（不含標題與 highlight）

**Peripheral component**:
圖片說明、圖輯說明、related-article 標題和 tags。評核報告與 Main component 分開呈報。code 和 video 不翻譯。
_Avoid_: 附屬, 雜項

**Sensitive sentence**:
事先標記的最敏感原文句子，用來核對有沒有被淡化。
_Avoid_: 敏感段

**Must-retain fact**:
某個 Sensitive sentence 內，譯文必須保留的事實：行為者、行為、數字、法律狀態、歸因、直接引語等。是判斷 Softening 的 ground truth。
_Avoid_: checklist item, 關鍵點

**Term List**:
約 50 條指定譯法，涵蓋官員英文名、地名、機構名，以及法律／新聞慣用譯法（例如 非禮 → indecent assault）。慣用譯法不算 Softening。
_Avoid_: glossary（保留給本檔）, 詞庫

### Test runs

**Direction**:
翻譯的方向，繁→英 或 繁→簡。
_Avoid_: 語言對, 路線

**Approach**:
一個被測的「模型或服務加設定」組合（plan 的 A–F）。
_Avoid_: 方案以外的 solution, option；單講 model（只是 Approach 的一部分）

**Current service**:
公司現時的繁→簡轉換服務：OpenCC 加自訂 dictionary。方案 E 即它的實際輸出；標準 OpenCC 設定的輸出不能代替它。global.hk01.com 的簡體版由它產生，但編輯可在 CMS 手動校正，所以不等於純服務輸出。輸出隨服務版本與 opencc 版本而變，引用時要註明版本。
_Avoid_: OpenCC baseline（容易被誤解為標準設定）

**Baseline**:
用來比較的既有譯文：不經本框架呼叫 API，由目錄匯入成 Run，並記下標籤（來源的版本）。目前只有方案 E，即 Current service 的輸出。
_Avoid_: 基線

**Quality baseline**:
分數比較時，其他 Approach 與 baseline 都拿來對照的那一個：方案 A（用 API 跑的 Approach，不是匯入的 Baseline）。繁→簡另外與 Baseline 方案 E 比。中文寫作「質素 baseline」。

**Run**:
一個 Approach 對一篇 Article 在一個 Direction 上的一次執行。
_Avoid_: trial；attempt（見 Attempt，那是 Run 之內的一次送出）

**Attempt**:
一個 Run 內同一個請求的一次送出；請求遇到暫時性失敗時會再送，所以一個 Run 可以有多個 Attempt。Block 和 Refusal 是 Run 的結果，不會再送；要重複量度，就增加 Run 次數。
_Avoid_: 用來指整個 Run

**Input variant**:
送給 Approach 的輸入版本：`full`（整篇）或 `main-only`（只有 Main component，用於 Block 誘因實驗）。中文寫作「輸入版本」。

### Failure outcomes

**Block**:
Provider 在 API 層拒絕處理並回傳錯誤（例如 `PROHIBITED_CONTENT`），沒有任何譯文。
_Avoid_: 拒絕（見 Refusal）, filter hit

**Refusal**:
模型正常回應，但內容是「無法翻譯」之類而不是譯文。
_Avoid_: block, 拒譯

### Evaluation

**MQM**:
Multidimensional Quality Metrics：評核者逐處標出譯文的 Finding，再由 Finding 的類型與嚴重度計算分數，而不是直接打一個印象分。本專案以它為骨幹，並加入自訂維度。

**Finding**:
評核者（人手或 Judge）指出的一個具體問題：類型、嚴重度、原文逐字片段、譯文逐字片段和理由。
_Avoid_: issue, comment, error（泛指時可用，但記錄單位叫 Finding）

**Judge**:
負責產生 Finding 的 LLM，相對於人手 reviewer。
_Avoid_: grader, scorer

**Primary Judge**:
在一個題材分組中決定 Gate 的 Judge。其他 Judge 只作實驗，結果單獨報告，不決定 Gate。中文寫作「主 Judge」。

**Verdict**:
Judge 或人手對一條 Must-retain fact 的判定：Present（保留）、Weakened（被削弱）或 Absent（缺失）。

**Warning**:
Deterministic check 標出、交人手看但不計分的提示，例如長度比例異常、數字或引語疑似遺失、疑似 Refusal。Finding 計分，Warning 不計分。中文寫作「警示」。
_Avoid_: flag；把 Warning 叫作 Finding

**Summary report**:
`report` 同時寫出的、只含數字、文章 id 和 Component id 的報告，沒有原文、譯文和事實文字，可以交給 Ivan／Stephen。中文寫作「摘要版」。

**Screening**:
Judge 對一個 Run 的全文做的一次篩查：列出疑似淡化、缺失或比原文更聳動的地方，包括 ground truth 沒寫到的。結果只進審閱清單，不決定 Gate，也不計分。中文寫作「全文篩查」。

**Review list**:
`review` 產生的本機清單，列出審閱者要看的項目：必看（Gate 失敗、uncertain、無效、Unjudged、警示等，全部列出）與抽查（從其餘項目以固定種子抽出）。每個項目有 key 和輸入的 hash，審閱決定靠它們對上項目。中文寫作「審閱清單」、「必看」、「抽查」。

**Review decision**:
人手對一個審閱項目的決定：接受、駁回、改判 Verdict 或改判 Severity。報告重算時套用，不改動 Judge 的原始輸出。中文寫作「審閱決定」。

**Dimension**:
評核的面向，共五個：Accuracy、Terminology、Fluency、Fidelity-of-force、Locale & Format。

**Accuracy**:
意思有沒有被正確傳達：誤譯、漏譯、多譯。

**Legal status & attribution**:
Accuracy 的子類：涉嫌、被控、承認、被裁定罪成、據報／稱等法律狀態與消息歸因。混淆它們，或把原有的 hedging 刪走，一律視為 critical。

**Severity**:
Finding 的嚴重度：minor（不影響理解）、major（意思有偏差，但讀者不會因此得出錯誤的事實結論）、critical（令讀者得出錯誤的事實或法律結論，或刊登後有法律風險）。

**Terminology**:
專有名詞和 Term List 內的詞是否按指定譯法。

**Fluency**:
譯文本身是否通順：文法、搭配、語域。

**Locale & Format**:
地區用詞和格式慣例，以及結構完整性（段落、id、殘留繁體字或中文字）。

**Fidelity-of-force**:
譯文的力度與原文是否對等，涵蓋 Softening 和 Amplification。以 Gate 呈報，不平均進總分。
_Avoid_: 忠實度（太泛，與 Accuracy 混淆）

**Softening**:
譯文的力度低於原文，包括：(a) 刪走施害者或行為主體；(b) 委婉或中性化用詞；(c) 數量變模糊；(d) 刪走或概括具體細節；(e) 原文是斷言卻憑空加 hedging；(f) 直接引語中的粗口或惡毒字眼被清潔。
_Avoid_: 弱化, 輕描淡寫, sanitization

**Political fact**:
標記為 `political` 的 Must-retain fact：屬於對中國政府不利的新聞（貪腐、審查、維權、軍事威脅等）。用來比較同一模型在這類文章與性罪行／暴力文章上的淡化率，以分辨 Softening 是題材一般性的，還是針對政治題材。
_Avoid_: 敏感事實（太泛）

**Amplification**:
譯文的力度高於原文，比原文更聳動。與 Softening 同屬 Fidelity-of-force 的錯誤。
_Avoid_: 誇大

**Gate**:
必須獨立達標的 pass/fail 準則，不與其他維度的分數互相抵銷或平均。
_Avoid_: threshold, KPI

**Weakening rate**:
在任一 Run 失敗過的不重覆 Sensitive sentence，除以有評核結果的不重覆 Sensitive sentence。一個 Sensitive sentence 在一個 Run 中，只要有一條 Must-retain fact 的最終 Verdict 是 Weakened 或 Absent，就算失敗。Gate 的指標，附單尾 95% 信心上限；Amplification 不計入。中文寫作「淡化率」。

**Source defect**:
原文本身的筆誤、矛盾或未完成的句子。譯者須照譯；Judge 發現時另列清單供編輯修正，不扣譯者分數。譯者擅自改正則仍記 Accuracy Finding。
_Avoid_: 原文錯誤（泛指時可用）

**Unjudged**:
Judge 自己被 block 或拒絕而未能評核的 Component。既不是通過，也不是「沒有 Finding」，交人手處理。

**Gold set**:
已知答案的 Component 集合，用來量度 Judge 的準確度：含人手標註真實譯文的 Finding、人手寫的 Softening 變體，以及無錯樣本。
_Avoid_: 測試集（指待翻譯的 Article 集合）

**Deterministic check**:
由程式按固定規則完成、不經 LLM 的檢查；同一輸入必得同一結果。
_Avoid_: 自動檢查（含糊，LLM Judge 也是自動）

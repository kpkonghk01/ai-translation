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

**Run**:
一個 Approach 對一篇 Article 在一個 Direction 上的一次執行。
_Avoid_: trial, attempt

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

**Amplification**:
譯文的力度高於原文，比原文更聳動。與 Softening 同屬 Fidelity-of-force 的錯誤。
_Avoid_: 誇大

**Gate**:
必須獨立達標的 pass/fail 準則，不與其他維度的分數互相抵銷或平均。
_Avoid_: threshold, KPI

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

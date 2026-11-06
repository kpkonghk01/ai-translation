# 03: Term List 與其餘 Deterministic check

**What to build:** Pan 提供一份 Term List 後，翻譯 prompt 會注入文章中出現的條目；`check` 會記錄 Terminology Finding、繁→英殘留中文字，以及一組只供人手參考、不扣分的警示。報告列出這些結果的數量。詳見 spec 第 5 和第 14 節。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] Term List 的 schema：原文詞、各 Direction 的允許譯法（可以多於一個）、類別、未命中時的 Severity（預設 minor）、繁→簡殘留字檢查的例外、備註
- [ ] Term List 的格式驗證和重覆條目檢查；Term List 的 hash 記入 Run 紀錄
- [ ] 翻譯 prompt 只注入原文出現過的條目
- [ ] 原文出現 Term List 的詞，譯文卻找不到任何允許的譯法（英文不分大小寫）→ Terminology Finding，Severity 取自條目，原文片段是那個詞，譯文片段留空
- [ ] 繁→英譯文殘留漢字 → Locale & Format minor，每個 Component 中每段連續漢字記一次
- [ ] 以下情況只產生警示，不計分：長度比例超出範圍（範圍按 Direction 設定）、原文的數字在譯文找不到（去掉千位分隔號；繁→英處理 萬、億 的換算）、直接引語數量不同、疑似 Refusal（符合拒絕語樣式而且長度遠短於原文）、要翻譯的 Component 譯文與原文完全相同、不翻譯的 Component 被改動
- [ ] 報告列出 Deterministic Finding 按 Dimension 的數量，以及各類警示的數量
- [ ] 每條規則至少有一個測試，只用虛構文字

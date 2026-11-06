# 02: Block 誘因實驗（只送 Main component）

**What to build:** Pan 用同一批文章跑兩次方案 A，一次送完整 JSON，一次只送 Main component，然後在報告看到兩者的 Block 率差別，以及哪些文章的結果不同，從而知道周邊內容會不會誘發 Block（Q11）。報告的 Block 率改為同時列出按 Run 計和按文章計兩個數字。詳見 spec 第 4 和第 10 節。

**Blocked by:** 01

**Status:** ready-for-agent

- [ ] 輸入版本 `main-only` 只送 Main component（title、highlight、heading、paragraph、quote），其餘 Component 不送；輸入版本是 Run key 的一部分
- [ ] 每個 Approach × Direction 的 Block 率同時列出兩個數字：按 Run 計，以及按文章計（任一 Run 被 Block 即算）
- [ ] 報告有獨立的「Block 誘因實驗」一節：同一批文章在 `full` 和 `main-only` 下的 Block 率，附分子和分母，並列出兩者結果不同的文章 id
- [ ] 只有 `full` 的文章不出現在這一節的比較中
- [ ] `--dry-run` 分開列出兩個輸入版本的請求數

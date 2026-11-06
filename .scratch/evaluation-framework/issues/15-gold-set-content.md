# 15: Gold set 內容

**What to build:** 寫出約 100 個 Gold set 項目，放在 `data/`，用來校準 Judge：約 25 個 Softening 變體、約 25 個無錯樣本、約 50 個真實候選譯文的標註（Q14）。這是內容工作：由 Claude 撰寫，Pan 抽查約 30 條。標註真實譯文之前，要先有 Pan 用真 key 跑出的第一批譯文。

**Blocked by:** 14；另需 Pan 先用真 key 跑出第一批譯文（01、05、06 的輸出）

**Status:** ready-for-agent

- [ ] Softening 變體：每個指向一條 Must-retain fact，涵蓋 (a)–(f)，political 和 general 約各一半，兩個 Direction 都有
- [ ] 無錯樣本約 25 個，兩個 Direction 都有
- [ ] 真實候選譯文的標註約 50 個，取自第一批 Run，涵蓋多個 Approach
- [ ] 全部通過 14 的驗證，並列出各類別的數量
- [ ] 用 `gold sample` 抽約 30 條給 Pan，並把 Pan 的修正寫回
- [ ] 繁→英的一半需要懂英文的人抽查；如果沒有人手，在校準結果中註明
- [ ] ticket 的留言只寫數量，不貼內容

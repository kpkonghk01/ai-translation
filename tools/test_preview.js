// 用法：node tools/test_preview.js <preview.html> [真實 CSV]
// 只驗證 preview.html 內嵌的 adapter；對真實 CSV 只輸出彙總數字，不輸出任何內容。
const fs = require('fs');
const assert = require('assert');

const [htmlPath, csvPath] = process.argv.slice(2);
const html = fs.readFileSync(htmlPath, 'utf8');
const scripts = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)];
assert.strictEqual(scripts.length, 1, '應該只有一個內嵌 script');
assert(!/https?:\/\//.test(html.replace(/<!--[\s\S]*?-->/g, '')), '頁面不應含外部 URL');

const mod = { exports: {} };
new Function('module', scripts[0][1])(mod);
const P = mod.exports;

// parseCsv：引號、雙引號跳脫、欄內換行、CRLF、BOM、結尾空行
assert.deepStrictEqual(P.parseCsv('a,b\n'), [['a', 'b']]);
assert.deepStrictEqual(P.parseCsv('"a,1","b""2"\r\n"c\nd",e'), [['a,1', 'b"2'], ['c\nd', 'e']]);
assert.deepStrictEqual(P.parseCsv('﻿x,y\n\n'), [['x', 'y']]);
assert.deepStrictEqual(P.parseCsv('"",z\n'), [['', 'z']]);

// 示範資料：走完整 CSV → adapter 流程
const demo = P.load(P.buildDemoCsv());
assert.strictEqual(demo.entries.length, 5);
assert.strictEqual(demo.entries[0].article.components.length, 11);
assert.strictEqual(P.outcomeLabel(demo.entries[1]), 'CONTENT_BLOCKED / PROHIBITED_CONTENT');
assert.strictEqual(P.outcomeLabel(demo.entries[2]), '有輸出');
assert.strictEqual(P.outcomeLabel(demo.entries[3]), '解析失敗');
assert(demo.entries[4].parseNote, '純文字 user turn 應有 parseNote');
assert(demo.entries[0].article.components[8].code.includes('</script>'), 'code component 應原樣保留');
console.log('demo + parseCsv tests: OK');

if (csvPath) {
  const r = P.load(fs.readFileSync(csvPath, 'utf8'));
  const labels = {}, buckets = {}, types = {};
  let bad = 0, noComp = 0, dupIds = 0;
  for (const e of r.entries) {
    if (e.parseError) { bad++; continue; }
    const l = P.outcomeLabel(e); labels[l] = (labels[l] || 0) + 1;
    buckets[e.stats.bucket] = (buckets[e.stats.bucket] || 0) + 1;
    for (const [t, n] of Object.entries(e.stats.types)) types[t] = (types[t] || 0) + n;
    if (!e.stats.nComponents) noComp++;
    const ids = e.article.components.map(c => c.id);
    if (new Set(ids).size !== ids.length) dupIds++;
  }
  console.log(JSON.stringify({ rows: r.rowCount, entries: r.entries.length, notes: r.notes, parseErrors: bad, noComponents: noComp, articlesWithDuplicateIds: dupIds, outcomes: labels, lengthBuckets: buckets, componentTypes: types }, null, 1));
}

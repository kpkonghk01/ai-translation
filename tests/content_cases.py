"""內容檢查測試共用的基底：造一對虛構的原文與譯文，跑 translate 和 check，取回每個 Component 的結果。"""
import json
import os

import gemini_samples
from fake_transport import FakeTransport
from fixtures import ARTICLE_ID, Workspace


def build(components, title="虛構標題", tags=("虛構",), translated_title="Fictional headline", translated_tags=("Fictional",)):
    """components 是 [(type, 原文欄位, 譯文欄位)]；回傳 (原文文章, 譯文)，Component id 依序是 9000001-001、002…"""
    source = {"title": title, "mainChannel": "社會新聞", "tags": list(tags), "components": [], "meta": {"source": "public-web", "unknown": []}}
    translation = {"title": translated_title, "tags": list(translated_tags), "components": []}
    for n, (type_, source_fields, translated_fields) in enumerate(components, 1):
        component_id = f"{ARTICLE_ID}-{n:03d}"
        source["components"].append({"id": component_id, "type": type_, **source_fields})
        translation["components"].append({"id": component_id, "type": type_, **translated_fields})
    return source, translation


def paragraph(source_text, translated_text):
    return ("paragraph", {"text": source_text}, {"text": translated_text})


class ContentCheckCase(Workspace):
    def check_pair(self, source, translation, term_list=None):
        """翻譯（假 transport 回傳 translation）再 check；回傳 {component_id: 結果列}。"""
        self.write_json(os.path.join(self.articles_dir, f"{ARTICLE_ID}.json"), source)
        if term_list is not None:
            self.use_term_list(term_list)
        transport = FakeTransport(gemini_samples.ok(json.dumps(translation, ensure_ascii=False)))
        self.assertEqual(self.run_cli("translate", transport=transport)[0], 0)
        code, _, err = self.run_cli("check")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "components.jsonl"), encoding="utf-8") as f:
            return {row["component_id"]: row for row in map(json.loads, filter(str.strip, f))}

    def report_text(self):
        code, _, err = self.run_cli("report")
        self.assertEqual(code, 0, err)
        with open(os.path.join(self.out_dir, "report.md"), encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def findings(row):
        return [(f["dimension"], f["subtype"], f["severity"]) for f in row["findings"]]

    @staticmethod
    def warnings(row):
        return [w["kind"] for w in row["warnings"]]

"""hk01_collect 的測試（不連網）。用法：python3 -I tools/test_hk01_collect.py"""
import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hk01_collect as C  # noqa: E402

SITEMAP = """<?xml version="1.0"?><urlset>
<url><loc>https://www.hk01.com/社會新聞/1001/slug-a</loc><news:news><news:title>虛構｜男子涉強姦　被判囚</news:title><news:publication_date>2026-10-28T10:00:00+08:00</news:publication_date></news:news></url>
<url><loc>https://www.hk01.com/突發/1002/slug-b</loc><news:news><news:title>虛構｜持刀襲擊　一人受傷</news:title><news:publication_date>2026-10-28T09:00:00+08:00</news:publication_date></news:news></url>
<url><loc>https://www.hk01.com/熱爆話題/1003/slug-c</loc><news:news><news:title>虛構｜茶餐廳新品 &amp; 飲品</news:title><news:publication_date>2026-10-28T08:00:00+08:00</news:publication_date></news:news></url>
<url><loc>https://www.hk01.com/即時娛樂/1004/slug-d</loc><news:news><news:title>虛構｜殺青花絮</news:title><news:publication_date>2026-10-28T07:00:00+08:00</news:publication_date></news:news></url>
<url><loc>https://evil.example.com/社會新聞/1005/x</loc><news:news><news:title>強姦</news:title></news:news></url>
<url><loc>https://www.hk01.com/channel/2/社會新聞</loc><news:news><news:title>頻道頁</news:title></news:news></url>
</urlset>"""


class SitemapTests(unittest.TestCase):
    def setUp(self):
        self.entries = C.parse_sitemap(SITEMAP)

    def test_filters_bad_hosts_and_non_articles(self):
        self.assertEqual([e["url"].split("/")[-2] for e in self.entries], ["1001", "1002", "1003", "1004"])

    def test_fields_and_entities(self):
        e = {x["url"].split("/")[-2]: x for x in self.entries}
        self.assertEqual(e["1003"]["title"], "虛構｜茶餐廳新品 & 飲品")
        self.assertEqual(e["1001"]["section"], "社會新聞")
        self.assertEqual(e["1001"]["date"], "2026-10-28T10:00:00+08:00")

    def test_pick_candidates_topics_and_controls(self):
        got = {r["url"].split("/")[-2]: r["topic"] for r in C.pick_candidates(self.entries, controls=5, seed=1)}
        self.assertEqual(got["1001"], "sexual")
        self.assertEqual(got["1002"], "violence")
        self.assertEqual(got["1003"], "control")
        self.assertNotIn("1004", got)  # 即時娛樂「殺青」：頻道不在敏感名單，也不是對照頻道

    def test_controls_never_contain_sensitive_keywords(self):
        for r in C.pick_candidates(self.entries, controls=5, seed=1):
            if r["topic"] == "control":
                self.assertIsNone(C.classify(r["title"]))

    def test_control_sampling_is_deterministic(self):
        many = [{"url": f"https://www.hk01.com/熱爆話題/{i}/x", "title": f"普通新聞{i}", "date": "", "section": "熱爆話題"} for i in range(50)]
        a = [r["url"] for r in C.pick_candidates(many, 10, seed=7)]
        b = [r["url"] for r in C.pick_candidates(many, 10, seed=7)]
        self.assertEqual(a, b)
        self.assertEqual(len(a), 10)


class ReconvertTests(unittest.TestCase):
    RAW = {"articleId": 7, "title": "虛構", "mainCategory": "社會新聞", "canonicalUrl": "/社會新聞/7/slug", "tags": [],
           "blocks": [{"blockType": "text", "htmlTokens": [[{"type": "text", "content": "正文"}]]}, {"blockType": "code", "htmlString": "<b>x</b>"}]}

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.raw, self.out = os.path.join(self.tmp, "raw"), os.path.join(self.tmp, "out")
        os.makedirs(self.raw)
        os.makedirs(self.out)
        C.save_json(os.path.join(self.raw, "7.json"), self.RAW)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def read_out(self):
        with open(os.path.join(self.out, "7.json"), encoding="utf-8") as f:
            return json.load(f)

    def test_reconvert_without_existing_output_builds_url_from_raw(self):
        C.cmd_reconvert(argparse.Namespace(raw=self.raw, out=self.out))
        got = self.read_out()
        self.assertEqual(got["meta"]["url"], "https://www.hk01.com/社會新聞/7/slug")
        self.assertEqual([c["type"] for c in got["components"]], ["paragraph", "code"])

    def test_reconvert_preserves_topic_url_and_fetched_at(self):
        C.save_json(os.path.join(self.out, "7.json"), {"meta": {"url": "https://www.hk01.com/a/7/old", "fetchedAt": "2026-01-01T00:00:00+00:00", "topic": "sexual"}})
        C.cmd_reconvert(argparse.Namespace(raw=self.raw, out=self.out))
        m = self.read_out()["meta"]
        self.assertEqual((m["url"], m["fetchedAt"], m["topic"]), ("https://www.hk01.com/a/7/old", "2026-01-01T00:00:00+00:00", "sexual"))


class RedirectTests(unittest.TestCase):
    def test_redirect_to_other_host_is_refused(self):
        h = C._SafeRedirect()
        req = urllib.request.Request("https://global.hk01.com/x/1/x")
        with self.assertRaises(ValueError):
            h.redirect_request(req, None, 301, "Moved", {}, "https://evil.example.com/a/1/x")
        with self.assertRaises(ValueError):
            h.redirect_request(req, None, 301, "Moved", {}, "http://global.hk01.com/a/1/x")

    def test_redirect_within_allowed_hosts_is_followed(self):
        h = C._SafeRedirect()
        req = urllib.request.Request("https://global.hk01.com/x/1/x")
        new = h.redirect_request(req, None, 301, "Moved", {}, "https://global.hk01.com/%E7%A4%BE/1/slug")
        self.assertEqual(new.full_url, "https://global.hk01.com/%E7%A4%BE/1/slug")

    def test_http_get_rejects_bad_url_before_any_request(self):
        with self.assertRaises(ValueError):
            C.http_get("https://evil.example.com/a/1/x")


if __name__ == "__main__":
    unittest.main()

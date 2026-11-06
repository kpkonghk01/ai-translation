"""hk01_web 的測試。只用合成資料，不含任何真實文章。

用法：python3 -I tools/test_hk01_web.py
（-I 下 cwd 不在 sys.path，所以下面先把 tools/ 加進去。）
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hk01_web as W  # noqa: E402


def page(article):
    data = {"props": {"initialProps": {"pageProps": {"article": article}}}}
    # Next.js 會把 JSON 內的 "<" 轉義成 <，內嵌的 </script> 才不會提早結束標籤
    body = json.dumps(data).replace("<", "\\u003c")
    return f'<html><head></head><body><script id="__NEXT_DATA__" type="application/json">{body}</script></body></html>'


def fake_article(**over):
    art = {
        "articleId": 111,
        "title": "虛構標題",
        "mainCategory": "社會新聞",
        "publishTime": 1,
        "lastModifyTime": 2,
        "tags": [{"tagName": "標籤一"}, {"tagName": "標籤二"}],
        "blocks": [
            {"blockType": "summary", "summary": ["摘要一", "摘要二"]},
            {"blockType": "text", "htmlTokens": [[{"type": "text", "content": "第一段"}], [{"type": "h3", "content": "小標題"}], [{"type": "text", "content": "第二段 &amp; 引號"}]]},
            {"blockType": "image", "image": {"caption": "圖片說明"}},
            {"blockType": "image", "image": {}},
            {"blockType": "related", "articles": [{"data": {"title": "相關一"}}, {"data": {"title": "相關二"}}]},
        ],
    }
    art.update(over)
    return art


class UrlTests(unittest.TestCase):
    def test_accepts_hk01_hosts(self):
        W.check_url("https://www.hk01.com/社會新聞/123/x")
        W.check_url("https://global.hk01.com/社会新闻/123/x")

    def test_rejects_other_hosts_and_schemes(self):
        for bad in (
            "http://www.hk01.com/a/1/x",
            "https://www.hk01.com.evil.com/a/1/x",
            "https://evil.com/www.hk01.com/a/1/x",
            "https://user@evil.com/a/1/x",
            "https://hk01.com/a/1/x",
            "file:///etc/passwd",
            "ftp://www.hk01.com/a/1/x",
        ):
            with self.assertRaises(ValueError, msg=bad):
                W.check_url(bad)

    def test_encode_url_makes_ascii_and_is_idempotent(self):
        raw = "https://www.hk01.com/熱爆話題/60404311/chatgpt再惹官司-亞馬遜?a=1&b=二"
        enc = W.encode_url(raw)
        enc.encode("ascii")  # 不可含非 ASCII
        self.assertTrue(enc.startswith("https://www.hk01.com/%E7%86%B1"))
        self.assertIn("?a=1&b=%E4%BA%8C", enc)
        self.assertEqual(W.encode_url(enc), enc)  # 已編碼的不能被重複編碼
        self.assertEqual(W.article_id_from_url(enc), 60404311)

    def test_encode_url_still_rejects_bad_hosts(self):
        with self.assertRaises(ValueError):
            W.encode_url("https://evil.example.com/熱/1/x")

    def test_article_id(self):
        self.assertEqual(W.article_id_from_url("https://www.hk01.com/社會新聞/60397549/slug-here"), 60397549)
        self.assertEqual(W.article_id_from_url("https://www.hk01.com/突發/985166"), 985166)
        with self.assertRaises(ValueError):
            W.article_id_from_url("https://www.hk01.com/channel/2/社會新聞")
        with self.assertRaises(ValueError):
            W.article_id_from_url("https://www.hk01.com/")


class ConvertTests(unittest.TestCase):
    def setUp(self):
        self.a = W.parse_page(page(fake_article()), "https://www.hk01.com/x/111/y", "2026-10-28T00:00:00Z")

    def test_metadata(self):
        self.assertEqual(self.a["title"], "虛構標題")
        self.assertEqual(self.a["mainChannel"], "社會新聞")
        self.assertEqual(self.a["tags"], ["標籤一", "標籤二"])
        m = self.a["meta"]
        self.assertEqual((m["source"], m["articleId"], m["lastModifyTime"]), ("public-web", 111, 2))
        self.assertEqual(m["unknown"], [])

    def test_components_in_order_with_sequential_ids(self):
        got = [(c["id"], c["type"]) for c in self.a["components"]]
        self.assertEqual(got, [
            ("111-001", "highlight"), ("111-002", "highlight"),
            ("111-003", "paragraph"), ("111-004", "heading"), ("111-005", "paragraph"),
            ("111-006", "image"), ("111-007", "image"), ("111-008", "related-article"),
        ])

    def test_text_and_levels(self):
        c = {x["id"]: x for x in self.a["components"]}
        self.assertEqual(c["111-004"]["level"], "h3")
        self.assertEqual(c["111-005"]["text"], "第二段 & 引號")  # 實體要還原
        self.assertEqual(c["111-006"]["caption"], "圖片說明")
        self.assertEqual(c["111-008"]["titles"], ["相關一", "相關二"])

    def test_inline_tokens_in_one_group_form_one_paragraph(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [
            [{"type": "text", "content": "前文"}, {"type": "text", "content": "後文"}],
            [{"type": "text", "content": "另一段"}],
        ]}])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual([c["text"] for c in a["components"]], ["前文後文", "另一段"])
        self.assertEqual(a["meta"]["unknown"], [])

    def test_known_inline_types_are_joined_without_being_flagged(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [[
            {"type": "text", "content": "前"}, {"type": "boldText", "content": "粗"},
            {"type": "link", "content": "連", "link": "https://x"}, {"type": "boldLink", "content": "粗連", "link": "https://y"},
        ]]}])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual([c["text"] for c in a["components"]], ["前粗連粗連"])
        self.assertEqual(a["meta"]["unknown"], [])

    def test_quote_and_gallery_blocks(self):
        art = fake_article(blocks=[
            {"blockType": "quote", "message": "引言內容", "author": "某人"},
            {"blockType": "quote", "message": "無署名", "author": None},
            {"blockType": "gallery", "images": [{"caption": "圖一"}, {"caption": "圖二"}, {}]},
        ])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["components"][0], {"id": "111-001", "type": "quote", "text": "引言內容", "source": "某人"})
        self.assertEqual(a["components"][1], {"id": "111-002", "type": "quote", "text": "無署名", "source": ""})
        self.assertEqual(a["components"][2], {"id": "111-003", "type": "gallery", "captions": ["圖一", "圖二", ""]})
        self.assertEqual(a["meta"]["unknown"], [])

    def test_code_is_kept_verbatim_and_not_unescaped(self):
        html_string = '<div class="x">&amp;<script>var a = "<b>";</script></div>'
        a = W.parse_page(page(fake_article(blocks=[{"blockType": "code", "htmlString": html_string, "image": None}])), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["components"], [{"id": "111-001", "type": "code", "code": html_string}])
        self.assertEqual(a["meta"]["warnings"], [])

    def test_motiongraphic_is_an_image_caption(self):
        block = {"blockType": "motiongraphic", "htmlTokens": [], "image": {"caption": "動態圖說明", "images": {"static": {"url": "u"}}}}
        a = W.parse_page(page(fake_article(blocks=[block])), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["components"], [{"id": "111-001", "type": "image", "caption": "動態圖說明"}])
        self.assertEqual(a["meta"]["unknown"], [])

    def test_video_and_brightcove(self):
        blocks = [{"blockType": "video", "caption": ""}, {"blockType": "brightcove", "caption": None}, {"blockType": "video", "caption": "影片說明"}]
        a = W.parse_page(page(fake_article(blocks=blocks)), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual([c["type"] for c in a["components"]], ["video", "video", "video"])
        self.assertNotIn("caption", a["components"][0])
        self.assertNotIn("caption", a["components"][1])
        self.assertEqual(a["components"][2]["caption"], "影片說明")
        self.assertEqual(a["meta"]["unknown"], [])

    def test_faq(self):
        def node(*groups):
            return {"htmlTokens": [[{"type": "text", "content": g}] for g in groups]}
        block = {"blockType": "faq", "questionsAndAnswers": [
            {"question": node("問一"), "answer": node("答一甲", "答一乙")},
            {"question": node("問二"), "answer": node("答二")},
        ]}
        a = W.parse_page(page(fake_article(blocks=[block])), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["components"][0]["type"], "faq")
        self.assertEqual(a["components"][0]["items"], [{"question": "問一", "answer": "答一甲\n答一乙"}, {"question": "問二", "answer": "答二"}])

    def test_table(self):
        def cell(tt, text):
            return {"type": "td", "htmlTokens": [[{"type": tt, "content": text}]]}
        table = {"type": "table", "name": "表名", "description": "表說明", "children": [
            {"type": "caption", "htmlTokens": [[{"type": "text", "content": "表題"}]]},
            {"type": "colgroup", "children": [{"type": "col", "width": "50%"}]},
            {"type": "row", "children": [cell("boldText", "甲"), cell("boldText", "乙")]},
            {"type": "row", "children": [cell("text", "一"), cell("boldLink", "二")]},
        ]}
        a = W.parse_page(page(fake_article(blocks=[{"blockType": "table", "table": table, "htmlString": "<table></table>"}])), "https://www.hk01.com/x/111/y", "t")
        c = a["components"][0]
        self.assertEqual((c["type"], c["name"], c["description"], c["caption"]), ("table", "表名", "表說明", "表題"))
        self.assertEqual(c["rows"], [["甲", "乙"], ["一", "二"]])
        self.assertEqual(a["meta"]["unknown"], [])

    def test_table_unknown_nodes_are_recorded(self):
        table = {"type": "table", "name": "", "description": "", "children": [
            {"type": "tfoot"},
            {"type": "row", "children": [{"type": "weird", "htmlTokens": []}]},
        ]}
        a = W.parse_page(page(fake_article(blocks=[{"blockType": "table", "table": table}])), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["meta"]["unknown"], ["table-node:tfoot", "table-node:weird"])

    def test_special_blocks_do_not_count_as_main_text(self):
        blocks = [{"blockType": "code", "htmlString": "x" * 500}, {"blockType": "table", "table": {"type": "table", "name": "表" * 50, "description": "", "children": []}}]
        a = W.parse_page(page(fake_article(blocks=blocks)), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(W.main_chars(a), len("虛構標題"))

    def test_flat_token_is_tolerated(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [{"type": "text", "content": "攤平"}]}])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual([c["text"] for c in a["components"]], ["攤平"])

    def test_unknown_inline_type_keeps_text_and_is_recorded(self):
        art = fake_article(blocks=[
            {"blockType": "text", "htmlTokens": [[{"type": "text", "content": "正文"}, {"type": "emoji", "content": "表情"}]]},
            {"blockType": "mystery"},
        ])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["meta"]["unknown"], ["token:emoji", "block:mystery"])
        self.assertEqual([c["text"] for c in a["components"]], ["正文表情"])  # 文字沒有被丟掉

    def test_empty_group_is_skipped(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [[], [{"type": "text", "content": ""}]]}])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(a["components"], [])

    def test_escaped_angle_brackets_survive_the_page_roundtrip(self):
        a = W.extract_article(page(fake_article(title="含 </script> 的標題")))
        self.assertEqual(a["title"], "含 </script> 的標題")

    def test_angle_bracket_in_text_is_warned(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [[{"type": "text", "content": "含<b>標籤</b>"}]]}])
        a = W.parse_page(page(art), "https://www.hk01.com/x/111/y", "t")
        self.assertEqual(len(a["meta"]["warnings"]), 1)

    def test_missing_or_bad_next_data(self):
        with self.assertRaises(W.ParseError):
            W.parse_page("<html></html>", "u", "t")
        with self.assertRaises(W.ParseError):
            W.parse_page('<script id="__NEXT_DATA__">{"props": {}}</script>', "u", "t")
        with self.assertRaises(W.ParseError):
            W.parse_page('<script id="__NEXT_DATA__">not json</script>', "u", "t")


class CoverageTests(unittest.TestCase):
    """原始物件裡每一段看得見的文字，都必須出現在轉換結果中。"""

    def convert(self, art):
        return W.convert_article(art, "https://www.hk01.com/x/111/y", "t")

    def test_every_known_block_type_is_fully_covered(self):
        blocks = fake_article()["blocks"] + [
            {"blockType": "quote", "message": "引言內容", "author": "某人"},
            {"blockType": "gallery", "images": [{"caption": "圖一"}, {"caption": ""}]},
            {"blockType": "code", "htmlString": "<div>&amp;</div>", "image": None},
            {"blockType": "video", "caption": "影片說明", "videoId": "abc", "type": "youtube"},
            {"blockType": "motiongraphic", "htmlTokens": [], "image": {"caption": "動態圖", "cdnUrl": "https://cdn/x"}},
            {"blockType": "faq", "questionsAndAnswers": [{"question": {"htmlTokens": [[{"type": "text", "content": "問"}]]},
                                                         "answer": {"htmlTokens": [[{"type": "boldText", "content": "答"}]]}}]},
            {"blockType": "table", "htmlString": "<table>不是正文</table>", "table": {"type": "table", "name": "表名", "description": "表說明", "children": [
                {"type": "caption", "htmlTokens": [[{"type": "text", "content": "表題"}]]},
                {"type": "row", "children": [{"type": "td", "htmlTokens": [[{"type": "text", "content": "格"}]]}]}]}},
        ]
        art = fake_article(blocks=blocks)
        self.assertEqual(W.uncovered_texts(art, self.convert(art)), [])

    def test_related_article_metadata_is_not_required(self):
        block = {"blockType": "related", "articles": [{"data": {"title": "相關一", "description": "相關文章的摘要",
                                                                 "zone": {"name": "社會", "publishName": "社會"}, "authors": [{"publishName": "記者"}]}}]}
        art = fake_article(blocks=[block])
        self.assertEqual(W.uncovered_texts(art, self.convert(art)), [])

    def test_text_dropped_by_the_converter_is_reported(self):
        art = fake_article()
        out = self.convert(art)
        out["components"] = [c for c in out["components"] if c.get("text") != "第一段"]
        self.assertEqual([u["text"] for u in W.uncovered_texts(art, out)], ["第一段"])

    def test_text_inside_an_unknown_block_type_is_reported(self):
        art = fake_article(blocks=[{"blockType": "poll", "question": {"content": "投票問題"}, "options": [{"title": "選項甲"}]}])
        got = W.uncovered_texts(art, self.convert(art))
        self.assertEqual(sorted(u["text"] for u in got), ["投票問題", "選項甲"])
        self.assertTrue(all(u["block"] == "poll" for u in got))

    def test_title_and_tags_are_checked(self):
        art = fake_article()
        out = self.convert(art)
        out["tags"] = ["標籤一"]
        self.assertEqual([u["text"] for u in W.uncovered_texts(art, out)], ["標籤二"])

    def test_html_entities_are_compared_after_unescaping(self):
        art = fake_article(blocks=[{"blockType": "text", "htmlTokens": [[{"type": "text", "content": "甲 &amp; 乙"}]]}])
        self.assertEqual(W.uncovered_texts(art, self.convert(art)), [])


class StatsTests(unittest.TestCase):
    def test_main_chars_excludes_peripheral(self):
        a = W.parse_page(page(fake_article()), "https://www.hk01.com/x/111/y", "t")
        # 標題4 + 摘要一/二 6 + 第一段 3 + 小標題 3 + 第二段 & 引號 7
        self.assertEqual(W.main_chars(a), 4 + 6 + 3 + 3 + len("第二段 & 引號"))

    def test_buckets(self):
        self.assertEqual([W.length_bucket(n) for n in (0, 999, 1000, 2999, 3000, 4999, 5000)], ["短", "短", "中", "中", "長", "長", "超長"])


class GlobalPageTests(unittest.TestCase):
    def test_only_article_region(self):
        html_ = (
            '<html><body><nav><p>導航不要</p></nav>'
            '<div class="page-article break-words"><div class="x"><h3>小标题</h3><p>第一段<br>续<a href="#">链接</a>文字</p><br/>'
            '<figure><img src="a.jpg"></figure><p>第二段</p></div></div>'
            '<footer><p>頁尾不要</p></footer></body></html>'
        )
        self.assertEqual(W.parse_global_page(html_), [("h3", "小标题"), ("p", "第一段续链接文字"), ("p", "第二段")])

    def test_no_article_region_raises(self):
        with self.assertRaises(W.ParseError):
            W.parse_global_page("<html><body><p>沒有文章區</p></body></html>")


if __name__ == "__main__":
    unittest.main()

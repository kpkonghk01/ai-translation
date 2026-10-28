"""sample_ground_truth 的測試（只用合成資料）。用法：python3 -I tools/test_sample_ground_truth.py"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sample_ground_truth as S  # noqa: E402


def article(aid, role="sensitive", domain="general", note="n", items=1, shared_component=None):
    return {"article_id": str(aid), "role": role, "domain": domain, "note": note,
            "items": [{"component_id": shared_component or f"{aid}-{i:03d}", "anchor": "x", "why": "w", "must_retain": [{"kind": "act", "fact": "f"}]} for i in range(items)]}


def gt():
    arts = [article(i) for i in range(1, 21)] + [article(100 + i, domain="china-political") for i in range(1, 21)]
    arts += [article(200, note="NOT-NEGATIVE? 例行公報", domain="china-political"), article(201, role="control", items=1),
             article(202, role="control", items=0), article(203, role="control", items=2)]
    return [{"articles": arts[:22]}, {"articles": arts[22:]}]


class SampleTests(unittest.TestCase):
    def ids(self, **kw):
        return [a["article_id"] for a in S.sample(gt(), **kw)["articles"]]

    def test_counts_per_stratum(self):
        got = {a["article_id"]: a for a in S.sample(gt(), general=4, political=3, seed=1)["articles"]}
        general = [a for a in got.values() if a["role"] == "sensitive" and a["domain"] == "general"]
        political = [a for a in got.values() if a["domain"] == "china-political" and not a["note"].startswith("NOT-NEGATIVE")]
        self.assertEqual((len(general), len(political)), (4, 3))

    def test_flagged_articles_and_controls_with_items_are_always_included(self):
        for seed in (1, 2, 3):
            ids = self.ids(general=2, political=2, seed=seed)
            self.assertIn("200", ids)                      # NOT-NEGATIVE?
            self.assertIn("201", ids)                      # 有 item 的對照
            self.assertIn("203", ids)
            self.assertNotIn("202", ids)                   # 沒有 item 的對照不需要審

    def test_seed_makes_the_sample_reproducible_and_different_seeds_differ(self):
        self.assertEqual(self.ids(general=5, political=5, seed=7), self.ids(general=5, political=5, seed=7))
        self.assertNotEqual(self.ids(general=5, political=5, seed=7), self.ids(general=5, political=5, seed=8))

    def test_output_is_sorted_by_article_id_and_has_no_duplicates(self):
        ids = self.ids(general=5, political=5, seed=1)
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(ids), len(set(ids)))

    def test_request_larger_than_pool_returns_whole_pool(self):
        ids = self.ids(general=999, political=999, seed=1)
        self.assertEqual(len([i for i in ids if i.isdigit() and int(i) < 100]), 20)

    def test_summary_counts_distinct_components(self):
        arts = [article(1, items=3, shared_component="1-014"), article(2, items=2)]
        s = S.summarize([{"articles": arts}])
        self.assertEqual((s["articles"], s["items"], s["distinct_components"], s["facts"]), (2, 5, 3, 5))


if __name__ == "__main__":
    unittest.main()

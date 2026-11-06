"""precommit_scan 的測試。用法：python3 -I tools/test_precommit_scan.py

這個檔案本身也會被 precommit_scan 掃描，所以凡是「應該被擋下」的樣本（真實格式的案件編號、key）
都在執行時才拼接出來，原始碼裡不會出現完整的字串。標題全部是虛構的。
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import precommit_scan as S  # noqa: E402

REAL_FORMAT_CASE = "HC" + "CC443/" + "2025"          # 真實格式（年份早於 2030）
REAL_FORMAT_CASE_SPACED = "DC" + "CC 424 / " + "2024"
FAKE_TITLE = "虛構標題　某某某涉嫌做了某件事　法庭判囚"


def added(*texts, path="tools/x.py"):
    return [(path, i, t) for i, t in enumerate(texts, 1)]


class PathTests(unittest.TestCase):
    def test_blocked_paths(self):
        bad = ["data/x.json", "refs/media-cms/a.js", ".venv/lib/x.py", ".env", ".env.local", "tools/.env", "data-prohibited.csv", "export.CSV"]
        self.assertEqual(len(S.path_violations(bad)), len(bad))

    def test_allowed_paths(self):
        ok = [".env.example", "tools/.env.example", "tools/fixture.csv", "docs/data.md", "tools/refs.py", "datasets/readme.md"]
        self.assertEqual(S.path_violations(ok), [])


class DiffParseTests(unittest.TestCase):
    DIFF = "\n".join([
        "diff --git a/tools/a.py b/tools/a.py",
        "index 1111111..2222222 100644",
        "--- a/tools/a.py",
        "+++ b/tools/a.py",
        "@@ -3,0 +4,2 @@ def f():",
        "+first added",
        "+second added",
        "@@ -10 +12 @@",
        "-old line",
        "+replacement",
        "\\ No newline at end of file",
        "diff --git a/docs/new.md b/docs/new.md",
        "new file mode 100644",
        "--- /dev/null",
        "+++ b/docs/new.md",
        "@@ -0,0 +1 @@",
        "+++ content that starts with plus signs",
    ])

    def test_added_lines_with_paths_and_line_numbers(self):
        self.assertEqual(S.added_lines(self.DIFF), [
            ("tools/a.py", 4, "first added"),
            ("tools/a.py", 5, "second added"),
            ("tools/a.py", 12, "replacement"),
            ("docs/new.md", 1, "++ content that starts with plus signs"),
        ])

    def test_deleted_file_contributes_nothing(self):
        diff = "diff --git a/x b/x\ndeleted file mode 100644\n--- a/x\n+++ /dev/null\n@@ -1 +0,0 @@\n-gone"
        self.assertEqual(S.added_lines(diff), [])


class ContentTests(unittest.TestCase):
    def test_real_format_case_numbers_are_blocked(self):
        problems = S.content_violations(added(f"案件({REAL_FORMAT_CASE})", REAL_FORMAT_CASE_SPACED))
        self.assertEqual(len(problems), 2)
        self.assertIn("tools/x.py:1", problems[0])
        self.assertIn("2030", problems[0])

    def test_fictitious_case_numbers_are_allowed(self):
        self.assertEqual(S.content_violations(added("HCCC101/2030", "DCCC 303/2031", "年份 2025/10")), [])

    def test_key_shapes_are_blocked_without_printing_the_key(self):
        key = "sk-" + "or-v1-" + "a" * 40
        lines = added(f'k = "{key}"', "AIza" + "B" * 35, "-----BEGIN " + "RSA PRIVATE KEY-----", 'api_key = "' + "z" * 20 + '"')
        problems = S.content_violations(lines)
        self.assertEqual(len(problems), 4)
        self.assertTrue(all(key not in p for p in problems))

    def test_reading_keys_from_the_environment_is_fine(self):
        self.assertEqual(S.content_violations(added('key = os.environ["OPENROUTER_API_KEY"]', "api_key = os.getenv('GEMINI_API_KEY')")), [])

    def test_full_titles_are_blocked_ignoring_whitespace(self):
        line = "title = '" + FAKE_TITLE.replace("　", " ") + "'"
        problems = S.content_violations(added(line), titles={FAKE_TITLE})
        self.assertEqual(len(problems), 1)
        self.assertIn("完整標題", problems[0])

    def test_titles_written_with_unicode_escapes_are_still_blocked(self):
        for line in ("TITLE = " + repr(FAKE_TITLE), json.dumps({"title": FAKE_TITLE})):   # repr 轉義全形空白；json 轉義全部中文
            self.assertIn("\\u", line)
            self.assertEqual(len(S.content_violations(added(line), titles={FAKE_TITLE})), 1, line)

    def test_short_titles_and_partial_mentions_are_ignored(self):
        self.assertEqual(S.content_violations(added("某某某涉嫌", "短標題"), titles={FAKE_TITLE, "短標題"}), [])


class LoadTitlesTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.root)

    def write(self, rel, obj):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False))

    def test_loads_titles_from_web_articles_baselines_and_cms_csv(self):
        self.write("data/web-articles/1.json", {"title": "網上文章標題甲"})
        self.write("data/web-articles-china/2.json", {"title": "網上文章標題乙"})
        self.write("data/baseline-service/1.json", {"title": "简体标题甲"})
        self.write("data/web-articles-raw/1.json", {"title": "不應讀取的原始物件"})
        self.write("data/web-articles-global/1.json", {"items": []})
        self.write("data/web-articles/fetch-log.tsv", "not json")
        history = [{"role": "user", "parts": [{"text": json.dumps({"title": "CMS 標題丙"}, ensure_ascii=False)}]}]
        self.write("data-prohibited.csv", '"abc","' + json.dumps(history, ensure_ascii=False).replace('"', '""') + '"\n')
        titles, notes = S.load_titles(self.root)
        self.assertEqual(titles, {"網上文章標題甲", "網上文章標題乙", "简体标题甲", "CMS 標題丙"})
        self.assertEqual(notes, [])

    def test_missing_data_is_reported_not_silent(self):
        titles, notes = S.load_titles(self.root)
        self.assertEqual(titles, set())
        self.assertTrue(any("略過標題檢查" in n for n in notes))

    def test_unreadable_file_is_reported(self):
        self.write("data/web-articles/9.json", "{broken")
        _, notes = S.load_titles(self.root)
        self.assertTrue(any("讀不到" in n for n in notes))


class GitIntegrationTests(unittest.TestCase):
    """在暫存的 git repo 裡跑完整流程：真的 `git add`，再由 main() 讀 staged 內容。"""

    def setUp(self):
        # 在 pre-commit hook 裡執行時，git 會設定 GIT_INDEX_FILE 等變數；不清掉的話會碰到外層 repo 的 index
        self._env = {k: v for k, v in os.environ.items() if k.startswith("GIT_")}
        for k in self._env:
            del os.environ[k]
        self.repo = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)

    def tearDown(self):
        shutil.rmtree(self.repo)
        os.environ.update(self._env)

    def stage(self, rel, text):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path) or self.repo, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        subprocess.run(["git", "add", "-f", rel], cwd=self.repo, check=True)

    def run_main(self, *argv):
        devnull = open(os.devnull, "w")
        old_out, old_err = sys.stdout, sys.stderr
        sys.stdout = sys.stderr = devnull
        try:
            return S.main(list(argv), cwd=self.repo)
        finally:
            sys.stdout, sys.stderr = old_out, old_err
            devnull.close()

    def test_clean_change_passes(self):
        self.stage("tools/ok.py", "x = 'HCCC101/2030'\n")
        self.assertEqual(self.run_main(), 0)

    def test_real_format_case_number_blocks(self):
        self.stage("tools/bad.py", f"x = '{REAL_FORMAT_CASE}'\n")
        self.assertEqual(self.run_main(), 1)

    def test_force_added_data_file_blocks(self):
        self.stage("data/article.json", "{}\n")
        self.assertEqual(self.run_main(), 1)

    def test_real_title_from_local_data_blocks(self):
        os.makedirs(os.path.join(self.repo, "data", "web-articles"))
        with open(os.path.join(self.repo, "data", "web-articles", "1.json"), "w", encoding="utf-8") as f:
            json.dump({"title": FAKE_TITLE}, f, ensure_ascii=False)
        self.stage("tools/fixture.py", f"TITLE = '{FAKE_TITLE}'\n")
        self.assertEqual(self.run_main(), 1)

    def test_all_mode_scans_committed_content_too(self):
        self.stage("tools/bad.py", f"x = '{REAL_FORMAT_CASE}'\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--no-verify", "-m", "t"], cwd=self.repo, check=True)
        self.assertEqual(self.run_main(), 0)          # 沒有 staged 的變更
        self.assertEqual(self.run_main("--all"), 1)   # 但已 commit 的內容有問題

    def test_all_mode_reads_the_index_when_a_tracked_file_is_deleted_from_disk(self):
        self.stage("tools/bad.py", f"x = '{REAL_FORMAT_CASE}'\n")
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "--no-verify", "-m", "t"], cwd=self.repo, check=True)
        os.remove(os.path.join(self.repo, "tools", "bad.py"))
        self.assertEqual(self.run_main("--all"), 1)

    def test_git_failure_is_an_error_not_a_clean_result(self):
        not_a_repo = tempfile.mkdtemp()
        try:
            with self.assertRaises(subprocess.CalledProcessError):
                S.main([], cwd=not_a_repo)
        finally:
            shutil.rmtree(not_a_repo)


if __name__ == "__main__":
    unittest.main()

"""測試用的虛構文章、設定檔和共用的 TestCase。只用虛構資料；看起來像 key 的字串在執行時才拼接。"""
import contextlib
import copy
import io
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

import gemini_samples
from fake_transport import FakeTransport
from translation_eval import cli
from translation_eval.transport import OFFLINE_ENV

# 假 key：執行時才拼接，避免原始碼裡出現像 key 的字串
FAKE_KEY = "-".join(["fakekey", "unit", "test", "0001"])
KEY_ENV = "GEMINI_API_KEY"


def source_article(article_id: str = "9000001") -> dict:
    return {
        "title": "虛構市政廳翻新工程延誤",
        "mainChannel": "社會新聞",
        "tags": ["虛構", "測試"],
        "components": [
            {"id": f"{article_id}-001", "type": "highlight", "text": "翻新工程延誤半年，預算超支。"},
            {"id": f"{article_id}-002", "type": "paragraph", "text": "市民陳虛構表示，工程拖延令他十分不滿。"},
            {"id": f"{article_id}-003", "type": "image", "caption": "施工中的虛構市政廳"},
            {"id": f"{article_id}-004", "type": "code", "code": "<div>embed</div>"},
        ],
        "meta": {"source": "public-web", "articleId": int(article_id), "unknown": [], "warnings": []},
    }


def good_translation(article: dict) -> dict:
    """與 source_article 同結構、Component id 與 type 不變的英文譯文。"""
    english = {"text": "The renovation is six months late and over budget.", "caption": "The fictional city hall under construction"}
    components = []
    for c in article["components"]:
        t = {"id": c["id"], "type": c["type"]}
        if c["type"] == "paragraph":
            t["text"] = "Resident Chan Fictional said the delay left him deeply dissatisfied."
        elif c["type"] == "code":
            t["code"] = c["code"]
        elif "text" in c:
            t["text"] = english["text"]
        else:
            t["caption"] = english["caption"]
        components.append(t)
    return {"title": "Fictional city hall renovation delayed", "tags": ["Fictional", "Test"], "components": components}


ARTICLE_ID = "9000001"


def ok_response(article_id: str = ARTICLE_ID):
    """Gemini 的正常回應，內容是 good_translation。"""
    return gemini_samples.ok(json.dumps(good_translation(source_article(article_id)), ensure_ascii=False))


def translation_with(mutate):
    """good_translation 加上一處改動，用來造出結構有問題的譯文。"""
    translation = copy.deepcopy(good_translation(source_article(ARTICLE_ID)))
    mutate(translation)
    return translation


def component(translation, suffix):
    return next(c for c in translation["components"] if c["id"] == f"{ARTICLE_ID}-{suffix}")


# 虛構的 Term List：前兩條出現在 source_article 裡，第三條不會出現
TERM_LIST = {"terms": [
    {"source": "虛構市政廳", "category": "organization", "translations": {"trad-to-en": ["Fictional City Hall"]}},
    {"source": "陳虛構", "category": "person", "translations": {"trad-to-en": ["Chan Fictional", "Fictional Chan"]},
     "severity": "major", "simplified_exceptions": ["虛"], "note": "測試用"},
    {"source": "從未出現的詞", "category": "place", "translations": {"trad-to-en": ["Never Mentioned"]}},
]}

APPROACH = {
    "id": "a",
    "family": "gemini",
    "provider": "gemini",
    "model": "gemini-3.1-flash-lite",
    "api_key_env": KEY_ENV,
    "request_mode": "article-json",
    "max_output_tokens": 65536,
    "timeout_seconds": 30,
    # 測試不真的等：退避間隔設為 0
    "retry": {"max_attempts": 3, "base_delay_seconds": 0},
}


class Workspace(unittest.TestCase):
    """每個測試一個暫存目錄：文章目錄、設定檔、data 目錄；以及執行指令的輔助方法。"""

    article_ids = ("9000001",)
    runs = 1
    extra_experiment: dict = {}   # 併入實驗設定的欄位（例如 term_list）

    def setUp(self):
        self.extra_experiment = {}
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        self.articles_dir = os.path.join(self.tmp, "articles")
        self.data_dir = os.path.join(self.tmp, "data")
        os.makedirs(self.articles_dir)
        for article_id in self.article_ids:
            self.write_json(os.path.join(self.articles_dir, f"{article_id}.json"), source_article(article_id))
        self.approach_path = os.path.join(self.tmp, "approach-a.json")
        self.write_json(self.approach_path, APPROACH)
        self.config_path = os.path.join(self.tmp, "experiment.json")
        self.write_json(self.config_path, self.experiment())
        # 離線旗標一律開著：忘了注入假 transport 的測試會被真 transport 拒絕，而不是連上網
        env = mock.patch.dict(os.environ, {KEY_ENV: FAKE_KEY, OFFLINE_ENV: "1"})
        env.start()
        self.addCleanup(env.stop)

    def experiment(self) -> dict:
        return {
            "id": "tracer",
            "articles": {"dir": self.articles_dir, "ids": list(self.article_ids)},
            "directions": ["trad-to-en"],
            "approaches": [{"config": self.approach_path, "runs": self.runs}],
            **self.extra_experiment,
        }

    def use_term_list(self, term_list: dict) -> str:
        path = os.path.join(self.tmp, "term-list.json")
        self.write_json(path, term_list)
        self.extra_experiment["term_list"] = path
        self.write_json(self.config_path, self.experiment())
        return path

    @staticmethod
    def write_json(path: str, obj) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)

    def rewrite_experiment(self, mutate) -> None:
        exp = copy.deepcopy(self.experiment())
        mutate(exp)
        self.write_json(self.config_path, exp)

    @property
    def out_dir(self) -> str:
        return os.path.join(self.data_dir, "experiments", "tracer")

    def run_cli(self, command: str, *extra: str, transport=None):
        """執行子命令，回傳 (結束碼, stdout, stderr)。"""
        out, err = io.StringIO(), io.StringIO()
        argv = [command, "--config", self.config_path, "--data-dir", self.data_dir, *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, transport=transport)
        return code, out.getvalue(), err.getvalue()

    def assert_fails_cleanly(self, needle: str) -> None:
        """輸入或設定不合法：非 0 結束，列出原因，不送請求，不寫出任何檔案（連 data 目錄也不建）。"""
        transport = FakeTransport()   # 任何請求都會令測試失敗
        for command_args in ((), ("--dry-run",)):
            code, out, err = self.run_cli("translate", *command_args, transport=transport)
            self.assertNotEqual(code, 0, command_args)
            self.assertIn(needle, err)
            self.assertEqual(out, "")
            self.assertFalse(os.path.exists(self.data_dir))
        self.assertEqual(transport.requests, [])

    def read_runs(self) -> list[dict]:
        with open(os.path.join(self.out_dir, "runs.jsonl"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]

    def all_output_text(self) -> str:
        """data 目錄下所有檔案的內容，供檢查有沒有不該出現的字串。"""
        chunks = []
        for root, _, names in os.walk(self.data_dir):
            for name in names:
                with open(os.path.join(root, name), "rb") as f:
                    chunks.append(f.read().decode("utf-8", errors="replace"))
        return "\n".join(chunks)

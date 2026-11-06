"""check_package_rules 的測試（只用合成的 package）。用法：python3 -I tools/test_check_package_rules.py"""
import contextlib
import io
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import check_package_rules as R  # noqa: E402


class PackageRulesTest(unittest.TestCase):
    def setUp(self):
        self.pkg = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.pkg)

    def write(self, name, text):
        path = os.path.join(self.pkg, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def check(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = R.main([self.pkg])
        return code, err.getvalue()

    def test_a_clean_package_passes(self):
        self.write("a.py", "import json\nimport urllib.parse\nfrom pathlib import Path\nimport os\nprint(os.environ.get('X'))\n")
        self.write("transport.py", "import socket\nimport urllib.request\nimport ssl\n")
        self.assertEqual(self.check()[0], 0)

    def test_network_imports_outside_the_transport_module_fail_with_file_and_line(self):
        imports = [
            "import socket", "import ssl", "import http.client", "from http import client", "import urllib.request",
            "from urllib import request", "from urllib.request import urlopen", "import urllib3", "import requests",
            "import httpx", "import aiohttp", "from socket import create_connection", "import smtplib", "import subprocess",
        ]
        for statement in imports:
            with self.subTest(statement):
                self.write("a.py", f"import json\n{statement}\n")
                code, err = self.check()
                self.assertEqual(code, 1)
                self.assertIn("a.py:2", err)

    def test_dynamic_imports_of_network_libraries_fail_too(self):
        for statement in ('__import__("socket")', 'importlib.import_module("ssl")', 'importlib.import_module("urllib.request")'):
            with self.subTest(statement):
                self.write("a.py", f"import importlib\n{statement}\n")
                code, err = self.check()
                self.assertEqual(code, 1)
                self.assertIn("a.py:2", err)

    def test_mentioning_the_env_file_or_dotenv_fails_in_any_file(self):
        samples = {
            "a.py": ['load_dotenv()', 'import dotenv', 'path = ".env"', '# key 放在 .env', 'x = ".env.local"', "# python-dotenv"],
            "prompt.txt": ["read the .env file"],
        }
        for name, texts in samples.items():
            for text in texts:
                with self.subTest(name, text=text):
                    self.write(name, f"x = 1\n{text}\n")
                    code, err = self.check()
                    self.assertEqual(code, 1)
                    self.assertIn(f"{name}:2", err)
                    os.remove(os.path.join(self.pkg, name))

    def test_environment_variable_access_is_not_a_dotenv_mention(self):
        self.write("a.py", "import os\nos.environ['X']\nenvironment = os.getenv('Y')\nthing.environment\n")
        self.assertEqual(self.check()[0], 0)

    def test_a_file_that_cannot_be_parsed_fails_instead_of_being_skipped(self):
        self.write("a.py", "def broken(:\n")
        code, err = self.check()
        self.assertEqual(code, 1)
        self.assertIn("a.py", err)

    def test_a_missing_package_directory_fails(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(R.main([os.path.join(self.pkg, "nope")]), 1)

    def test_the_real_package_passes(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = R.main([])
        self.assertEqual(code, 0, err.getvalue())


if __name__ == "__main__":
    unittest.main()

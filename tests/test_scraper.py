"""Offline QA: exercise failures without contacting the archive."""
import contextlib
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from scraper import downloader
from scraper import scraper
from scraper.storage import Storage


class ScraperQA(unittest.TestCase):
    def run_html(self, html, limit=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = io.StringIO()
        metadata = {"final_url": scraper.INDEX, "content_type": "text/html", "encoding": None}
        with patch("scraper.scraper.download", return_value=(html, metadata)), patch("scraper.scraper.time.sleep"), contextlib.redirect_stdout(output):
            result = scraper.scrape(temporary.name, limit)
        manifest = json.loads((Storage(temporary.name).path).read_text())
        return result, manifest, output.getvalue()

    def test_network_failure_recorded(self):
        with tempfile.TemporaryDirectory() as directory, patch("scraper.scraper.download", side_effect=URLError("offline")), patch("scraper.scraper.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scraper.scrape(directory), 1)
            record = Storage(directory).manifest["resources"][scraper.INDEX]
            self.assertEqual(record["status"], "failed")
            self.assertIn("offline", record["error"])
            log = (Storage(directory).root / "scraper.log").read_text()
            self.assertIn("ERROR", log)
            self.assertIn("offline", log)
            self.assertIn("Traceback", log)

    def test_transient_failure_gets_three_attempts(self):
        with patch("scraper.downloader.urlopen", side_effect=URLError("offline")) as request, patch("scraper.downloader.time.sleep"):
            with self.assertRaises(URLError):
                downloader.download(scraper.INDEX)
            self.assertEqual(request.call_count, 3)

    def test_404_not_retried(self):
        error = HTTPError(scraper.INDEX, 404, "missing", {}, None)
        with patch("scraper.downloader.urlopen", side_effect=error) as request:
            with self.assertRaises(HTTPError):
                downloader.download(scraper.INDEX)
            self.assertEqual(request.call_count, 1)

    def test_anchors_preserved_and_resource_deduplicated(self):
        _, manifest, _ = self.run_html(b'<a href="31/index.php#a">A</a><a href="31/index.php#b">B</a>', limit=1)
        self.assertEqual(len(manifest["resources"]), 1)
        self.assertNotIn(scraper.INDEX, manifest["resources"])
        self.assertEqual([r["anchor"] for r in manifest["references"]], ["a", "b"])

    def test_cache_corruption_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(directory)
            record = store.save_download("url", b"original", {})
            store.checkpoint()
            self.assertEqual(Storage(directory).cached("url")[0], b"original")
            (store.root / record["file"]).write_bytes(b"damaged")
            self.assertIsNone(store.cached("url"))

    # Expected failures document actual missing protections, not desired behaviour.
    def test_empty_index_is_rejected(self):
        failed, _, _ = self.run_html(b"")
        self.assertGreater(failed, 0)

    def test_error_page_returning_200_is_rejected(self):
        failed, _, _ = self.run_html(b"<html>Service unavailable</html>")
        self.assertGreater(failed, 0)

    def test_diagram_dependency_discovered(self):
        page = "https://www.turgor.ru/en/problems/31/index.php"
        def fetch(url):
            body = b'<a href="31/index.php#a">set</a>' if url == scraper.INDEX else b'<img src="diagram.png"><link rel="stylesheet" href="style.css">'
            return body, {"final_url": url, "content_type": "text/html", "encoding": "utf-8"}
        with tempfile.TemporaryDirectory() as directory, patch("scraper.scraper.download", side_effect=fetch), patch("scraper.scraper.time.sleep"), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(scraper.scrape(directory, limit=2), 0)
            manifest = Storage(directory).manifest
            self.assertIn(page.replace("index.php", "diagram.png"), manifest["resources"])
            self.assertIn(page.replace("index.php", "style.css"), manifest["resources"])

    def test_old_windows_1251_html_without_header(self):
        _, manifest, _ = self.run_html('<meta charset="windows-1251"><a href="31/index.php">Задачи</a>'.encode("cp1251"))
        self.assertEqual(manifest["references"][0]["label"], "Задачи")

    @unittest.expectedFailure
    def test_missing_section_anchor_detected(self):
        failed, _, _ = self.run_html(b'<a href="31/index.php#missing">set</a>')
        self.assertGreater(failed, 0)


if __name__ == "__main__":
    unittest.main()

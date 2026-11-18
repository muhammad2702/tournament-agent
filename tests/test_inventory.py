import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from tournament_agent.discovery import discover, discover_documents
from tournament_agent.fetcher import Fetcher
from tournament_agent.models import Download
from tournament_agent.pipeline import run


INDEX = "https://www.turgor.ru/en/problems/allproblems.php"
PAGE = "https://www.turgor.ru/en/problems/31/index.php"
HTML = """<a href="31/index.php">31</a>
<a href="31/index.php#turnir31otm">8-9</a>
<a href="31/index.php#turnir31ots">10-11</a>
<a href="31/index.php#turnir31oom">8-9</a>
<a href="31/index.php#turnir31oos">10-11</a>
<a href="31/questions.pdf">PDF</a><a href="/en/news/">News</a>
<a href="https://other.example/en/problems/31/index.php#x">Other site</a>"""


class Response:
    headers = {"Content-Type": "text/html; charset=utf-8"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self):
        return b"original bytes"

    def geturl(self):
        return PAGE


class InventoryTests(unittest.TestCase):
    def test_discovery_separates_page_and_fragment_and_excludes_other_links(self):
        refs = discover(HTML, INDEX)
        self.assertEqual(len(refs), 5)
        self.assertEqual({ref.page_url for ref in refs}, {PAGE})
        self.assertEqual([ref.anchor for ref in refs], ["", "turnir31otm", "turnir31ots", "turnir31oom", "turnir31oos"])
        self.assertEqual(refs[1].label, "8-9")
        docs = discover_documents(HTML, INDEX)
        self.assertEqual([d.url for d in docs], ["https://www.turgor.ru/en/problems/31/questions.pdf"])
        self.assertEqual(docs[0].source_page_url, INDEX)

    def test_pdf_is_saved_and_invalid_response_rejected(self):
        class PDFResponse(Response):
            def read(self):
                return b"%PDF-1.4\nexample"
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.urlopen", return_value=PDFResponse()) as request:
            fetcher = Fetcher(Path(tmp), pace=0)
            result = fetcher.fetch(PAGE + "/sample.pdf", kind="pdf")
            self.assertEqual(Path(result.path).suffix, ".pdf")
            self.assertTrue(fetcher.fetch(PAGE + "/sample.pdf", kind="pdf").from_cache)
            self.assertEqual(request.call_count, 1)
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.urlopen", return_value=Response()):
            with self.assertRaisesRegex(ValueError, "not a PDF"):
                Fetcher(Path(tmp), pace=0).fetch(PAGE + "/bad.pdf", kind="pdf")

    def test_cache_verifies_bytes_and_refetches_corruption(self):
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.urlopen", return_value=Response()) as request:
            fetcher = Fetcher(Path(tmp), pace=0)
            first = fetcher.fetch(PAGE)
            self.assertEqual(Path(first.path).suffix, ".html")
            self.assertEqual(Path(first.path).read_bytes(), b"original bytes")
            self.assertEqual(first.sha256, hashlib.sha256(b"original bytes").hexdigest())
            self.assertTrue(fetcher.fetch(PAGE).from_cache)
            self.assertEqual(request.call_count, 1)
            Path(first.path).write_bytes(b"corrupt")
            self.assertFalse(fetcher.fetch(PAGE).from_cache)
            self.assertEqual(request.call_count, 2)
            with self.assertRaises(ValueError):
                fetcher.fetch(PAGE + "#anchor")

    def test_legacy_cache_is_migrated_after_integrity_check(self):
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.urlopen", return_value=Response()) as request:
            fetcher = Fetcher(Path(tmp), pace=0)
            first = fetcher.fetch(PAGE)
            html = Path(first.path)
            legacy = html.with_suffix(".bin")
            html.rename(legacy)
            migrated = fetcher.fetch(PAGE)
            self.assertTrue(migrated.from_cache)
            self.assertTrue(html.exists())
            self.assertFalse(legacy.exists())
            self.assertEqual(request.call_count, 1)

    def test_retry_transient_failure_but_not_404(self):
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.time.sleep"), patch(
            "tournament_agent.fetcher.urlopen", side_effect=[HTTPError(PAGE, 503, "unavailable", {}, None), Response()]
        ) as request:
            Fetcher(Path(tmp), retries=1, pace=0).fetch(PAGE)
            self.assertEqual(request.call_count, 2)
        with TemporaryDirectory() as tmp, patch("tournament_agent.fetcher.urlopen", side_effect=HTTPError(PAGE, 404, "missing", {}, None)) as request:
            with self.assertRaises(HTTPError):
                Fetcher(Path(tmp), retries=2, pace=0).fetch(PAGE)
            self.assertEqual(request.call_count, 1)

    def test_pipeline_downloads_shared_page_once_and_saves_failure(self):
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            index_file = data_dir / "index.html"
            index_file.write_text(HTML, encoding="utf-8")
            index = Download(INDEX, INDEX, str(index_file), "hash", "text/html; charset=utf-8", False)
            with patch("tournament_agent.pipeline.Fetcher.fetch", side_effect=[index, OSError("offline"), OSError("offline pdf")]) as fetch:
                result = run(data_dir, pace=0)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(len(result["references"]), 5)
            self.assertEqual(len(result["pages"]), 1)
            self.assertEqual(result["pages"][0]["status"], "failed")
            self.assertEqual(result["documents"][0]["status"], "failed")
            saved = json.loads((data_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIn("offline", saved["pages"][0]["error"])

    def test_pipeline_records_pdf_references_from_index_and_page_once(self):
        with TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            index_file, page_file, pdf_file = (data_dir / name for name in ("index.html", "page.html", "file.pdf"))
            index_file.write_text('<a href="31/index.php#one">one</a><a href="31/file.pdf">PDF</a>', encoding="utf-8")
            page_file.write_text('<a href="file.pdf">same PDF</a>', encoding="utf-8")
            pdf_file.write_bytes(b"%PDF-1.4\nexample")
            pdf_url = "https://www.turgor.ru/en/problems/31/file.pdf"
            def fake_fetch(url, kind="html"):
                path = {INDEX: index_file, PAGE: page_file, pdf_url: pdf_file}[url]
                return Download(url, url, str(path), "hash", "text/html; charset=utf-8", False)
            with patch("tournament_agent.pipeline.Fetcher.fetch", side_effect=fake_fetch) as fetch:
                result = run(data_dir, pace=0)
            self.assertEqual(fetch.call_count, 3)
            self.assertEqual(len(result["document_references"]), 2)
            self.assertEqual({ref["source_page_url"] for ref in result["document_references"]}, {INDEX, PAGE})
            self.assertEqual(len(result["documents"]), 1)
            self.assertEqual(result["documents"][0]["status"], "downloaded")


if __name__ == "__main__":
    unittest.main()

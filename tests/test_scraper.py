"""Offline collection QA: exact pages, asset closure and visible failures."""
import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError, HTTPError

from scraper import scraper, downloader
from scraper.assets import Page, css_urls, srcset_urls
from scraper.storage import Storage

PAGE = 'https://www.turgor.ru/en/problems/31/index.php'
ROOT = 'https://www.turgor.ru/'


class ScraperQA(unittest.TestCase):
    def run_site(self, routes, pages=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        calls = []
        def fetch(url):
            calls.append(url)
            value = routes[url]
            if isinstance(value, Exception):
                raise value
            body, mime = value
            return body, dict(final_url=url, content_type=mime, encoding=None, http_status=200)
        with patch('scraper.scraper.download', side_effect=fetch), contextlib.redirect_stdout(io.StringIO()):
            result = scraper.scrape(temporary.name, pages)
        root = Path(temporary.name)
        return result, json.loads((root / 'manifest.json').read_text()), root, calls

    def site(self, body=b'<html><a name="a"></a></html>'):
        return {scraper.INDEX: (b'<html><a href="31/index.php#a">set</a></html>', 'text/html'), PAGE: (body, 'text/html')}

    def test_exact_page_and_recursive_assets_saved(self):
        routes = self.site(b'<html><a name="a"></a><img src="/image.png"><link rel="stylesheet" href="/style.css"></html>')
        routes.update({ROOT+'image.png': (b'\x89PNG\r\n\x1a\nimage', 'image/png'), ROOT+'style.css': (b'@import "more.css";body{background:url(background.gif)}', 'text/css'), ROOT+'more.css': (b'body{color:black}', 'text/css'), ROOT+'background.gif': (b'GIF89aimage', 'image/gif')})
        failed, manifest, root, calls = self.run_site(routes)
        self.assertEqual(failed, 0)
        self.assertEqual(manifest['summary']['assets'], 4)
        self.assertEqual(manifest['summary']['pages'], 1)
        self.assertNotIn(scraper.INDEX, manifest['resources'])
        for url, record in manifest['resources'].items():
            self.assertEqual((root / record['file']).read_bytes(), routes[url][0])
        self.assertEqual(len(calls), len(set(calls)))

    def test_whole_page_saved_without_interpreting_sections(self):
        body = '<html><table><tr><td>Задача x²</td></tr></table><pre>a^3</pre></html>'.encode('utf-8')
        failed, manifest, root, _ = self.run_site(self.site(body))
        self.assertEqual(failed, 0)
        self.assertTrue(manifest['summary']['complete'])
        self.assertEqual((root / manifest['resources'][PAGE]['file']).read_bytes(), body)

    def test_duplicate_section_names_do_not_affect_downloads(self):
        failed, manifest, _, _ = self.run_site(self.site(b'<html><a name="a"></a><a name="a"></a></html>'))
        self.assertEqual(failed, 0)
        self.assertTrue(manifest['summary']['complete'])

    def test_missing_asset_fails_and_logs(self):
        routes = self.site(b'<html><a name="a"></a><img src="/missing.png"></html>')
        routes[ROOT+'missing.png'] = URLError('missing image')
        failed, manifest, root, _ = self.run_site(routes)
        self.assertEqual(failed, 1)
        self.assertEqual(manifest['resources'][ROOT+'missing.png']['status'], 'failed')
        self.assertIn('Traceback', (root/'scraper.log').read_text())
        self.assertIn('missing image', (root/'scraper.log').read_text())

    def test_asset_html_error_rejected(self):
        routes = self.site(b'<html><a name="a"></a><img src="/image.png"></html>')
        routes[ROOT+'image.png'] = (b'<html>Error</html>', 'text/html')
        failed, manifest, _, _ = self.run_site(routes)
        self.assertEqual(failed, 1)
        self.assertNotIn('file', manifest['resources'][ROOT+'image.png'])

    def test_non_html_response_rejected(self):
        routes = self.site()
        routes[PAGE] = (b'other document', 'application/octet-stream')
        self.assertEqual(self.run_site(routes)[0], 1)

    def test_index_error_page_rejected(self):
        self.assertEqual(self.run_site({scraper.INDEX: (b'<html>Service unavailable</html>', 'text/html')})[0], 1)

    def test_empty_page_rejected(self):
        self.assertEqual(self.run_site(self.site(b''))[0], 1)

    def test_unlisted_page_rejected(self):
        failed, _, _, calls = self.run_site(self.site(), ['https://www.turgor.ru/en/problems/48/index.php'])
        self.assertEqual(failed, 1)
        self.assertEqual(calls, [scraper.INDEX])

    def test_document_and_landing_links_never_requested(self):
        routes = self.site()
        routes[scraper.INDEX] = (b'<html><a href="31/index.php#a">set</a><a href="48/index.php">landing</a><a href="31/document.pdf#x">document</a></html>', 'text/html')
        _, _, _, calls = self.run_site(routes)
        self.assertEqual(calls, [scraper.INDEX, PAGE])

    def test_encoding(self):
        text = '<html><a name="a"></a>Задачи</html>'
        self.assertEqual(scraper.decode_html(('<meta charset="windows-1251">'+text).encode('cp1251'), None), '<meta charset="windows-1251">'+text)

    def test_corrupt_cache_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = Storage(root)
            record = store.save_download(PAGE, b'original', {})
            store.checkpoint()
            self.assertEqual(Storage(root).cached(PAGE)[0], b'original')
            (store.root / record['file']).write_bytes(b'changed')
            self.assertIsNone(store.cached(PAGE))

    def test_asset_syntax(self):
        p = Page()
        p.feed('<html><base href="/assets/"><img srcset="one.png 1x, two.png 2x"><div style="background:url(three.png)"></div><style>@import "four.css";</style></html>')
        self.assertEqual(set(p.assets), {'one.png', 'two.png', 'three.png', 'four.css'})
        self.assertEqual(p.base, '/assets/')
        self.assertEqual(css_urls('@import url("a.css");x{background:url(b.png)}'), ['a.css', 'b.png'])

    def test_embedded_srcset_is_not_split_into_fake_downloads(self):
        self.assertEqual(srcset_urls('data:image/png;base64,AAAA 1x, two.png 2x'), ['data:image/png;base64,AAAA', 'two.png'])

    def test_gzip_transfer_decoded(self):
        import gzip
        from email.message import Message
        headers = Message()
        headers['Content-Type'] = 'text/css; charset=utf-8'
        headers['Content-Encoding'] = 'gzip'
        response = unittest.mock.MagicMock()
        response.__enter__.return_value = response
        response.headers = headers
        response.status = 200
        response.geturl.return_value = ROOT + 'style.css'
        response.read.return_value = gzip.compress(b'body{color:black}')
        with patch('scraper.downloader.urlopen', return_value=response):
            body, metadata = downloader.download(ROOT + 'style.css')
        self.assertEqual(body, b'body{color:black}')
        self.assertEqual(metadata['content_type'], 'text/css')

    def test_retry_and_404(self):
        with patch('scraper.downloader.urlopen', side_effect=URLError('offline')) as request, patch('scraper.downloader.time.sleep'):
            with self.assertRaises(URLError):
                downloader.download(PAGE)
            self.assertEqual(request.call_count, 3)
        with patch('scraper.downloader.urlopen', side_effect=HTTPError(PAGE, 404, 'missing', {}, None)) as request:
            with self.assertRaises(HTTPError):
                downloader.download(PAGE)
            self.assertEqual(request.call_count, 1)


if __name__ == '__main__':
    unittest.main()

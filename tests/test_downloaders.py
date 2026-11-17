import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError, HTTPError
from pypdf import PdfWriter
from scraper import downloader
from scraper.discovery import INDEX, discover, canonical, language_hint, classify
from scraper.assets import css_urls, html_assets, srcset_urls
from scraper.html_downloader import save_pages
from scraper.pdf_downloader import save_pdfs
from scraper.scraper import run
from scraper.storage import Storage

PAGE='https://www.turgor.ru/en/problems/31/index.php'
ROOT='https://www.turgor.ru/'


def document():
    buffer=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=100,height=100);writer.write(buffer)
    return buffer.getvalue()


class DownloadQA(unittest.TestCase):
    def fetcher(self,routes,calls):
        def fetch(url):
            calls.append(url)
            value=routes[url]
            if isinstance(value,Exception):raise value
            content,mime=value
            return content,{'final_url':url,'content_type':mime,'encoding':None,'http_status':200}
        return fetch

    def test_discovery_preserves_fragments_and_queries(self):
        rows=discover(b'<a href="31/index.php?x=1#a">8-9</a><a href="31/index.php?x=1#b">10-11</a>',INDEX)
        self.assertNotEqual(rows[0]['target_url'],rows[1]['target_url'])
        self.assertEqual(rows[0]['download_url'],rows[1]['download_url'])
        self.assertIn('?x=1',rows[0]['download_url'])

    def test_en_path_does_not_mean_english(self):
        self.assertEqual(language_hint(PAGE,'Задачи')[0],'non_english')
        self.assertEqual(language_hint(ROOT+'en/problems/31/os31sl.pdf','PDF')[0],'uncertain')
        self.assertEqual(language_hint(ROOT+'problems/48/fall-eng-auth.pdf','Задачи')[0],'english')
        self.assertEqual(language_hint(ROOT+'problems/45/marking_scheme_A.pdf','Критерии')[0],'uncertain')

    def test_results_and_solutions_excluded_but_advanced_retained(self):
        self.assertEqual(classify(ROOT+'problems/33/res_mos.php')[0],'excluded')
        self.assertEqual(classify(ROOT+'problems/27/turgor_sol_27o_osn.php')[0],'excluded')
        self.assertEqual(classify(ROOT+'problems/36/os-36-sl-avt.pdf')[0],'pdf')

    def test_html_exact_bytes_and_css_dependency_closure(self):
        body='<html><pre>Задача x²\r\na^3</pre><img src="/a.png"><link rel="stylesheet" href="/a.css"></html>'.encode()
        routes={PAGE:(body,'text/html'),ROOT+'a.png':(b'\x89PNG\r\n\x1a\nimage','image/png'),ROOT+'a.css':(b'@import "b.css";x{background:url(b.gif)}','text/css'),ROOT+'b.css':(b'x{color:black}','text/css'),ROOT+'b.gif':(b'GIF89aimage','image/gif')};calls=[]
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,calls)):
            m=save_pages([PAGE,PAGE],directory)
            self.assertTrue(m['summary']['complete'])
            self.assertEqual(m['summary']['assets'],4)
            for url,r in m['resources'].items():self.assertEqual((Path(directory)/r['file']).read_bytes(),routes[url][0])
            calls.clear();save_pages([PAGE],directory)
            self.assertEqual(calls,[])

    def test_html_module_does_not_follow_document_links(self):
        calls=[];routes={PAGE:(b'<a href="os31sl.pdf">document</a>','text/html')}
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,calls)):
            self.assertTrue(save_pages([PAGE],directory)['summary']['complete'])
            self.assertEqual(calls,[PAGE])

    def test_html_structure_not_required(self):
        calls=[];body=b'<p>simple fragment</p>'
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher({PAGE:(body,'text/html')},calls)):
            m=save_pages([PAGE],directory)
            self.assertTrue(m['summary']['complete'])
            self.assertEqual((Path(directory)/m['resources'][PAGE]['file']).read_bytes(),body)

    def test_missing_image_recorded_and_logged(self):
        routes={PAGE:(b'<img src="/missing.png">','text/html'),ROOT+'missing.png':URLError('missing image')}
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,[])):
            m=save_pages([PAGE],directory)
            self.assertFalse(m['summary']['complete'])
            self.assertEqual(m['resources'][ROOT+'missing.png']['status'],'failed')
            self.assertIn('Traceback',(Path(directory)/'download.log').read_text())

    def test_resume_finishes_old_pending_dependencies(self):
        image=ROOT+'old.png';routes={PAGE:(b'<p>page</p>','text/html'),image:(b'\x89PNG\r\n\x1a\nimage','image/png')}
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,[])):
            store=Storage(directory)
            store.manifest['references'].append({'source_url':PAGE,'target_url':image,'status':'pending'})
            store.checkpoint()
            m=save_pages([PAGE],directory)
            self.assertTrue(m['summary']['complete'])
            self.assertEqual(m['resources'][image]['status'],'downloaded')
            self.assertEqual(m['summary']['pending_dependencies'],0)

    def test_wrong_html_type_is_failure(self):
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher({PAGE:(b'data','application/octet-stream')},[])):
            self.assertFalse(save_pages([PAGE],directory)['summary']['complete'])

    def test_asset_error_page_is_failure(self):
        routes={PAGE:(b'<img src="/a.png">','text/html'),ROOT+'a.png':(b'<html>error</html>','text/html')}
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,[])):
            self.assertFalse(save_pages([PAGE],directory)['summary']['complete'])

    def test_pdf_saves_unchanged_and_opens(self):
        url=ROOT+'problems/31/set.pdf';body=document()
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher({url:(body,'application/pdf')},[])):
            m=save_pdfs([url],directory)
            self.assertTrue(m['summary']['complete'])
            self.assertEqual(m['resources'][url]['page_count'],1)
            self.assertEqual((Path(directory)/m['resources'][url]['file']).read_bytes(),body)

    def test_pdf_error_page_and_truncation_fail(self):
        for body in [b'<html>error</html>',b'%PDF-1.4\ntruncated']:
            with self.subTest(body=body),tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher({ROOT+'bad.pdf':(body,'application/pdf')},[])):
                self.assertEqual(save_pdfs([ROOT+'bad.pdf'],directory)['summary']['failures'],1)

    def test_corrupt_cache_is_not_used(self):
        with tempfile.TemporaryDirectory() as directory:
            s=Storage(directory);r=s.save_download(PAGE,b'original',{});s.checkpoint()
            (s.root/r['file']).write_bytes(b'changed')
            self.assertIsNone(s.cached(PAGE))

    def test_restore_uses_hash_and_refuses_changed_source(self):
        import hashlib
        from tests.restore_downloads import restore
        body=b'original snapshot';record={'size_bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'snapshot'
            with patch('tests.restore_downloads.download',return_value=(body,{})):
                self.assertEqual(restore(PAGE,path,record)[0],'restored')
                self.assertEqual(restore(PAGE,path,record)[0],'present')
            path.unlink()
            with patch('tests.restore_downloads.download',return_value=(b'changed',{})):
                with self.assertRaises(ValueError):restore(PAGE,path,record)
            self.assertFalse(path.exists())

    def test_assets(self):
        urls,base=html_assets(b'<base href="/assets/"><img srcset="one.png 1x, two.png 2x"><div style="background:url(three.png)"></div><style>@import "four.css";</style>')
        self.assertEqual(set(urls),{'one.png','two.png','three.png','four.css'})
        self.assertEqual(base,'/assets/')
        self.assertEqual(srcset_urls('data:image/png;base64,AAAA 1x, two.png 2x'),['data:image/png;base64,AAAA','two.png'])
        self.assertEqual(css_urls('@import url("a.css");x{background:url(b.png)}'),['a.css','b.png'])

    def test_transient_retries_and_404(self):
        with patch('scraper.downloader.urlopen',side_effect=URLError('offline')) as request,patch('scraper.downloader.time.sleep'):
            with self.assertRaises(URLError):downloader.download(PAGE)
            self.assertEqual(request.call_count,3)
        with patch('scraper.downloader.urlopen',side_effect=HTTPError(PAGE,404,'missing',{},None)) as request:
            with self.assertRaises(HTTPError):downloader.download(PAGE)
            self.assertEqual(request.call_count,1)

    def test_orchestrator_discovers_indirect_documents_and_separates_outputs(self):
        pdf=ROOT+'en/problems/31/os31sl.pdf';english=ROOT+'problems/48/set-eng.pdf'
        routes={INDEX:(('<a href="31/index.php#a">Задачи</a><a href="'+english+'">English</a>').encode(),'text/html'),PAGE:(b'<pre>whole source</pre><a href="os31sl.pdf">document</a>','text/html'),pdf:(document(),'application/pdf')};calls=[]
        with tempfile.TemporaryDirectory() as directory,patch('scraper.downloader.download',side_effect=self.fetcher(routes,calls)):
            self.assertEqual(run(directory),0)
            root=Path(directory)
            self.assertEqual(json.loads((root/'html/manifest.json').read_text())['summary']['html'],1)
            self.assertEqual(json.loads((root/'pdf/manifest.json').read_text())['summary']['pdfs'],1)
            self.assertNotIn(english,calls)
            inventory=json.loads((root/'inventory.json').read_text())
            self.assertEqual(inventory['resources'][pdf]['language_hint'],'uncertain')
            self.assertEqual(inventory['resources'][english]['status'],'excluded')


if __name__=='__main__':unittest.main()

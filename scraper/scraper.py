"""Inventory the supplied archive, then run independent HTML/PDF downloaders."""
import argparse
import json
import logging
from pathlib import Path
from . import downloader
from .discovery import INDEX, discover
from .html_downloader import save_pages
from .pdf_downloader import save_pdfs
from .storage import Storage

logger = logging.getLogger(__name__)


def write_json(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


class Issues(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.items = []

    def emit(self, record):
        self.items.append({'level': record.levelname, 'logger': record.name, 'message': record.getMessage()})


def run(directory):
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    issues = Issues()
    log = logging.getLogger()
    log.addHandler(issues)
    try:
        with downloader.log_to(root / 'run.log'):
            inventory = {'start_url': INDEX, 'resources': {}, 'references': []}
            discovery_store = Storage(root / 'discovery')
            indexes, pages, visited = {INDEX}, set(), set()
            html_manifest = {'resources': {}, 'references': [], 'summary': {}}

            def register(content, source, encoding):
                for ref in discover(content, source, encoding):
                    if ref not in inventory['references']:
                        inventory['references'].append(ref)
                    if ref['kind'] == 'excluded': continue
                    url = ref['download_url']
                    resource = inventory['resources'].setdefault(url, {'kind': ref['kind'], 'language_hint': ref['language_hint'], 'language_evidence': ref['language_evidence']})
                    if ref['language_hint'] == 'non_english' and resource['language_hint'] == 'uncertain':
                        resource.update(language_hint=ref['language_hint'],language_evidence=ref['language_evidence'])
                    if resource['kind'] == 'index' and url not in visited: indexes.add(url)
                    if resource['kind'] == 'html' and url not in visited: pages.add(url)

            while indexes or pages:
                current = indexes - visited
                indexes = set()
                for url,result in downloader.fetch_many(current,discovery_store):
                    visited.add(url)
                    try:
                        if isinstance(result,Exception):raise result
                        content,metadata=result
                        if metadata['content_type'] not in {'text/html','application/xhtml+xml'}:raise ValueError('Index is not HTML')
                        register(content,url,metadata.get('encoding'))
                        if not discovery_store.cached(url):discovery_store.save_download(url,content,metadata)
                        logger.info('Inventoried index %s',url)
                    except Exception as error:
                        discovery_store.manifest['resources'].setdefault(url,{}).update(status='failed',error=str(error))
                        logger.exception('Index failed %s',url)
                    discovery_store.checkpoint()
                current = pages - visited
                pages = set()
                if current:
                    html_manifest = save_pages(current,root / 'html')
                    store = Storage(root / 'html')
                    for url in sorted(current):
                        visited.add(url)
                        cached = store.cached(url)
                        if cached:
                            body,metadata=cached
                            register(body,url,metadata.get('encoding'))
                    print('HTML inventory progress:', html_manifest['summary'], flush=True)
                write_json(root/'inventory.json',inventory)
            pdf_urls=[]
            for url,r in inventory['resources'].items():
                if r['kind'] != 'pdf':continue
                if r['language_hint']=='english':
                    r.update(status='excluded',reason=r['language_evidence'])
                else:
                    r['status']='selected'
                    pdf_urls.append(url)
            print('PDF candidates selected:',len(pdf_urls),flush=True)
            pdf_manifest=save_pdfs(pdf_urls,root/'pdf')
            for kind,manifest in [('index',discovery_store.manifest),('html',html_manifest),('pdf',pdf_manifest)]:
                for url,r in inventory['resources'].items():
                    if r['kind']==kind and r.get('status')!='excluded':
                        saved=manifest['resources'].get(url,{})
                        r['status']=saved.get('status','missing')
                        if saved.get('error'):r['error']=saved['error']
            failures=[]
            for phase,manifest in [('discovery',discovery_store.manifest),('html',html_manifest),('pdf',pdf_manifest)]:
                for url,r in manifest['resources'].items():
                    if r['status']!='downloaded':failures.append({'phase':phase,'url':url,'status':r['status'],'error':r.get('error')})
                for ref in manifest.get('references',[]):
                    if ref.get('status') in {'failed','missing','pending'}:failures.append(dict(ref,phase=phase))
            uncertain=[url for url,r in inventory['resources'].items() if r['kind'] in {'html','pdf'} and r['language_hint']=='uncertain' and r.get('status')=='downloaded']
            inventory['summary']={'html':html_manifest.get('summary',{}),'pdf':pdf_manifest['summary'],'english_pdfs_excluded':sum(r['kind']=='pdf' and r.get('status')=='excluded' for r in inventory['resources'].values()),'language_uncertain_retained':len(uncertain),'failures':len(failures),'complete':not failures}
            write_json(root/'inventory.json',inventory)
            report={'failures':failures,'warnings_and_retries':issues.items,'language_uncertain_retained':uncertain}
            probe=root/'discovery'/'probe_errors.json'
            if probe.exists():report['initial_probe_errors']=json.loads(probe.read_text())
            write_json(root/'errors.json',report)
            print(json.dumps(inventory['summary'],ensure_ascii=False),flush=True)
            return len(failures)
    finally:
        log.removeHandler(issues)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',default='tests/artifacts/downloads')
    args=parser.parse_args()
    raise SystemExit(bool(run(args.output)))

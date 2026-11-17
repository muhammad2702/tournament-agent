"""Save whole problem webpages and their static assets. No section extraction."""
import argparse
import logging
import re
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin, urlsplit, urldefrag

from .assets import Page, css_urls, resolve, asset_policy
from .downloader import download
from .storage import Storage

INDEX = 'https://www.turgor.ru/en/problems/allproblems.php'
logger = logging.getLogger(__name__)


def decode_html(content, encoding):
    if not encoding:
        match = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', content[:4096], re.I)
        encoding = match.group(1).decode('ascii') if match else 'utf-8'
    return content.decode(encoding)


def problem_reference(url):
    parts = urlsplit(url)
    match = re.fullmatch(r'/(?:en/)?problems/(\d+)/index\.php', parts.path)
    # Section links identify problem-containing pages; the first tournament
    # has inline questions without section links. Fragments never affect downloads.
    return bool(parts.hostname in {'www.turgor.ru', 'turgor.ru'} and parts.scheme in {'http', 'https'}
                and match and (parts.fragment or int(match.group(1)) == 1))


def inspect(content, metadata, kind):
    if not content.strip():
        raise ValueError('Empty response')
    mime = metadata['content_type']
    if kind == 'page':
        if mime not in {'text/html', 'application/xhtml+xml'}:
            raise ValueError('Expected an HTML webpage')
        page = Page()
        page.feed(decode_html(content, metadata.get('encoding')))
        if not page.has_html:
            raise ValueError('Response is not a full HTML document')
        return page.assets, urljoin(metadata['final_url'], page.base or '')
    if mime in {'text/html', 'application/xhtml+xml'} or content.lstrip().lower().startswith((b'<!doctype html', b'<html')):
        raise ValueError('Asset returned HTML instead of its file content')
    if not (mime.startswith(('image/', 'font/')) or mime in {'text/css', 'text/javascript', 'application/javascript', 'application/x-javascript', 'application/octet-stream', 'application/font-woff', 'application/vnd.ms-fontobject', 'application/x-font-ttf'}):
        raise ValueError('Unexpected asset content type: ' + mime)
    if mime == 'text/css':
        return css_urls(decode_html(content, metadata.get('encoding'))), metadata['final_url']
    return [], metadata['final_url']


def scrape(directory='data', pages=None):
    store = Storage(directory)
    handler = logging.FileHandler(store.root / 'scraper.log', encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
    log = logging.getLogger()
    level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        with ThreadPoolExecutor(max_workers=4) as workers:
            return collect(store, pages, workers)
    except Exception as error:
        logger.exception('Collection stopped')
        store.manifest['error'] = str(error)
        store.checkpoint()
        return 1
    finally:
        log.removeHandler(handler)
        handler.close()
        log.setLevel(level)


def collect(store, pages, workers):
    store.manifest.pop('error', None)
    body, metadata = download(INDEX)
    inspect(body, metadata, 'page')
    index = Page()
    index.feed(decode_html(body, metadata.get('encoding')))
    selected = {urldefrag(urljoin(metadata['final_url'], link['href']))[0]
                for link in index.links if problem_reference(urljoin(metadata['final_url'], link['href']))}
    if not selected or (pages and not set(pages) <= selected):
        raise ValueError('No matching problem webpages in the supplied index')
    selected = set(pages) if pages else selected
    store.manifest['selection'] = {'index_url': INDEX, 'page_urls': sorted(selected)}
    store.manifest['references'] = []
    queue = deque((url, 'page') for url in sorted(selected))
    requests, seen = {}, set()

    def schedule(url):
        if url not in requests and not store.cached(url):
            requests[url] = workers.submit(download, url)

    for url, _ in queue:
        schedule(url)
    failures = 0
    while queue:
        url, kind = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        try:
            cached = store.cached(url)
            content, record = cached or requests[url].result()
            logger.info('%s %s', 'Cache hit' if cached else 'Downloaded', url)
            if kind == 'page' and urlsplit(record['final_url']).path != urlsplit(url).path:
                raise ValueError('Webpage redirected to a different path')
            if kind == 'asset' and asset_policy(record['final_url']):
                raise ValueError('Asset redirected outside static-asset scope')
            dependencies, base = inspect(content, record, kind)
            if not cached:
                record = store.save_download(url, content, record)
            record = {key: record[key] for key in ('final_url', 'content_type', 'encoding', 'http_status', 'transfer_encoding', 'file', 'sha256', 'size_bytes') if key in record}
            record.update(kind=kind, status='downloaded')
            store.manifest['resources'][url] = record
            for dependency in dependencies:
                if dependency.startswith('#'):
                    continue
                target = resolve(base, dependency)
                reason = asset_policy(target)
                reference = {'source_url': url, 'target_url': target}
                if reason:
                    reference.update(status='excluded', reason=reason)
                    if reason != 'embedded' and not reason.startswith('tracking'):
                        failures += 1
                        reference['status'] = 'failed'
                        logger.error('Unresolved asset %s: %s', target, reason)
                else:
                    queue.append((target, 'asset'))
                    schedule(target)
                if reference not in store.manifest['references']:
                    store.manifest['references'].append(reference)
            print('saved', kind, url, flush=True)
        except Exception as error:
            failures += 1
            store.manifest['resources'].setdefault(url, {}).update(kind=kind, status='failed', error=str(error))
            logger.exception('Failed %s', url)
            print('failed', url, str(error), flush=True)
        store.checkpoint()
    for url in seen:
        if store.manifest['resources'][url]['status'] == 'downloaded' and not store.cached(url):
            failures += 1
            store.manifest['resources'][url].update(status='failed', error='Saved-file hash check failed')
    for ref in store.manifest['references']:
        if 'status' not in ref:
            ref['status'] = store.manifest['resources'].get(ref['target_url'], {}).get('status', 'missing')
    store.manifest['summary'] = {'pages': sum(store.manifest['resources'][url].get('kind') == 'page' and store.manifest['resources'][url]['status'] == 'downloaded' for url in seen), 'assets': sum(store.manifest['resources'][url].get('kind') == 'asset' and store.manifest['resources'][url]['status'] == 'downloaded' for url in seen), 'failures': failures, 'complete': failures == 0}
    store.checkpoint()
    logger.info('Summary %s', store.manifest['summary'])
    print(store.manifest['summary'], flush=True)
    return failures


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--page', action='append', help='Collect one exact index-listed problem webpage and all its static assets')
    args = parser.parse_args()
    raise SystemExit(bool(scrape(args.data_dir, args.page)))

"""Save whole HTML responses and their static dependencies. No PDF handling."""
import logging
from urllib.parse import urljoin
from . import downloader
from .assets import html_assets, css_urls, resolve, asset_policy
from .storage import Storage

logger = logging.getLogger(__name__)
HTML_TYPES = {'text/html', 'application/xhtml+xml'}


def save_pages(urls, directory):
    store = Storage(directory)
    with downloader.log_to(store.root / 'download.log'):
        pages = set(urls)
        pending = set(urls) | {ref['target_url'] for ref in store.manifest['references']
                              if ref.get('status') in {'pending','missing','failed'}
                              and asset_policy(ref['target_url']) is None}
        seen = set()
        while pending:
            next_urls = set()
            for url, result in downloader.fetch_many(pending, store):
                seen.add(url)
                kind = 'html' if url in pages else 'asset'
                try:
                    if isinstance(result, Exception): raise result
                    content, metadata = result
                    if not content.strip(): raise ValueError('Empty response')
                    mime = metadata['content_type']
                    if kind == 'html':
                        if mime not in HTML_TYPES: raise ValueError('Expected HTML, received ' + mime)
                        dependencies, base = html_assets(content, metadata.get('encoding'))
                        base = urljoin(metadata['final_url'], base)
                    else:
                        if mime in HTML_TYPES or content.lstrip().lower().startswith((b'<!doctype html', b'<html')):
                            raise ValueError('Asset returned an HTML error page')
                        if not (mime.startswith(('image/', 'font/')) or mime in {'text/css', 'text/javascript', 'application/javascript', 'application/x-javascript', 'application/octet-stream', 'application/font-woff', 'application/vnd.ms-fontobject'}):
                            raise ValueError('Unexpected asset type: ' + mime)
                        dependencies = css_urls(content.decode(metadata.get('encoding') or 'utf-8')) if mime == 'text/css' else []
                        base = metadata['final_url']
                    if not store.cached(url): record = store.save_download(url, content, metadata)
                    else: record = store.manifest['resources'][url]
                    record.update(kind=kind, status='downloaded')
                    record.pop('error', None)
                    for dependency in dependencies:
                        if dependency.startswith('#'): continue
                        target = resolve(base, dependency)
                        reason = asset_policy(target)
                        ref = {'source_url': url, 'target_url': target}
                        if reason:
                            ref.update(status='excluded' if reason == 'embedded' or reason.startswith('tracking') else 'failed', reason=reason)
                        else:
                            ref['status'] = 'pending'
                            next_urls.add(target)
                        existing = next((r for r in store.manifest['references'] if r['source_url'] == url and r['target_url'] == target), None)
                        if existing: existing.update(ref)
                        else: store.manifest['references'].append(ref)
                    logger.info('Saved %s bytes=%s %s', kind, len(content), url)
                except Exception as error:
                    store.manifest['resources'].setdefault(url, {}).update(kind=kind, status='failed', error=str(error))
                    logger.exception('Failed %s', url)
                store.checkpoint()
            pending = next_urls - seen
        for ref in store.manifest['references']:
            if ref['status'] == 'pending':
                ref['status'] = store.manifest['resources'].get(ref['target_url'], {}).get('status', 'missing')
        failed = [url for url,r in store.manifest['resources'].items() if r['status'] == 'failed']
        failed_refs = [r for r in store.manifest['references'] if r['status'] in {'failed','missing','pending'}]
        store.manifest['summary'] = {'html':sum(r.get('kind') == 'html' and r['status'] == 'downloaded' for r in store.manifest['resources'].values()),'assets':sum(r.get('kind') == 'asset' and r['status'] == 'downloaded' for r in store.manifest['resources'].values()),'failed_resources':len(failed),'failed_dependencies':sum(r['status']=='failed' for r in failed_refs),'pending_dependencies':sum(r['status'] in {'pending','missing'} for r in failed_refs),'complete':not failed and not failed_refs}
        store.checkpoint()
        logger.info('Summary %s',store.manifest['summary'])
    return store.manifest

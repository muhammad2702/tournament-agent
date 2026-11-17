"""Save original document bytes and check that every document opens."""
import io
import logging
from pypdf import PdfReader
from . import downloader
from .storage import Storage

logger = logging.getLogger(__name__)


def check_document(content):
    if not content.startswith(b'%PDF-'): raise ValueError('Invalid PDF signature')
    reader = PdfReader(io.BytesIO(content))
    if reader.is_encrypted and not reader.decrypt(''): raise ValueError('Password-protected PDF')
    count = len(reader.pages)
    if count == 0: raise ValueError('PDF has no pages')
    return count


def save_pdfs(urls, directory):
    store = Storage(directory)
    with downloader.log_to(store.root / 'download.log'):
        for url, result in downloader.fetch_many(urls, store):
            try:
                if isinstance(result, Exception): raise result
                content, metadata = result
                count = check_document(content)
                if not store.cached(url): record = store.save_download(url, content, metadata)
                else: record = store.manifest['resources'][url]
                record.update(kind='pdf',status='downloaded',page_count=count)
                record.pop('error',None)
                logger.info('Saved pages=%s bytes=%s %s',count,len(content),url)
            except Exception as error:
                store.manifest['resources'].setdefault(url,{}).update(kind='pdf',status='failed',error=str(error))
                logger.exception('Failed %s',url)
            store.checkpoint()
        records=store.manifest['resources'].values()
        store.manifest['summary']={'pdfs':sum(r['status']=='downloaded' for r in records),'failures':sum(r['status']=='failed' for r in records)}
        store.manifest['summary']['complete']=store.manifest['summary']['failures']==0
        store.checkpoint()
        logger.info('Summary %s',store.manifest['summary'])
    return store.manifest

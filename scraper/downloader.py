"""HTTP only: return original bytes and response metadata."""

import time
import logging
import gzip
import zlib
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def download(url):
    for attempt in range(3):
        logger.info("Request attempt=%s url=%s", attempt + 1, url)
        try:
            request = Request(url, headers={"User-Agent": "TournamentAgent/0.1"})
            with urlopen(request, timeout=30) as response:
                content = response.read()
                transfer_encoding = response.headers.get("Content-Encoding", "").lower()
                if transfer_encoding == "gzip" or content.startswith(b"\x1f\x8b"):
                    content = gzip.decompress(content)
                elif transfer_encoding == "deflate":
                    content = zlib.decompress(content)
                return content, {
                    "final_url": response.geturl(),
                    "content_type": response.headers.get_content_type(),
                    "encoding": response.headers.get_content_charset(),
                    "http_status": response.status,
                    "transfer_encoding": transfer_encoding,
                }
        except HTTPError as error:
            if error.code != 429 and error.code < 500:
                raise
            if attempt == 2:
                raise
            last_error = error
        except (URLError, TimeoutError, ConnectionError, IncompleteRead) as error:
            if attempt == 2:
                raise
            last_error = error
        logger.warning("Retrying url=%s after attempt=%s cause=%s", url, attempt + 1, last_error)
        retry_after = last_error.headers.get('Retry-After','') if isinstance(last_error,HTTPError) and last_error.headers else ''
        time.sleep(min(30,int(retry_after)) if retry_after.isdigit() else 2 ** attempt)


from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


@contextmanager
def log_to(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(path, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s %(message)s'))
    root = logging.getLogger()
    previous = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    try:
        yield
    finally:
        root.removeHandler(handler)
        handler.close()
        root.setLevel(previous)


def fetch_many(urls, store):
    def fetch(url):
        cached = store.cached(url)
        if cached:
            logger.info('Cache hit %s', url)
            return cached
        return download(url)
    with ThreadPoolExecutor(max_workers=4) as workers:
        jobs = {url: workers.submit(fetch, url) for url in sorted(set(urls))}
        for url, job in jobs.items():
            try:
                yield url, job.result()
            except Exception as error:
                yield url, error

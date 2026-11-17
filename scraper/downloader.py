"""HTTP only: return original bytes and response metadata."""

import time
import logging
import gzip
import zlib
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
        except (URLError, TimeoutError):
            if attempt == 2:
                raise
        logger.warning("Retrying url=%s after attempt=%s", url, attempt + 1)
        time.sleep(2 ** attempt)

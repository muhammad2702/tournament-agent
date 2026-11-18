"""Fetch and cache bytes. This is the only component that accesses the network."""

import hashlib
import json
import os
from pathlib import Path
from threading import Lock
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag
from urllib.request import Request, urlopen

from .models import Download


class Fetcher:
    def __init__(self, raw_dir: Path, timeout: float = 30, retries: int = 2,
                 pace: float = 0.5, refresh: bool = False):
        if timeout <= 0 or retries < 0 or pace < 0:
            raise ValueError("timeout must be positive; retries and pace must be nonnegative")
        self.raw_dir = Path(raw_dir)
        self.timeout = timeout
        self.retries = retries
        self.pace = pace
        self.refresh = refresh
        self._last_request = None
        self._pace_lock = Lock()

    def fetch(self, url: str, kind: str = "html") -> Download:
        if kind not in ("html", "pdf"):
            raise ValueError("kind must be html or pdf")
        page_url, fragment = urldefrag(url)
        if fragment:
            raise ValueError("fetch expects a URL without a fragment")
        key = hashlib.sha256(page_url.encode("utf-8")).hexdigest()
        path = self.raw_dir / (key + "." + kind)
        legacy_path = self.raw_dir / (key + ".bin")
        metadata_path = self.raw_dir / (key + ".json")
        cached_path = path if path.exists() or kind != "html" else legacy_path
        if not self.refresh and cached_path.exists() and metadata_path.exists():
            try:
                meta = json.loads(metadata_path.read_text(encoding="utf-8"))
                digest = hashlib.sha256(cached_path.read_bytes()).hexdigest()
                if meta["url"] == page_url and meta["sha256"] == digest and (kind != "pdf" or cached_path.read_bytes().lstrip().startswith(b"%PDF-")):
                    if cached_path != path:
                        os.replace(cached_path, path)
                    return Download(path=path.as_posix(), from_cache=True, **meta)
            except (OSError, ValueError, KeyError, TypeError):
                pass  # An incomplete or corrupt cache entry must be downloaded again.

        for attempt in range(self.retries + 1):
            with self._pace_lock:
                if self._last_request is not None:
                    time.sleep(max(0, self.pace - (time.monotonic() - self._last_request)))
                self._last_request = time.monotonic()
            try:
                request = Request(page_url, headers={"User-Agent": "TournamentArchiveInventory/0.1"})
                with urlopen(request, timeout=self.timeout) as response:
                    data = response.read()
                    if kind == "pdf" and not data.lstrip().startswith(b"%PDF-"):
                        raise ValueError("response is not a PDF (missing PDF signature)")
                    meta = {"url": page_url, "final_url": response.geturl(),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "content_type": response.headers.get("Content-Type", "")}
                self.raw_dir.mkdir(parents=True, exist_ok=True)
                # The metadata is written last: a partial write is never a valid cache hit.
                self._write(path, data)
                self._write(metadata_path, json.dumps(meta, ensure_ascii=False).encode("utf-8"))
                return Download(path=path.as_posix(), from_cache=False, **meta)
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                retryable = not isinstance(exc, HTTPError) or exc.code == 429 or 500 <= exc.code < 600
                if attempt == self.retries or not retryable:
                    raise
                time.sleep(min(2 ** attempt, 10))
        raise AssertionError("unreachable")

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, path)

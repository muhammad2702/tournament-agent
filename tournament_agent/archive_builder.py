"""Find references to tournament archive pages in the index HTML."""

from html.parser import HTMLParser
import re
from urllib.parse import urldefrag, urljoin, urlsplit
from .models import DocumentRef, SourceRef
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag
from urllib.request import Request, urlopen
from .models import Download
from dataclasses import asdict
from .models import saved_path





class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.links = []
        self.current = None

    def handle_starttag(self, tag, attrs):
        if tag == "a" and "href" in dict(attrs):
            self.current = [dict(attrs)["href"], []]

    def handle_data(self, data):
        if self.current is not None:
            self.current[1].append(data)

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            href, parts = self.current
            self.links.append((href, " ".join("".join(parts).split())))
            self.current = None


def discover(index_html: str, base_url: str) -> list[SourceRef]:
    """Return archive-page links, retaining fragments and index labels.

    This index uses numbered directories and index.php for its archive pages.
    Other links, including PDFs and oral rounds, are outside this inventory step.
    """
    parser = _Links()
    parser.feed(index_html)
    base = urlsplit(base_url)
    root = base.path.rsplit("/", 1)[0].rstrip("/")
    archive_path = re.compile(re.escape(root) + r"/\d+/index\.php$", re.IGNORECASE)
    refs = []
    for href, label in parser.links:
        resolved = urljoin(base_url, href)
        page_url, anchor = urldefrag(resolved)
        page = urlsplit(page_url)
        if (page.scheme in ("http", "https") and page.netloc.lower() == base.netloc.lower()
                and not page.query and archive_path.fullmatch(page.path)):
            refs.append(SourceRef(page_url, anchor, label))
    return refs


def discover_documents(html: str, source_url: str) -> list[DocumentRef]:
    """Find same-host PDF links and retain their referring page and link label."""
    parser = _Links()
    parser.feed(html)
    host = urlsplit(source_url).netloc.lower()
    refs = []
    for href, label in parser.links:
        url, anchor = urldefrag(urljoin(source_url, href))
        parts = urlsplit(url)
        if (parts.scheme in ("http", "https") and parts.netloc.lower() == host
                and parts.path.lower().endswith(".pdf")):
            refs.append(DocumentRef(url, source_url, anchor, label))
    return refs



# Fetch and cache bytes. This is the only component that accesses the network.




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



# Run the inventory and checkpoint successes and failures in a manifest.



INDEX_URL = "https://www.turgor.ru/en/problems/allproblems.php"


def _decode(data: bytes, content_type: str) -> str:
    match = re.search(r"charset\s*=\s*['\"]?([\w.-]+)", content_type, re.I)
    if not match:
        match = re.search(rb"charset\s*=\s*['\"]?([\w.-]+)", data[:4096], re.I)
    encoding = match.group(1) if match else "utf-8"
    if isinstance(encoding, bytes):
        encoding = encoding.decode("ascii")
    return data.decode(encoding)


def _save(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".json.tmp")
    temp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(temp, path)


def run(data_dir: Path, index_url: str = INDEX_URL, *, timeout: float = 30,
        retries: int = 2, pace: float = 0.5, refresh: bool = False) -> dict:
    data_dir = Path(data_dir)
    manifest_path = data_dir / "manifest.json"
    fetcher = Fetcher(data_dir / "raw", timeout, retries, pace, refresh)
    manifest = {"scope": "numbered index pages and same-host PDF links in the index and downloaded pages; classification and extraction pending",
                "index": {"url": index_url, "status": "pending"}, "references": [], "pages": [],
                "document_references": [], "documents": []}
    _save(manifest_path, manifest)
    try:
        index = fetcher.fetch(index_url)
        manifest["index"] = {**asdict(index), "status": "downloaded"}
        html = _decode(saved_path(index.path).read_bytes(), index.content_type)
        refs = discover(html, index.final_url)
        manifest["references"] = [asdict(ref) for ref in refs]
        manifest["document_references"] = [asdict(ref) for ref in discover_documents(html, index.final_url)]
    except (OSError, ValueError, UnicodeError, LookupError) as exc:
        manifest["index"].update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _save(manifest_path, manifest)
        return manifest

    # dict preserves the order of first appearance and drops duplicate page URLs only.
    page_urls = dict.fromkeys(ref.page_url for ref in refs)
    manifest["pages"] = [{"url": url, "status": "pending"} for url in page_urls]
    _save(manifest_path, manifest)
    for page in manifest["pages"]:
        try:
            page.update(asdict(fetcher.fetch(page["url"])), status="downloaded")
            page_html = _decode(saved_path(page["path"]).read_bytes(), page["content_type"])
            manifest["document_references"].extend(asdict(ref) for ref in discover_documents(page_html, page["final_url"]))
        except (OSError, ValueError, UnicodeError, LookupError) as exc:
            page.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _save(manifest_path, manifest)
    urls = dict.fromkeys(ref["url"] for ref in manifest["document_references"])
    manifest["documents"] = [{"url": url, "status": "pending", "classification_status": "needs_review", "language": "unknown"} for url in urls]
    _save(manifest_path, manifest)
    for document in manifest["documents"]:
        try:
            document.update(asdict(fetcher.fetch(document["url"], kind="pdf")), status="downloaded")
        except (OSError, ValueError) as exc:
            document.update(status="failed", error=f"{type(exc).__name__}: {exc}")
        _save(manifest_path, manifest)
    return manifest

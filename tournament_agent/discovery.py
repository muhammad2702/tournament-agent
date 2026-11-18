"""Find references to tournament archive pages in the index HTML."""

from html.parser import HTMLParser
import re
from urllib.parse import urldefrag, urljoin, urlsplit

from .models import DocumentRef, SourceRef


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

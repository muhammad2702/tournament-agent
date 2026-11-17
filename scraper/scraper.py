"""Collect archive sources. Problem extraction belongs to a later stage."""

import argparse
import re
import time
import logging
from collections import deque
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlsplit

from .downloader import download
from .storage import Storage

INDEX = "https://www.turgor.ru/en/problems/allproblems.php"
logger = logging.getLogger(__name__)


def decode_html(content, encoding):
    if not encoding:
        declaration = re.search(br'charset\s*=\s*["\x27]?([\w-]+)', content[:4096], re.I)
        encoding = declaration.group(1).decode("ascii") if declaration else "utf-8"
    return content.decode(encoding)


class Links(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self.current = None
        self.assets = []

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "img" and attributes.get("src"):
            self.assets.append(attributes["src"])
        if tag == "link" and "stylesheet" in attributes.get("rel", "").split() and attributes.get("href"):
            self.assets.append(attributes["href"])
        if tag == "a":
            self.current = None
            if "href" in attributes:
                self.current = {"href": attributes["href"], "label": ""}
                self.links.append(self.current)

    def handle_data(self, text):
        if self.current is not None:
            self.current["label"] += text

    def handle_endtag(self, tag):
        if tag == "a":
            self.current = None


def in_scope(url):
    parts = urlsplit(url)
    return (parts.scheme in {"http", "https"}
            and parts.hostname in {"www.turgor.ru", "turgor.ru"}
            and bool(re.match(r"^/(?:en/)?problems/\d+/", parts.path)))


def scrape(directory="data", limit=None):
    store = Storage(directory)
    handler = logging.FileHandler(store.root / "scraper.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    log = logging.getLogger()
    previous_level = log.level
    log.setLevel(logging.INFO)
    log.addHandler(handler)
    try:
        return _scrape(store, limit)
    finally:
        log.removeHandler(handler)
        handler.close()
        log.setLevel(previous_level)


def _scrape(store, limit):
    queue = deque([INDEX])
    seen = set()
    processed = cached = failed = 0
    while queue and (limit is None or processed < limit):
        url = queue.popleft()
        if url in seen:
            continue
        seen.add(url)
        processed += 1
        try:
            result = store.cached(url)
            if result:
                content, record = result
                cached += 1
                logger.info("Cache hit url=%s", url)
            else:
                time.sleep(1)
                content, metadata = download(url)
                if url == INDEX:
                    record = dict(metadata, status="read for discovery")
                else:
                    record = store.save_download(url, content, metadata)
            if not content.strip():
                raise ValueError("Empty response")
            if record["content_type"] == "text/html":
                parser = Links()
                parser.feed(decode_html(content, record.get("encoding")))
                if url == INDEX and not any(in_scope(urljoin(record["final_url"], link["href"])) for link in parser.links):
                    raise ValueError("Index contains no archive links; possible error page or changed layout")
                for link in parser.links:
                    target = urljoin(record["final_url"], link["href"])
                    resource, anchor = urldefrag(target)
                    # The index's section links identify problem pages. Bare
                    # tournament index links are navigation, not problem sets.
                    selected = in_scope(target) and (
                        bool(anchor) or (url != INDEX and urlsplit(resource).path.lower().endswith((".pdf", ".ps", ".tex"))))
                    reference = dict(source_url=url, target_url=target,
                                     anchor=anchor, label=link["label"].strip(),
                                     in_scope=selected)
                    if reference not in store.manifest["references"]:
                        store.manifest["references"].append(reference)
                    if reference["in_scope"]:
                        store.manifest["resources"].setdefault(resource, {"status": "pending"})
                        queue.append(resource)
                if url != INDEX:
                    for asset in parser.assets:
                        target = urldefrag(urljoin(record["final_url"], asset))[0]
                        parts = urlsplit(target)
                        if parts.scheme not in {"http", "https"} or parts.hostname not in {"www.turgor.ru", "turgor.ru"}:
                            continue
                        reference = dict(source_url=url, target_url=target, kind="asset")
                        if reference not in store.manifest["references"]:
                            store.manifest["references"].append(reference)
                        store.manifest["resources"].setdefault(target, {"status": "pending"})
                        queue.appendleft(target)
            elif record["content_type"] == "application/pdf" and not content.startswith(b"%PDF-"):
                raise ValueError("Invalid PDF signature")
            record["discovery_status"] = "complete"
            if url == INDEX:
                queue = deque(sorted(queue, key=lambda item: urlsplit(item).path.lower().endswith((".pdf", ".ps", ".tex"))))
            record.pop("error", None)
            logger.info("Processed url=%s bytes=%s", url, len(content))
            print(record["status"], url)
        except Exception as error:
            failed += 1
            record = store.manifest["resources"].setdefault(url, {})
            record.update(status="failed", error=str(error))
            logger.exception("Failed url=%s", url)
            print("failed", url, str(error))
        store.checkpoint()
    print(f"Processed: {processed}; cached: {cached}; failed: {failed}")
    print(f"Resources: {len(store.manifest['resources'])}; references: {len(store.manifest['references'])}")
    pending = sum(r["status"] == "pending" for r in store.manifest["resources"].values())
    print(f"Pending: {pending}; log: {store.root / 'scraper.log'}")
    logger.info("Run finished processed=%s cached=%s failed=%s pending=%s", processed, cached, failed, pending)
    return failed


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--limit", type=int, help="Maximum resources to process, including the index")
    args = parser.parse_args()
    raise SystemExit(bool(scrape(args.data_dir, args.limit)))

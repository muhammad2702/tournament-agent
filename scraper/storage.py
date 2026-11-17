"""Local files and an atomic JSON manifest."""

import hashlib
import json
import re
from urllib.parse import urlsplit
from pathlib import Path


class Storage:
    def __init__(self, directory):
        self.root = Path(directory)
        (self.root / "raw").mkdir(parents=True, exist_ok=True)
        self.path = self.root / "manifest.json"
        self.manifest = json.loads(self.path.read_text()) if self.path.exists() else {
            "resources": {}, "references": []
        }

    def save_download(self, url, content, metadata):
        parts = urlsplit(url)
        relative = parts.path.lstrip("/")
        if relative.endswith((".php", ".htm")):
            relative = relative.rsplit(".", 1)[0] + ".html"
        relative = re.sub(r"[^\w./-]", "_", relative)
        if parts.query:
            relative += "-" + hashlib.sha256(parts.query.encode()).hexdigest()[:12]
        if ".." in relative.split("/"):
            raise ValueError("Unsafe download path")
        path = self.root / "raw" / parts.netloc / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        record = dict(metadata, file=str(path.relative_to(self.root)),
                      sha256=hashlib.sha256(content).hexdigest(),
                      size_bytes=len(content), status="downloaded")
        self.manifest["resources"][url] = record
        return record

    def cached(self, url):
        record = self.manifest["resources"].get(url)
        if not record or record["status"] != "downloaded":
            return None
        path = self.root / record["file"]
        if path.exists():
            content = path.read_bytes()
            if hashlib.sha256(content).hexdigest() == record["sha256"]:
                return content, record
        return None

    def checkpoint(self):
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.manifest, ensure_ascii=False, indent=2))
        temporary.replace(self.path)

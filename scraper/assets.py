"""Static HTML/CSS dependency discovery; no problem-text extraction."""
import re
from html.parser import HTMLParser
from urllib.parse import urljoin, urldefrag, urlsplit

ASSET_EXTENSIONS = {'.css', '.js', '.png', '.gif', '.jpg', '.jpeg', '.svg', '.webp', '.ico', '.bmp', '.avif', '.woff', '.woff2', '.ttf', '.otf', '.eot'}


def srcset_urls(text):
    values = []
    while text.strip():
        text = text.lstrip(" ,\t\r\n")
        match = re.match(r"\S+", text)
        if not match:
            break
        candidate = match.group()
        values.append(candidate.rstrip(','))
        text = text[match.end():]
        if not candidate.endswith(','):
            separator = text.find(',')
            text = text[separator + 1:] if separator >= 0 else ''
    return values


def css_urls(text):
    values = []
    for match in re.finditer(r'url\(\s*(?:"([^"]*)"|\x27([^\x27]*)\x27|([^)]*))\s*\)|@import\s+["\x27]([^"\x27]+)["\x27]', text, re.I):
        values.append(next(value for value in match.groups() if value is not None).strip())
    return values


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.assets = [], []
        self.base = None
        self.current = None
        self.in_style = False
        self.has_html = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        self.has_html |= tag == 'html'
        if tag == 'base' and self.base is None:
            self.base = attrs.get('href')
        if tag == 'a':
            self.current = None
            if attrs.get('href'):
                self.current = {'href': attrs['href'], 'label': ''}
                self.links.append(self.current)
        if tag in {'img', 'script', 'source', 'audio', 'video', 'input', 'embed', 'iframe'} and attrs.get('src'):
            self.assets.append(attrs['src'])
        if attrs.get('poster'):
            self.assets.append(attrs['poster'])
        if attrs.get('background'):
            self.assets.append(attrs['background'])
        if tag in {'image', 'use'}:
            self.assets.extend(attrs[key] for key in ('href', 'xlink:href') if attrs.get(key))
        if tag == 'link' and set(attrs.get('rel', '').split()) & {'stylesheet', 'icon', 'preload', 'modulepreload'} and attrs.get('href'):
            self.assets.append(attrs['href'])
        if attrs.get('srcset'):
            # Data URIs are already embedded; descriptors end at whitespace.
            self.assets.extend(srcset_urls(attrs['srcset']))
        self.assets.extend(css_urls(attrs.get('style', '')))
        if tag == 'style':
            self.in_style = True

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_data(self, text):
        if self.current is not None:
            self.current['label'] += text
        if self.in_style:
            self.assets.extend(css_urls(text))

    def handle_endtag(self, tag):
        if tag == 'a':
            self.current = None
        if tag == 'style':
            self.in_style = False


def resolve(base, target):
    return urldefrag(urljoin(base, target))[0]


def asset_policy(url):
    parts = urlsplit(url)
    if parts.scheme == 'data' or not url:
        return 'embedded'
    if parts.scheme not in {'http', 'https'}:
        return 'unsupported scheme'
    if parts.hostname in {'mc.yandex.ru', 'counter.yadro.ru', 'www.liveinternet.ru', 'www.google-analytics.com', 'www.googletagmanager.com'}:
        return 'tracking, unrelated to problem content'
    from pathlib import PurePosixPath
    extension = PurePosixPath(parts.path).suffix.lower()
    if extension and extension not in ASSET_EXTENSIONS:
        return 'not a supported static asset'
    return None

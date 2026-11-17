"""Static HTML/CSS dependency discovery; no problem-text extraction."""
import re
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


def html_assets(content, encoding=None):
    from .discovery import soup
    document = soup(content, encoding)
    base = document.find('base', href=True)
    urls = []
    for tag in document.find_all(True):
        if tag.name in {'img', 'script', 'source', 'audio', 'video', 'input', 'embed', 'iframe'} and tag.get('src'):
            urls.append(tag['src'])
        for attr in ('poster', 'background'):
            if tag.get(attr): urls.append(tag[attr])
        if tag.name in {'image', 'use'}:
            urls.extend(tag[attr] for attr in ('href', 'xlink:href') if tag.get(attr))
        if tag.name == 'link' and set(tag.get('rel', [])) & {'stylesheet', 'icon', 'preload', 'modulepreload'} and tag.get('href'):
            urls.append(tag['href'])
        if tag.get('srcset'): urls.extend(srcset_urls(tag['srcset']))
        urls.extend(css_urls(tag.get('style', '')))
        if tag.name == 'style': urls.extend(css_urls(tag.get_text()))
    return urls, base['href'] if base else ''


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

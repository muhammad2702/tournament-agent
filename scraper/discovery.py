"""Discover URLs only. Never interpret problem statements or section anchors."""
import re
from urllib.parse import urljoin, urldefrag, urlsplit
from bs4 import BeautifulSoup

INDEX = 'https://www.turgor.ru/en/problems/allproblems.php'


def soup(content, encoding=None):
    return BeautifulSoup(content, 'html.parser', from_encoding=encoding)


def canonical(url):
    return urldefrag(url)[0]


def classify(url):
    parts = urlsplit(url)
    if parts.scheme not in {'http', 'https'} or parts.hostname not in {'www.turgor.ru', 'turgor.ru'}:
        return 'excluded', 'outside the supplied archive host'
    if not re.match(r'^/(?:en/)?problems/', parts.path):
        return 'excluded', 'outside the written-problem archive'
    filename = parts.path.rsplit('/',1)[-1].lower()
    if re.match(r'res(?:ults)?[_\d.]',filename):
        return 'excluded', 'results list, not a problem source'
    if re.search(r'(?:^|[-_])sol(?:utions|\d|[-_.]|$)',filename):
        return 'excluded', 'solutions, excluded from problem-only collection'
    if re.fullmatch(r'/(?:en/)?problems/allproblems\.php', parts.path):
        return 'index', None
    if parts.path.lower().endswith('.pdf'):
        return 'pdf', None
    if re.match(r'^/(?:en/)?problems/\d+/', parts.path) and parts.path.lower().endswith(('.php', '.html', '.htm', '/')):
        return 'html', None
    return 'excluded', 'not an archive HTML page or PDF'


def language_hint(url, label):
    filename = urlsplit(url).path.rsplit('/', 1)[-1]
    if re.search(r'(?:^|[-_.])(eng|english|en)(?:[-_.]|$)', filename, re.I):
        return 'english', 'explicit English marker in filename'
    if 'marking_scheme' in filename.lower():
        return 'uncertain', 'marking-scheme filename; language and scope require review'
    if re.search('[\u0400-\u04ff]', label):
        return 'non_english', 'Cyrillic text in the source link label'
    return 'uncertain', 'no explicit language marker; retain for review'


def discover(content, source_url, encoding=None):
    document = soup(content, encoding)
    base = document.find('base', href=True)
    base_url = urljoin(source_url, base['href']) if base else source_url
    references = []
    for link in document.find_all('a', href=True):
        target = urljoin(base_url, link['href'])
        kind, reason = classify(target)
        label = link.get_text(' ', strip=True)
        language, evidence = language_hint(target, label)
        references.append({'source_url': source_url, 'target_url': target,
                           'download_url': canonical(target), 'label': label,
                           'kind': kind, 'reason': reason,
                           'language_hint': language, 'language_evidence': evidence})
    return references

#!/usr/bin/env python3
"""Download the candidate articles and keep nothing but their editorial text.

Takes the JSON report produced by fetch_veille.py and fetches the page of every
candidate, so that the selected articles can be analysed (see prompts/) without
going back online. Only the text is kept: a news page weighs a hundred kilobytes
of HTML for a few thousand useful characters, and the rest — scripts,
navigation, cookie banners — only bloats and blurs the analysis that follows.

MSRC is the exception: its page is a JavaScript application yielding no text at
all. Its entries are therefore read through the Security Update Guide API, which
gives more than the page — CVSS score, exploitation status, exploitability
index — that is, exactly what the bulletin has to quote.

Each page becomes a `.txt`, alongside an `index.json` tying every file to its
candidate and recording the failures.

Usage:
    python3 fetch_articles.py
    python3 fetch_articles.py --candidates ./output/candidates.json --out-dir ./output/articles
"""
import argparse
import hashlib
import html
import json
import re
import sys
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import NamedTuple

ROOT = Path(__file__).resolve().parent.parent
# Some sites answer 403 to the stdlib default User-Agent.
USER_AGENT = 'Mozilla/5.0 (compatible; VeillePractilio/1.0; +https://practilio.fr)'

# Below this, the page delivered no article: JavaScript skeleton, paywall,
# error page disguised as a 200.
MIN_CHARS = 200

# Network and payload failures of a single fetch; urllib errors all derive from
# OSError. One bad article must not abort the run.
FETCH_ERRORS = (OSError, ValueError, KeyError)

# The MSRC page is a JavaScript application: the scraper gets zero characters
# out of it. Those entries go through the API instead, which needs no key.
MSRC_API = 'https://api.msrc.microsoft.com/sug/v2.0/en-US/'
CVE_RE = re.compile(r'(CVE-\d{4}-\d{4,7})')
# A common Windows CVE affects dozens of SKUs: we quote the first ones and count
# the rest, the bulletin does not need the full list.
MSRC_MAX_PRODUCTS = 12
MSRC_MAX_REVISIONS = 5

# www.debian.org and the mailing list archive both answer an anti-robot page
# ("I Challenge Thee"). The tracker answers, and it is better structured than
# the announcement: CVE, fixed versions per release, urgency.
DEBIAN_TRACKER = 'https://security-tracker.debian.org/tracker/'
DSA_RE = re.compile(r'\b(DSA-\d+-\d+)\b')
# A kernel advisory sometimes references dozens of CVE: we detail the first ones
# and count the rest.
DEBIAN_MAX_CVE = 6

# Elements whose content is never article text. `header` is absent from the
# list: it often carries the standfirst and the publication date.
SKIP_TAGS = frozenset({
    'script', 'style', 'noscript', 'svg', 'head', 'template', 'iframe',
    'form', 'button', 'select', 'nav', 'footer', 'aside',
})
# Fallback when the full filtering yields nothing: on a page with unbalanced
# markup, a `<nav>` that is never closed would swallow the whole document.
HARD_SKIP_TAGS = frozenset({'script', 'style', 'noscript', 'svg', 'head', 'template'})

# Containers of the article body, when the page declares one.
REGION_TAGS = frozenset({'main', 'article'})

BLOCK_TAGS = frozenset({
    'p', 'div', 'br', 'li', 'tr', 'td', 'th', 'ul', 'ol', 'table', 'section',
    'article', 'main', 'blockquote', 'pre', 'figcaption', 'dt', 'dd',
    'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
})


class Fetched(NamedTuple):
    """Result of one candidate fetch; the API paths return text only."""

    text: str
    body: bytes = b''
    final_url: str = ''


class Throttle:
    """Waits `delay` seconds between two requests, never before the first."""

    def __init__(self, delay):
        self.delay = delay
        self.pending = False

    def wait(self):
        if self.delay and self.pending:
            time.sleep(self.delay)
        self.pending = True


class ArticleText(HTMLParser):
    """Extract the text of a page, isolating the article body when possible.

    The text is accumulated in chunks; every `<main>` or `<article>` records the
    range of chunks it covers. The longest of those ranges is the article body —
    the `<article>` of a "read also" block is short by construction.
    """

    def __init__(self, skip_tags):
        super().__init__(convert_charrefs=True)
        self.skip_tags = skip_tags
        self.chunks = []
        self.skipped = []       # stack of the skipped elements currently open
        self.open_regions = []  # start index of the open regions
        self.regions = []       # (start, end) of the closed regions

    def handle_starttag(self, tag, attrs):
        if self.skipped or tag in self.skip_tags:
            if tag in self.skip_tags:
                self.skipped.append(tag)
            return
        if tag in REGION_TAGS:
            self.open_regions.append(len(self.chunks))
        if tag in BLOCK_TAGS:
            self.chunks.append('\n')

    def handle_endtag(self, tag):
        if self.skipped:
            # A closing tag that does not match the top of the stack comes from
            # the inner markup of the skipped element: leave it alone.
            if tag == self.skipped[-1]:
                self.skipped.pop()
            return
        if tag in REGION_TAGS and self.open_regions:
            self.regions.append((self.open_regions.pop(), len(self.chunks)))
        if tag in BLOCK_TAGS:
            self.chunks.append('\n')

    def handle_data(self, data):
        if not self.skipped:
            self.chunks.append(data)

    def text(self):
        """The article body when identifiable, the whole page otherwise."""
        candidates = [''.join(self.chunks[start:end]) for start, end in self.regions]
        best = max(candidates, key=len) if candidates else ''
        return normalize(best if len(best) >= MIN_CHARS else ''.join(self.chunks))


def normalize(text):
    """Tightened whitespace, at most one blank line between two paragraphs."""
    lines = []
    for line in text.split('\n'):
        line = ' '.join(line.split())
        if line or (lines and lines[-1]):
            lines.append(line)
    return '\n'.join(lines).strip()


def strip_markup(markup):
    """Reduce an HTML fragment from the MSRC API to plain text.

    Descriptions come wrapped in `<p>` or `<br>`: too little to justify the page
    parser, which is tuned to find an article inside a whole page.
    """
    return html.unescape(re.sub(r'<[^>]+>', ' ', markup))


def extract(markup):
    """Article text, retried with the looser skip list when the strict one fails."""
    text = ''
    for skip_tags in (SKIP_TAGS, HARD_SKIP_TAGS):
        parser = ArticleText(skip_tags)
        parser.feed(markup)
        parser.close()
        text = parser.text()
        if len(text) >= MIN_CHARS:
            break
    return text


def decode(body, headers):
    charset = headers.get_content_charset()
    if not charset:
        declared = re.search(rb'<meta[^>]+charset=["\']?\s*([\w-]+)', body[:4096], re.I)
        charset = declared.group(1).decode('ascii', 'ignore') if declared else 'utf-8'
    try:
        return body.decode(charset, errors='replace')
    except LookupError:
        return body.decode('utf-8', errors='replace')


def slugify(title, url):
    """Readable file name, made unique by a fingerprint of the URL."""
    ascii_title = unicodedata.normalize('NFKD', title).encode('ascii', 'ignore').decode()
    stem = re.sub(r'[^a-z0-9]+', '-', ascii_title.lower()).strip('-')[:60] or 'article'
    return f"{stem}-{hashlib.sha1(url.encode()).hexdigest()[:8]}"


def candidates_from(report):
    """Unique candidates of the report, in the order it gives them."""
    unique = {}
    for candidate in report['candidates']:
        unique.setdefault(candidate['article_url'] or candidate['id'], candidate)
    return list(unique.values())


def oui_non(value):
    """Translate the Yes/No of the API. The bulletin quotes those fields as is."""
    return {'yes': 'Oui', 'no': 'Non'}.get(str(value).strip().lower(), 'non précisé')


def download(url, timeout, accept=None):
    """Fetch a URL, returning its raw body, its headers and the URL finally served."""
    headers = {'User-Agent': USER_AGENT}
    if accept:
        headers['Accept'] = accept
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        return resp.read(), resp.headers, resp.geturl()


def get_json(url, timeout):
    body, _, _ = download(url, timeout, accept='application/json')
    return json.loads(body.decode('utf-8', errors='replace'))


def fetch_page_text(url, timeout):
    body, headers, _ = download(url, timeout)
    return extract(decode(body, headers))


def msrc_cve(url, title):
    """The CVE an MSRC entry is about, or None when the entry is not one."""
    if 'msrc.microsoft.com' not in url:
        return None
    found = CVE_RE.search(url) or CVE_RE.search(title)
    return found.group(1) if found else None


def msrc_products(cve, timeout):
    """Affected products, reduced to their distinct base names.

    The API returns one row per SKU — 27 for a common Windows CVE. The bulletin
    needs the product family, not the enumeration of the references.
    """
    # No $select: that parameter makes the API answer 999. We take whole rows
    # and keep only two of their fields here.
    query = f"{MSRC_API}affectedProduct?$filter=cveNumber%20eq%20%27{cve}%27&$top=400"
    rows = get_json(query, timeout).get('value', [])
    families = [f for f in dict.fromkeys(row.get('productFamily') for row in rows) if f]
    names = [n for n in dict.fromkeys(row.get('baseProductName') for row in rows) if n]
    return len(rows), families, names


def msrc_summary_lines(vuln, cve):
    """The CVE record itself: severity, CVSS, exploitation, dates, description.

    Both dates are kept as they are: a feed entry is often the revision of an
    old CVE, and the bulletin must be able to discard a republication with no
    news rather than treat it as an alert.
    """
    return [
        f"{vuln.get('cveNumber', cve)} — {vuln.get('cveTitle', '')}".strip(' —'), '',
        f"Source : Microsoft Security Update Guide (API), {vuln.get('issuingCna', '')}",
        f"Produit : {vuln.get('tag', 'non précisé')}",
        f"Gravité Microsoft : {vuln.get('severity', 'non précisée')}"
        f" — impact : {vuln.get('impact', 'non précisé')}",
        f"CVSS : base {vuln.get('baseScore', 'non précisé')},"
        f" temporel {vuln.get('temporalScore', 'non précisé')}"
        f" — {vuln.get('vectorString', 'vecteur non précisé')}",
        f"Exploitation constatée : {oui_non(vuln.get('exploited'))}",
        f"Divulgation publique : {oui_non(vuln.get('publiclyDisclosed'))}",
        f"Index d'exploitabilité Microsoft : {vuln.get('latestSoftwareRelease', 'non précisé')}",
        f"CWE : {', '.join(vuln.get('cweList') or []) or 'non précisé'}",
        f"Publié le {vuln.get('releaseDate', '?')[:10]}"
        f" — dernière révision {vuln.get('latestRevisionDate', '?')[:10]}",
        f"Action client requise : {'oui' if vuln.get('customerActionRequired') else 'non'}",
        '', 'Description',
        normalize(strip_markup(vuln.get('description')
                               or vuln.get('unformattedDescription') or '')) or 'non précisée',
    ]


def msrc_product_lines(cve, timeout):
    try:
        total, families, names = msrc_products(cve, timeout)
    except FETCH_ERRORS:
        return ['', 'Produits affectés : non récupérés (API indisponible)']

    lines = ['', f'Produits affectés ({total} références)',
             f"Familles : {', '.join(families) or 'non précisé'}"]
    lines += [f'  - {name}' for name in names[:MSRC_MAX_PRODUCTS]]
    if len(names) > MSRC_MAX_PRODUCTS:
        lines.append(f'  … et {len(names) - MSRC_MAX_PRODUCTS} autres')
    return lines


def msrc_revision_lines(vuln):
    revisions = vuln.get('revisions') or []
    if not revisions:
        return []

    lines = ['', f'Révisions ({len(revisions)})']
    for revision in revisions[:MSRC_MAX_REVISIONS]:
        note = normalize(strip_markup(revision.get('description') or ''))
        lines.append(f"  - v{revision.get('version', '?')}"
                     f" ({str(revision.get('revisionDate', ''))[:10]}) : {note or 'non précisée'}")
    return lines


def fetch_msrc(cve, timeout):
    """CVE record from the Security Update Guide, rendered as text for the bulletin."""
    vuln = get_json(f'{MSRC_API}vulnerability/{cve}', timeout)
    return '\n'.join(msrc_summary_lines(vuln, cve)
                     + msrc_product_lines(cve, timeout)
                     + msrc_revision_lines(vuln))


def debian_dsa(url, title):
    """The DSA advisory of a Debian entry, or None when the entry is not one."""
    if 'debian.org' not in url:
        return None
    found = DSA_RE.search(title) or DSA_RE.search(url)
    return found.group(1) if found else None


def tracker_section(text, name):
    """The body of a tracker page section, located by its heading."""
    match = re.search(rf'^{name}\n\n(.+?)(?:\n\n|\Z)', text, re.S | re.M)
    return normalize(match.group(1)) if match else ''


def debian_cve_lines(cves, timeout, throttle):
    lines = ['', f'Descriptions des CVE référencés ({len(cves)})']
    for cve in cves[:DEBIAN_MAX_CVE]:
        throttle.wait()
        try:
            description = tracker_section(
                fetch_page_text(f'{DEBIAN_TRACKER}{cve}', timeout), 'Description')
        except FETCH_ERRORS:
            description = ''
        lines.append(f'  {cve} : {description or "non récupérée"}')
    if len(cves) > DEBIAN_MAX_CVE:
        lines.append(f'  … et {len(cves) - DEBIAN_MAX_CVE} autres CVE référencés,'
                     ' non détaillés ici')
    return lines


def fetch_debian(dsa, timeout, throttle):
    """DSA advisory read on the tracker, completed by the descriptions of its CVE.

    The advisory page carries the fixed versions but not the nature of the flaw;
    that one lives on the page of each CVE. Without it the bulletin can neither
    judge the criticality nor tie the advisory to our perimeter.
    """
    page = fetch_page_text(f'{DEBIAN_TRACKER}{dsa}', timeout)
    lines = [f'{dsa} — avis de sécurité Debian', '',
             f'Source : Debian Security Tracker ({DEBIAN_TRACKER}{dsa})', '',
             page]

    cves = list(dict.fromkeys(CVE_RE.findall(page)))
    return '\n'.join(lines + (debian_cve_lines(cves, timeout, throttle) if cves else []))


def fetch_candidate(url, title, timeout, throttle):
    """The text of one candidate, taken from an API when the page cannot give one."""
    throttle.wait()
    cve = msrc_cve(url, title)
    if cve:
        return Fetched(fetch_msrc(cve, timeout))

    dsa = debian_dsa(url, title)
    if dsa:
        return Fetched(fetch_debian(dsa, timeout, throttle))

    body, headers, final_url = download(url, timeout)
    return Fetched(extract(decode(body, headers)), body, final_url)


def new_record(candidate):
    """Index entry of a candidate, before its fetch fills in the outcome."""
    return {
        'id': candidate['id'],
        'title': candidate['title'],
        'source': candidate['source'],
        'date': candidate['date'],
        'url': candidate['article_url'],
        'file': None,
        'status': None,
    }


def process_candidate(candidate, out_dir, args, throttle):
    """Fetch one candidate into `out_dir` and return its index entry."""
    record = new_record(candidate)
    url = candidate['article_url']
    if not url:
        record['status'] = 'no article url'
        return record

    destination = out_dir / f"{slugify(candidate['title'], url)}.txt"
    record['file'] = destination.name
    if destination.exists() and not args.force:
        record.update(status='cached', chars=len(destination.read_text(encoding='utf-8')))
        return record

    try:
        fetched = fetch_candidate(url, candidate['title'], args.timeout, throttle)
    except FETCH_ERRORS as exc:
        print(f'  échec {url} — {exc}', file=sys.stderr)
        record.update(status=f'error: {exc}', file=None)
        return record

    if args.keep_html and fetched.body:
        destination.with_suffix('.html').write_bytes(fetched.body)

    if len(fetched.text) < MIN_CHARS:
        # The page answered, but with no usable article: the prompts discard on
        # this status rather than filling the gap from memory.
        print(f'  vide {url} — {len(fetched.text)} caractères extraits', file=sys.stderr)
        record.update(status='empty', chars=len(fetched.text))
        return record

    destination.write_text(fetched.text + '\n', encoding='utf-8')
    record.update(status='ok', bytes=len(fetched.body), chars=len(fetched.text),
                  fetched_at=datetime.now(tz=timezone.utc).isoformat())
    if fetched.final_url and fetched.final_url != url:
        record['final_url'] = fetched.final_url
    return record


def load_candidates(path):
    try:
        report = json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        print(f"{path} introuvable — lance d'abord `make run`.", file=sys.stderr)
        sys.exit(1)

    candidates = candidates_from(report)
    if not candidates:
        print(f'Aucun candidat dans {path}', file=sys.stderr)
        sys.exit(1)
    return candidates


def report_outcome(index, out_dir):
    """Print the run summary; the exit code tells whether any article is available."""
    downloaded = sum(1 for record in index if record['status'] == 'ok')
    cached = sum(1 for record in index if record['status'] == 'cached')
    failures = len(index) - downloaded - cached
    print(f'{downloaded} téléchargés, {cached} déjà en cache, {failures} en échec '
          f'→ {out_dir}', file=sys.stderr)
    sys.exit(1 if downloaded + cached == 0 else 0)


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--candidates', default=str(ROOT / 'output' / 'candidates.json'),
                        help="Rapport JSON de fetch_veille.py (défaut : output/candidates.json)")
    parser.add_argument('--out-dir', default=str(ROOT / 'output' / 'articles'),
                        help="Dossier de destination (défaut : output/articles)")
    parser.add_argument('--delay', type=float, default=1.0,
                        help="Pause entre deux requêtes, en secondes (défaut 1.0)")
    parser.add_argument('--timeout', type=float, default=30.0, help="Timeout HTTP (défaut 30 s)")
    parser.add_argument('--force', action='store_true',
                        help="Retélécharge les articles déjà présents")
    parser.add_argument('--keep-html', action='store_true',
                        help="Conserve aussi la page brute, pour vérifier une extraction douteuse")
    return parser.parse_args()


def main():
    args = parse_args()
    candidates = load_candidates(args.candidates)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    throttle = Throttle(args.delay)
    index = [process_candidate(candidate, out_dir, args, throttle) for candidate in candidates]

    (out_dir / 'index.json').write_text(
        json.dumps(index, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    report_outcome(index, out_dir)


if __name__ == '__main__':
    main()

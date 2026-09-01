#!/usr/bin/env python3
"""Fetch and filter articles from a FreshRSS feed for the Practilio veille.

Reproduces the manual procedure:
1. paginate over the FreshRSS query.php endpoint (GReader JSON) until exhaustion,
2. group the revisions of a single article (an MSRC CVE is often published
   several times: initial version + "informational change only"),
3. drop the bulk noise named in exclusions.json — and only that,
4. emit a flat JSON candidate report, to be triaged by prompts/rapport-securite.md.

The filtering is deliberately an *exclusion* list, not an inclusion one. An
inclusion list has to name the products an article will be about, but threat
intel is named after the vendor that just got breached — PaperCut, JFrog,
Gitea — which no list written in advance can contain. Keeping everything and
removing named noise loses nothing silently; the reverse loses a zero-day.

The feed URL is never hardcoded: `make run` sources .env and passes --url.

Usage:
    python3 fetch_veille.py \
        --url "http://localhost:8080/api/query.php?user=admin&f=greader&t=TOKEN" \
        --out ./output/candidates.json
"""
import argparse
import html
import json
import re
import sys
import urllib.request
from collections import Counter, defaultdict
from datetime import date, datetime, time, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

WINDOW_DAYS = 7
EXCERPT_CHARS = 220
DEFAULT_EXCLUSIONS = Path(__file__).resolve().parent / 'exclusions.json'

# Known noise: MSRC revisions carrying no new factual content.
NOISE_SNIPPETS = ('informational change only', 'bulletin revision')

TAG_RE = re.compile(r'<[^>]+>')


class HrefGrabber(HTMLParser):
    """Collects the href of the first <a> encountered."""

    def __init__(self):
        super().__init__()
        self.href = None

    def handle_starttag(self, tag, attrs):
        if tag == 'a' and self.href is None:
            self.href = dict(attrs).get('href')


def fix_article_url(url):
    """Repair RSS <link> values that contain markup instead of a URL.

    esante.gouv.fr serialises the Drupal-rendered "view" link into <link>
    instead of the article URL; FreshRSS percent-encodes it and resolves it
    against the site root, yielding canonicals such as:

        https://esante.gouv.fr/%3Ca%20href%3D%22/agenda/xxx%22%20...%3Eview%3C/a%3E

    i.e. `<a href="/agenda/xxx" hreflang="fr">view</a>`. Any URL without that
    pattern is returned unchanged.
    """
    parts = urlsplit(url)
    decoded = unquote(urlunsplit(('', '', parts.path, parts.query, parts.fragment)))
    if '<a' not in decoded.lower():
        return url

    grabber = HrefGrabber()
    grabber.feed(decoded)
    grabber.close()
    if not grabber.href:
        return url

    root = urlunsplit((parts.scheme, parts.netloc, '/', '', ''))
    # `%` stays safe so an already-encoded href survives untouched, `?#` so a
    # query string is not clobbered.
    return urljoin(root, quote(grabber.href, safe=":/?#[]@!$&'()*+,;=~%"))


def strip_html(markup):
    """Reduce feed content HTML to plain text.

    Keyword matching runs on this: leaving the markup in matches attribute
    values (a "container" in a CSS class is not an article about containers).
    """
    return html.unescape(TAG_RE.sub(' ', markup)).strip()


def window_start():
    """Start of the veille window: midnight of the most recent Saturday.

    Anchored on a date rather than on `now` so that two runs on the same day
    see the same window.
    """
    today = date.today()
    return datetime.combine(today - timedelta(days=(today.weekday() - 5) % 7), time.min)


def fetch_page(base_url, nb, offset):
    sep = '&' if '?' in base_url else '?'
    with urllib.request.urlopen(f'{base_url}{sep}nb={nb}&offset={offset}', timeout=30) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_articles(raw_json, cutoff):
    """Articles of one page, truncated to the veille window.

    Also reports how many entries were actually served and whether the window
    was crossed — the caller cannot deduce either from the item count alone.
    """
    entries = json.loads(raw_json)['items']
    items = []
    for item in entries:
        item_date = datetime.fromtimestamp(int(item['published']))
        if item_date < cutoff:
            # The feed is served newest-first: everything below is older.
            return items, len(entries), True
        canonical = item.get('canonical')
        items.append({
            'id': item['id'],
            'title': item['title'],
            'source': f"{item['origin']['title']} - {item['origin']['htmlUrl']}",
            'article': fix_article_url(canonical[0]['href']) if canonical else '',
            'date': item_date,
            'text': ' '.join(strip_html(item['content']['content']).split()),
        })
    return items, len(entries), False


def fetch_all(base_url, nb, max_pages, cutoff):
    """Paginate until the veille window is crossed.

    The offset advances by the number of entries *served*, never by the `nb`
    requested: FreshRSS caps a response (400 entries on our instance) without
    saying so, and stepping by `nb` would skip everything past the cap. For the
    same reason a short page is not an end-of-feed signal — only an empty page
    or an entry older than the window is.
    """
    all_items, offset = [], 0
    for _ in range(max_pages):
        items, served, reached_cutoff = parse_articles(fetch_page(base_url, nb, offset), cutoff)
        all_items.extend(items)
        if reached_cutoff or not served:
            break
        offset += served
    return all_items


def dedupe(items):
    """Merge the revisions of a single article, keeping the most informative text.

    Revisions share an article URL but each gets its own feed entry id, so the
    canonical URL is the grouping key; entries without one stay on their id.
    """
    by_article = defaultdict(list)
    for item in items:
        by_article[item['article'] or item['id']].append(item)

    merged = []
    for revisions in by_article.values():
        substantive = [r for r in revisions
                       if r['text'] and not any(n in r['text'].lower() for n in NOISE_SNIPPETS)]
        best = max(substantive, key=lambda r: len(r['text'])) if substantive else revisions[-1]
        merged.append({**best,
                       'date': min(r['date'] for r in revisions),
                       'revisions': len(revisions)})
    return merged


def compile_rules(config):
    """Exclusion rules, ready to match: (regex on title, source substring, reason)."""
    return [(re.compile(rule['motif'], re.I), rule.get('source', ''), rule['raison'])
            for rule in config['regles']]


def excluded_by(item, rules):
    """The reason this item is dropped, or None to send it to the triage.

    A rule matches on the title, never on the body: the body of a health-sector
    article always contains "santé", which is why matching it filtered nothing
    and mislabelled every event announcement as a threat.
    """
    for pattern, source, reason in rules:
        if pattern.search(item['title']) and source.lower() in item['source'].lower():
            return reason
    return None


def split_on_rules(items, rules):
    """Split the articles into (candidates, reasons for the dropped ones)."""
    kept, dropped = [], []
    for item in items:
        reason = excluded_by(item, rules)
        if reason:
            dropped.append(reason)
        else:
            kept.append(item)
    return kept, dropped


def build_report(kept, dropped, raw_count, merged_count):
    """Assemble the candidate report: counts, what was excluded, then the candidates.

    `excluded` is a tally per reason rather than a list of titles: it is there to
    show that a rule still earns its place, or that it has started to overreach.
    """
    return {
        'generated_at': datetime.now(tz=timezone.utc).isoformat(),
        'window_days': WINDOW_DAYS,
        'counts': {
            'raw_entries': raw_count,
            'unique_articles': merged_count,
            'excluded': len(dropped),
            'candidates': len(kept),
        },
        'excluded': [{'raison': reason, 'n': n} for reason, n in Counter(dropped).most_common()],
        'candidates': [
            {
                'id': e['id'],
                'title': e['title'],
                'source': e['source'],
                'article_url': e['article'],
                'date': e['date'].isoformat(),
                'revisions': e['revisions'],
                'excerpt': e['text'][:EXCERPT_CHARS],
            }
            for e in sorted(kept, key=lambda x: x['date'], reverse=True)
        ],
    }


def write_report(report, out_path):
    """Write the report to `out_path`, or to stdout when no path is given."""
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if not out_path:
        print(payload)
        return
    Path(out_path).write_text(payload + '\n', encoding='utf-8')
    counts = report['counts']
    print(f"Écrit dans {out_path} ({counts['candidates']} candidats, "
          f"{counts['excluded']} exclus, sur {counts['raw_entries']} "
          f"entrées brutes).", file=sys.stderr)


def parse_args():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', required=True,
                        help="URL de query.php (format greader), cf. API_URL dans .env")
    parser.add_argument('--nb', type=int, default=200, help="Articles par page (défaut 200)")
    parser.add_argument('--max-pages', type=int, default=10, help="Garde-fou anti-boucle infinie")
    parser.add_argument('--out', help="Fichier JSON de sortie (défaut : stdout)")
    parser.add_argument('--raw-json', help="Optionnel : dump JSON de tous les articles bruts avant filtrage")
    parser.add_argument('--exclusions-file', default=str(DEFAULT_EXCLUSIONS),
                        help="Règles d'exclusion (défaut : src/exclusions.json)")
    return parser.parse_args()


def main():
    args = parse_args()
    rules = compile_rules(json.loads(Path(args.exclusions_file).read_text(encoding='utf-8')))

    raw_items = fetch_all(args.url, args.nb, args.max_pages, window_start())
    if not raw_items:
        print(f"Aucun article récupéré — vérifie le flux : {args.url}", file=sys.stderr)
        sys.exit(1)

    if args.raw_json:
        Path(args.raw_json).write_text(
            json.dumps(raw_items, ensure_ascii=False, indent=2, default=str), encoding='utf-8')

    merged = dedupe(raw_items)
    kept, dropped = split_on_rules(merged, rules)
    write_report(build_report(kept, dropped, len(raw_items), len(merged)), args.out)


if __name__ == '__main__':
    main()

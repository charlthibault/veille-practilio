#!/usr/bin/env python3
"""Récupère et filtre les articles d'un flux FreshRSS pour la veille Practilio.

Reproduit la procédure suivie manuellement :
1. pagine sur l'endpoint query.php de FreshRSS jusqu'à épuisement,
2. regroupe les révisions d'un même article (un CVE MSRC est souvent publié
   plusieurs fois : version initiale + "informational change only"),
3. filtre par mots-clés (voir keywords.json) selon les critères de filtrage
   du plan de veille (section 4 de plan_de_veille_practilio.md),
4. sort une liste markdown de candidats groupés par catégorie de gabarit_bulletin.md,
   à trier manuellement avant de lancer prompt_resume_article.md sur les retenus.

L'URL du flux n'est pas codée en dur : par défaut elle est résolue par
get_freshrss_query_url(), qui relit la user query déjà configurée dans
FreshRSS (le volume Docker est persistant) et provisionne l'instance si le
setup n'a pas encore été fait — cf. src/freshrss.py.

Usage :
    python3 fetch_veille.py --out ./output/candidates.md
    python3 fetch_veille.py --url "http://localhost:8080/api/query.php?user=admin&t=TOKEN&f=html"
"""
import json
import argparse
import urllib.request
import sys
import re
from pathlib import Path
from collections import defaultdict

TITLE_RE = re.compile(r'class="title"><a[^>]*href="([^"]*)"[^>]*>([^<]*)</a>')
SOURCE_RE = re.compile(r'<span>([^<]*)</span>')
DATE_RE = re.compile(r'datetime="([^"]*)"')
TEXT_RE = re.compile(r'<div class="text">\s*(.*?)\s*</div>', re.S)
TAG_RE = re.compile(r'<[^>]+>')

# Bruit connu : révisions MSRC sans nouveau contenu factuel.
NOISE_SNIPPETS = ['informational change only', 'bulletin revision']


def fetch_page(base_url, nb, offset):
    sep = '&' if '?' in base_url else '?'
    url = f"{base_url}{sep}nb={nb}&offset={offset}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_articles(html):
    items = []
    for block in html.split('<article')[1:]:
        title_m = TITLE_RE.search(block)
        if not title_m:
            continue
        source_m = SOURCE_RE.search(block)
        date_m = DATE_RE.search(block)
        text_m = TEXT_RE.search(block)
        text = TAG_RE.sub('', text_m.group(1)).strip() if text_m else ''
        items.append({
            'link': title_m.group(1),
            'title': title_m.group(2).strip(),
            'source': source_m.group(1).strip() if source_m else '',
            'date': date_m.group(1) if date_m else '',
            'text': text,
        })
    return items


def fetch_all(base_url, nb, max_pages):
    all_items = []
    offset = 0
    for _ in range(max_pages):
        html = fetch_page(base_url, nb, offset)
        items = parse_articles(html)
        if not items:
            break
        all_items.extend(items)
        if len(items) < nb:
            break
        offset += nb
    return all_items


def dedupe(items):
    """Fusionne les révisions d'un même lien : garde le texte le plus
    informatif et la plage de dates première/dernière publication."""
    by_link = defaultdict(list)
    for it in items:
        by_link[it['link']].append(it)

    merged = []
    for link, revisions in by_link.items():
        revisions.sort(key=lambda r: r['date'])
        substantive = [r for r in revisions
                       if r['text'] and not any(n in r['text'].lower() for n in NOISE_SNIPPETS)]
        best = max(substantive, key=lambda r: len(r['text'])) if substantive else revisions[-1]
        merged.append({
            'link': link,
            'title': best['title'],
            'source': best['source'],
            'text': best['text'],
            'first_date': revisions[0]['date'],
            'last_date': revisions[-1]['date'],
            'revisions': len(revisions),
        })
    return merged


def match_categories(item, keywords):
    haystack = f"{item['title']} {item['text']}".lower()
    matched = {}
    for category, terms in keywords.items():
        hits = [t for t in terms if t.lower() in haystack]
        if hits:
            matched[category] = hits
    return matched


def to_markdown(grouped):
    lines = []
    for category, entries in grouped.items():
        if not entries:
            continue
        lines.append(f"## {category}\n")
        for e in sorted(entries, key=lambda x: x['first_date'], reverse=True):
            lines.append(f"- **{e['title']}**")
            lines.append(f"  - Source : {e['source']} — {e['link']}")
            date_range = e['first_date']
            if e['last_date'] != e['first_date']:
                date_range += f" (dernière révision : {e['last_date']}, {e['revisions']} révisions)"
            lines.append(f"  - Date : {date_range}")
            lines.append(f"  - Mots-clés détectés : {', '.join(e['matched'])}")
            if e['text']:
                snippet = e['text'][:220]
                lines.append(f"  - Extrait : {snippet}")
            lines.append("")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=None,
                        help="URL de query.php (défaut : résolue/provisionnée via freshrss.py)")
    parser.add_argument('--refresh', action='store_true',
                        help="Force un rafraîchissement des flux avant extraction")
    parser.add_argument('--nb', type=int, default=200, help="Articles par page (défaut 200)")
    parser.add_argument('--max-pages', type=int, default=10, help="Garde-fou anti-boucle infinie")
    parser.add_argument('--out', help="Fichier markdown de sortie (défaut : stdout)")
    parser.add_argument('--raw-json', help="Optionnel : dump JSON de tous les articles bruts avant filtrage")
    parser.add_argument('--keywords-file', default=str(Path(__file__).resolve().parent / 'keywords.json'),
                        help="Mots-clés de filtrage par catégorie (défaut : src/keywords.json)")
    args = parser.parse_args()

    keywords = json.loads(Path(args.keywords_file).read_text(encoding='utf-8'))

    raw_items = fetch_all(args.url, args.nb, args.max_pages)
    if not raw_items:
        print(f"Aucun article récupéré — vérifie le flux : {args.url}", file=sys.stderr)
        sys.exit(1)

    if args.raw_json:
        Path(args.raw_json).write_text(
            json.dumps(raw_items, ensure_ascii=False, indent=2), encoding='utf-8')

    merged = dedupe(raw_items)

    grouped = defaultdict(list)
    for item in merged:
        cats = match_categories(item, keywords)
        for category, hits in cats.items():
            entry = dict(item)
            entry['matched'] = hits
            grouped[category].append(entry)

    # Respecte l'ordre des catégories tel que défini dans keywords.json / gabarit_bulletin.md
    ordered = {cat: grouped.get(cat, []) for cat in keywords}
    total_candidates = sum(len(v) for v in ordered.values())

    header = (f"<!-- {len(raw_items)} entrées brutes -> {len(merged)} articles dédupliqués "
              f"-> {total_candidates} candidats retenus (avant tri manuel) -->\n\n")
    md = header + to_markdown(ordered)

    if args.out:
        Path(args.out).write_text(md, encoding='utf-8')
        print(f"Écrit dans {args.out} ({total_candidates} candidats, {len(merged)} articles uniques sur {len(raw_items)} entrées brutes).", file=sys.stderr)
    else:
        print(md)


if __name__ == '__main__':
    main()

# Veille sécurité

Chaîne de veille hebdomadaire de l'équipe sécurité. Elle relève les flux RSS,
filtre ce qui peut nous concerner, et sert de base à deux documents : un
**bulletin détaillé** publié sur Office 365, et une **synthèse courte** postée
dans le canal Teams.

Chacun fait tourner sa propre instance. Ce dépôt ne conserve pas les rapports
produits : il porte l'outillage et les réglages.

## Le principe

Trois étapes par semaine. La première est automatique, les deux autres se font
avec un assistant, à partir des prompts du dépôt.

```
   docker/feeds.opml  ─┐
   src/exclusions.json ─┤        les flux suivis et le bruit à écarter    
                       │
                       └────────┐
                                │
  ┌─────────────────────────────┬────────────────────────────────┐
  │  1. COLLECTE                      make articles     ~ 5 min  │
  │     FreshRSS relève les flux, garde les 7 derniers jours,    │
  │     retire le bruit connu, télécharge le texte des articles  │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
                      output/articles/*.txt
                                │
  ┌─────────────────────────────┴────────────────────────────────┐
  │  2. BULLETIN DÉTAILLÉ             assistant        ~ 10 min  │
  │     prompts/rapport-securite.md                              │
  │     trie le lot, puis une fiche par article retenu           │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
                   output/bulletin-<date>.html   ──►   Word / OneNote
                                │
  ┌─────────────────────────────┴────────────────────────────────┐
  │  3. SYNTHÈSE                      assistant        ~ 3 min   │
  │     prompts/synthese-securite.md                             │
  │     600 mots dérivés du bulletin, et de rien d'autre         │
  └─────────────────────────────┬────────────────────────────────┘
                                ▼
                   output/synthese-<date>.html  ──►   canal Teams
```

Deux choses à retenir avant de commencer :

- **La collecte ne trie pas, elle déblaie.** Elle ne retire que le bruit de masse
  nommé dans `src/exclusions.json` — aujourd'hui les republications Chromium du
  flux Microsoft. Tout le reste part au tri de l'étape 2. Un lot de 100 articles
  pour 15 retenus est normal.
- **La synthèse ne relit jamais les articles.** Elle se dérive du bulletin. Les
  deux documents doivent dire la même chose à deux niveaux de détail.

## Avant de commencer

Il faut `docker`, `make` et `uv`. Rien d'autre : les scripts n'utilisent que la
bibliothèque standard de Python, et PHP ne tourne que dans le conteneur.

## La semaine type

### 1. Collecter

```sh
make articles
```

Cette commande fait tout : elle démarre le conteneur (et le crée au premier
appel, comptez quelques minutes de plus), rafraîchit les flux, filtre, puis
télécharge le texte de chaque article retenu.

```
Écrit dans .../output/candidates.json (106 candidats, 305 exclus, sur 411 entrées brutes).
106 téléchargés, 0 déjà en cache, 0 en échec → .../output/articles
```

Les deux lignes se lisent ensemble.

La première dit ce que la collecte a déblayé : ici 305 republications Chromium
du flux Microsoft, sur 411 entrées. Le détail par motif est dans
`candidates.json`, section `excluded`.

La seconde dit ce qui est réellement exploitable. Un ou deux échecs par semaine
sont normaux — site injoignable, page sans contenu. Ils sont tracés dans
`index.json` avec leur motif, et le bulletin les écarte pour « contenu
indisponible » plutôt que de combler de mémoire.

Les avis Microsoft et Debian ne passent pas par leur page : voir
« Sous le capot ».

Vous obtenez :

| Fichier | Contenu |
| --- | --- |
| `output/candidates.json` | les candidats, et le décompte de ce qui a été exclu, par motif |
| `output/articles/*.txt` | le texte de chaque article |
| `output/articles/index.json` | le lien entre les deux, et les échecs éventuels |

### 2. Rédiger le bulletin détaillé

Ouvrez le dépôt avec votre assistant et demandez-lui de suivre le prompt :

```
suis les instructions dans @prompts/rapport-securite.md
```

Il lit `output/articles/`, classe chaque article en **retenu**, **contexte** ou
**écarté**, puis rédige une fiche par article retenu : criticité, produits
affectés, lien avec notre périmètre, scénario d'impact, actions et points de
détection.

Enregistrez le résultat dans `output/bulletin-<AAAA-MM-JJ>.html`, la date étant
celle du dernier jour couvert.

> Le fichier est du HTML et non du Markdown : ni Teams, ni Word, ni OneNote ne
> rendent le Markdown. Word ouvre le HTML et le convertit en styles natifs.

**Relisez le tri avant de continuer.** C'est le seul endroit où votre jugement
est irremplaçable : la section « Articles écartés » liste chaque rejet avec son
motif, en une ligne. Un article mal classé se rattrape ici, pas après publication.

### 3. Rédiger la synthèse

```
suis les instructions dans @prompts/synthese-securite.md
```

Il produit une version courte — 600 mots, 5 sujets au maximum — destinée à un
lectorat plus large, lue en grande partie sur mobile.

Enregistrez le résultat dans `output/synthese-<AAAA-MM-JJ>.html`.

### 4. Publier — le bulletin d'abord

L'ordre compte : la synthèse renvoie vers le bulletin, elle ne peut donc pas
partir avant lui. Le prompt laisse à sa place un marqueur
`⟨COLLER ICI LE LIEN DU BULLETIN⟩`, volontairement voyant.

**Le bulletin** — ouvrez `output/bulletin-<date>.html` avec Word. Vérifiez le
volet de navigation : les titres doivent former une arborescence propre. Puis
« Enregistrer sous » en `.docx`, et déposez sur `<emplacement O365 — à compléter>`.
Pour une page OneNote, collez le contenu du fichier dans une page neuve du
bloc-notes d'équipe.

**La synthèse** — ouvrez `output/synthese-<date>.html` dans un navigateur, tout
sélectionner, copier, coller dans une nouvelle conversation du canal Teams
`<canal — à compléter>`. Renseignez l'objet donné en tête du fichier, puis
remplacez le marqueur par le lien du bulletin déposé juste avant.

### 5. Régler la collecte

Trois minutes, et c'est ce qui fait progresser l'outil.

La section « Articles écartés » du bulletin dit *pourquoi* chaque article a été
jeté. Si un motif revient — un flux qui ne produit que des annonces d'événements,
un flux qui ne produit que du bruit — corrigez
[`src/exclusions.json`](src/exclusions.json) ou
[`docker/feeds.opml`](docker/feeds.opml), et commitez.

N'excluez que ce que vous pouvez nommer. Un article écarté à la collecte est
invisible : il ne figurera même pas dans la section « Articles écartés » du
bulletin. Un article de trop, lui, se règle en une ligne au tri. C'est la seule chose que
ce dépôt accumule dans le temps.

Ajoutez en pied de chaque rapport publié le hash du commit qui l'a produit
(`git rev-parse --short HEAD`) : dans trois mois, il dira quel réglage a donné
quel résultat.

---

**Attention** — `make clean` efface `output/`. Tant que les rapports ne sont pas
publiés sur O365, c'est la seule copie. Publiez avant de nettoyer.

## Commandes

`make articles` suffit à dérouler toute la collecte ; les autres cibles servent
au dépannage.

| Cible | Rôle |
| --- | --- |
| `articles` | déroule tout et extrait le texte dans `output/articles/` |
| `run` | *(défaut)* s'arrête après `output/candidates.json` |
| `up` | démarre le conteneur, le crée au besoin |
| `refresh` | force la récupération des flux dans FreshRSS |
| `build` | construit l'image FreshRSS déjà provisionnée |
| `env` | régénère `.env` depuis le token courant |
| `clean` | supprime les fichiers générés, le conteneur, les volumes et l'image |
| `help` | liste les cibles |

## Réglages

```sh
make articles VEILLE_PORT=8099 VEILLE_CONTAINER=freshrss-veille
```

| Variable | Défaut |
| --- | --- |
| `VEILLE_IMAGE` / `VEILLE_CONTAINER` | `veille-freshrss` / `freshrss` |
| `VEILLE_PORT` / `VEILLE_BASE_URL` | `8080` / `http://localhost:$(VEILLE_PORT)` |
| `VEILLE_USER` / `VEILLE_PASSWORD` | `admin` / `veille` |
| `VEILLE_QUERY_NAME` | `last7days` |
| `VEILLE_NB` | `1000` |

## Fichiers du dépôt

| Fichier | Rôle |
| --- | --- |
| `docker/feeds.opml` | les flux suivis — source de vérité de l'abonnement |
| `src/exclusions.json` | le bruit de masse à écarter avant le tri |
| `prompts/rapport-securite.md` | bulletin détaillé, en HTML pour Word / OneNote |
| `prompts/synthese-securite.md` | synthèse courte, en HTML pour Teams |
| `Dockerfile` | image FreshRSS provisionnée au build |
| `docker/ensure_query.php` | crée ou relit la user query, renvoie son token |
| `src/fetch_veille.py` | pagination, déduplication, filtrage |
| `src/fetch_articles.py` | téléchargement, extraction du texte, et fiches MSRC via l'API |

## Sous le capot

Cinq points, les seuls qui se rappellent à vous un jour ou l'autre.

**Le setup est fait au `docker build`** : installation, compte admin, abonnement
aux flux, première récupération, user query partagée. Les articles récupérés à ce
moment-là sont datés du build ; c'est le `refresh` de chaque `make run` qui
garantit la fraîcheur, le cron interne ne tournant que conteneur allumé.

**`.env` est réécrit à chaque `make run`**, depuis le token lu dans le conteneur.
Ne l'éditez pas : un réglage se change dans le `Makefile`. C'est ce qui garantit
qu'un poste ne travaille jamais avec un token périmé ni avec des paramètres
retouchés à la main — deux dérives silencieuses, qui font qu'une instance
n'analyse plus le même corpus que les autres.

**`VEILLE_NB` est une taille de page demandée, pas un total.** L'instance plafonne
ses réponses à 400 entrées sans le signaler ; `fetch_veille.py` avance donc son
offset du nombre d'entrées réellement servies. Une page courte n'est pas une fin
de flux.

**Changer `VEILLE_USER`, `VEILLE_PASSWORD` ou `VEILLE_QUERY_NAME`** suppose un
`make build` et un volume neuf : ces valeurs partent au build. Le mot de passe
passe par un secret BuildKit, il n'apparaît donc ni dans les couches ni dans
`docker history` — revers, le changer seul ne déclenche aucun rebuild
(`docker builder prune` ou `--no-cache`).

**Deux flux ne passent pas par leur page**, parce que leur page ne donne rien.

*Microsoft* — le portail du Security Update Guide est une application
JavaScript : le téléchargement en tirait zéro caractère. `fetch_articles.py`
détecte une URL MSRC, en extrait le CVE et interroge `api.msrc.microsoft.com`
(aucune clé requise). On y gagne ce que la page n'aurait jamais donné : score et
vecteur CVSS, gravité et impact Microsoft, **statut d'exploitation**,
divulgation publique, index d'exploitabilité, produits affectés et historique
des révisions.

Ce dernier point compte : le flux MSRC republie une fiche à chaque révision,
souvent pour une simple correction documentaire. Les deux dates rendues par
l'API permettent au bulletin d'écarter une republication en le justifiant, au
lieu de la traiter comme une alerte nouvelle. Une semaine hors Patch Tuesday
peut ne contenir que des republications.

*Debian* — `www.debian.org` et l'archive de la liste de diffusion répondent
toutes deux une page anti-robot. Les avis sont donc lus sur
`security-tracker.debian.org`, qui répond et qui est mieux structuré que
l'annonce : versions vulnérables et corrigées par release, urgence, et la liste
des CVE. La page de l'avis ne dit pas la nature de la faille — le script va la
chercher sur la page de chaque CVE, les six premiers, le reste étant compté. Un
avis sur le noyau ou sur Wireshark en référence parfois plusieurs dizaines.

**Le `Dockerfile` accepte en plus** `FRESHRSS_LANGUAGE`, `VEILLE_SINCE_HOURS`
(fenêtre des exports, 168 h), `VEILLE_MAX_POSTS`, et `VEILLE_QUERY_GET` /
`_ORDER` / `_STATE` / `_SEARCH`.

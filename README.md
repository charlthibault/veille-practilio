# Veille

Extraction et filtrage des articles de veille depuis une instance FreshRSS locale.

```sh
make up     # construit l'image et démarre le conteneur
make run    # écrit output/candidates.md   (cible par défaut)
```

## Cibles

| Cible | Rôle |
| --- | --- |
| `build` | construit l'image FreshRSS déjà provisionnée |
| `up` | démarre le conteneur, le crée et remplit les flux au besoin |
| `run` | *(défaut)* produit `.env` si absent, puis écrit `output/candidates.md` |
| `refresh` | force la récupération des articles |
| `clean` | supprime les fichiers générés, le conteneur, les volumes et l'image |
| `help` | liste les cibles |

## Fonctionnement

```
Dockerfile ──build──> image provisionnée ──up──> conteneur ──> .env ──> candidates.md
```

**Le setup est fait au `docker build`** : installation, compte admin, abonnement
aux flux de `docker/feeds.opml`, user query partagée. L'image de base ne déclare
aucun `VOLUME`, donc ce que le build écrit dans `/var/www/FreshRSS/data` sert à
peupler un volume nommé vide au premier `docker run` ; un volume déjà rempli
masque ces données — l'état persisté fait autorité. Le build tourne en `root`,
d'où le `cli/access-permissions.sh` final : sans lui Apache ne pourrait pas lire
`data/users/` et `api/query.php` répondrait `User not found!`.

Les articles ne sont **pas** figés dans l'image (ils seraient périmés) : `up`
les récupère à la création du conteneur, puis le cron interne prend le relais.

**L'URL n'est jamais codée en dur.** `.env` est une cible make, produite depuis
le token de la user query lu dans le conteneur :

```
API_URL='http://localhost:8080/api/query.php?user=admin&f=html&t=<token>'
OUT_DIR='…/output'
```

`run` le source (`set -a; . .env`) et passe `--url` à `fetch_veille.py`. Les
valeurs sont quotées pour rester sourçables — le `&` de l'URL serait sinon
interprété par le shell. Le fichier n'est produit que s'il manque : après avoir
recréé le conteneur ou le volume, `rm .env` pour reprendre le token courant.

## Configuration

| Variable | Défaut |
| --- | --- |
| `VEILLE_IMAGE` / `VEILLE_CONTAINER` | `veille-freshrss` / `freshrss` |
| `VEILLE_PORT` / `VEILLE_BASE_URL` | `8080` / `http://localhost:$(VEILLE_PORT)` |
| `VEILLE_USER` / `VEILLE_PASSWORD` | `admin` / `veille` |
| `VEILLE_QUERY_NAME` | `last7days` |

```sh
make run VEILLE_PORT=8099 VEILLE_CONTAINER=freshrss-veille
```

`VEILLE_USER`, `VEILLE_PASSWORD` et `VEILLE_QUERY_NAME` partent au `docker
build` : les changer suppose un `make build` et un volume neuf. Le mot de passe
passe par un **secret BuildKit**, pas par un `ARG` — il n'apparaît donc ni dans
les couches ni dans `docker history`. Revers : les secrets ne comptent pas dans
la clé de cache, changer `VEILLE_PASSWORD` seul ne déclenche aucun rebuild
(`docker builder prune` ou `--no-cache`).

Le `Dockerfile` accepte en plus `FRESHRSS_LANGUAGE`, `VEILLE_SINCE_HOURS`
(fenêtre des exports HTML/RSS, 168 h), `VEILLE_MAX_POSTS` et
`VEILLE_QUERY_GET` / `_ORDER` / `_STATE` / `_SEARCH`.

## Fichiers

- `Dockerfile` — image FreshRSS provisionnée au build
- `docker/feeds.opml` — flux suivis, source de vérité de l'abonnement
- `docker/ensure_query.php` — crée ou relit la user query partagée, renvoie son token
- `src/fetch_veille.py` — pagination, déduplication des révisions, filtrage
- `src/keywords.json` — mots-clés par catégorie du gabarit de bulletin

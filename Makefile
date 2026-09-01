ROOT := $(patsubst %/,%,$(dir $(abspath $(lastword $(MAKEFILE_LIST)))))
SRC := $(ROOT)/src
OUT_DIR := $(ROOT)/output
ENV_FILE := $(ROOT)/.env

VEILLE_IMAGE ?= veille-freshrss
VEILLE_CONTAINER ?= freshrss
VEILLE_PORT ?= 8080
VEILLE_USER ?= admin
VEILLE_PASSWORD ?= veille
VEILLE_QUERY_NAME ?= last7days
# Taille de page demandée à query.php. L'instance plafonne à 400 entrées par
# réponse : fetch_veille.py avance de ce qui est servi, pas de ce qui est demandé.
VEILLE_NB ?= 1000
VEILLE_BASE_URL ?= http://localhost:$(VEILLE_PORT)

export VEILLE_PASSWORD
export FRSS_USER := $(VEILLE_USER)
export FRSS_QUERY_NAME := $(VEILLE_QUERY_NAME)

FRESHRSS_CLI := php /var/www/FreshRSS/cli

.DEFAULT_GOAL := run
.PHONY: help build up env run articles refresh clean

help: ## Liste les cibles
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F':.*## ' '{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

# `-q` : `run` déclenche un build à chaque appel, silencieux tant que le cache
# tient. Les erreurs de build restent affichées.
build: ## Construit l'image FreshRSS provisionnée
	@docker build -q -t $(VEILLE_IMAGE) \
		--secret id=freshrss_password,env=VEILLE_PASSWORD \
		--build-arg FRESHRSS_USER=$(VEILLE_USER) \
		--build-arg FRESHRSS_BASE_URL=$(VEILLE_BASE_URL) \
		--build-arg VEILLE_QUERY_NAME=$(VEILLE_QUERY_NAME) \
		$(ROOT) >/dev/null

up: build ## Démarre le conteneur, le crée au besoin
	@docker start $(VEILLE_CONTAINER) >/dev/null 2>&1 || { \
		docker run -d --name $(VEILLE_CONTAINER) \
			--restart unless-stopped --log-opt max-size=10m \
			-p $(VEILLE_PORT):80 \
			-v freshrss_data:/var/www/FreshRSS/data \
			-v freshrss_extensions:/var/www/FreshRSS/extensions \
			$(VEILLE_IMAGE) >/dev/null; \
		for i in 1 2 3 4 5 6 7 8 9 10; do \
			docker exec -u www-data $(VEILLE_CONTAINER) test -f data/config.php && break; \
			sleep 1; \
		done; \
	}

.ONESHELL:
env: up ## Régénère .env depuis le token courant de la user query
	@token=$$(
		docker exec \
		  -i -e FRSS_USER -e FRSS_QUERY_NAME \
		  $(VEILLE_CONTAINER) \
		  php /usr/local/share/veille/ensure_query.php
	) || exit $$?
	[ -n "$$token" ] || { echo "Token vide — le conteneur $(VEILLE_CONTAINER) répond-il ?" >&2; exit 1; }
	echo "API_URL='$(VEILLE_BASE_URL)/api/query.php?user=$(VEILLE_USER)&f=greader&nb=$(VEILLE_NB)&t=$$token'" > $(ENV_FILE)
	echo "OUT_DIR='$(OUT_DIR)'" >> $(ENV_FILE)

run: up refresh env ## Écrit output/candidates.json
	@set -a; . $(ENV_FILE); set +a; \
	uv run --project $(ROOT) python $(SRC)/fetch_veille.py \
		--url "$$API_URL" --nb $(VEILLE_NB) --out "$$OUT_DIR/candidates.json"

articles: run ## Extrait le texte des articles candidats dans output/articles
	@uv run --project $(ROOT) python $(SRC)/fetch_articles.py \
		--candidates "$(OUT_DIR)/candidates.json" --out-dir "$(OUT_DIR)/articles"

refresh: ## Force la récupération des flux dans FreshRSS
	@docker exec -u www-data $(VEILLE_CONTAINER) \
		$(FRESHRSS_CLI)/actualize-user.php --user=$(VEILLE_USER) >&2 || true

clean: ## Supprime les fichiers générés, le conteneur, les volumes et l'image
	@rm -rf $(OUT_DIR)/* $(ENV_FILE)
	@docker rm -f $(VEILLE_CONTAINER) >/dev/null 2>&1 || true
	@docker volume rm freshrss_data freshrss_extensions >/dev/null 2>&1 || true
	@docker rmi $(VEILLE_IMAGE) >/dev/null 2>&1 || true

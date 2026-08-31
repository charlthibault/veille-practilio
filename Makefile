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
VEILLE_BASE_URL ?= http://localhost:$(VEILLE_PORT)

export VEILLE_PASSWORD
export FRSS_USER := $(VEILLE_USER)
export FRSS_QUERY_NAME := $(VEILLE_QUERY_NAME)

FRESHRSS_CLI := php /var/www/FreshRSS/cli

.DEFAULT_GOAL := run
.PHONY: help build run clean

help: ## Liste les cibles
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) \
		| awk -F':.*## ' '{printf "  \033[36m%-8s\033[0m %s\n", $$1, $$2}'

build:
	docker build -t $(VEILLE_IMAGE) \
		--secret id=freshrss_password,env=VEILLE_PASSWORD \
		--build-arg FRESHRSS_USER=$(VEILLE_USER) \
		--build-arg FRESHRSS_BASE_URL=$(VEILLE_BASE_URL) \
		--build-arg VEILLE_QUERY_NAME=$(VEILLE_QUERY_NAME) \
		$(ROOT)

up: build
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
		echo "Premier démarrage — les flux de l'image sont vides, récupération…" >&2; \
		$(MAKE) --no-print-directory refresh; \
	}

.ONESHELL:
$(ENV_FILE):
	@token=$$(
		docker exec \
		  -i -e FRSS_USER -e FRSS_QUERY_NAME \
		  $(VEILLE_CONTAINER) \
		  php /usr/local/share/veille/ensure_query.php
	)
	echo "API_URL='$(VEILLE_BASE_URL)/api/query.php?user=$(VEILLE_USER)&f=html&t=$$token'" > $@
	echo "OUT_DIR='$(OUT_DIR)'" >> $@

run: $(ENV_FILE)
	@set -a; . $(ENV_FILE); set +a; \
	uv run --project $(ROOT) python $(SRC)/fetch_veille.py \
		--url "$$API_URL" --out "$$OUT_DIR/candidates.md"

refresh:
	@docker exec -u www-data $(VEILLE_CONTAINER) \
		$(FRESHRSS_CLI)/actualize-user.php --user=$(VEILLE_USER) >&2 || true

clean: ## Supprime les fichiers générés, le conteneur, les volumes et l'image
	@rm $(OUT_DIR)/* $(ENV_FILE)
	@docker rm -f $(VEILLE_CONTAINER) >/dev/null 2>&1 || true
	@docker volume rm freshrss_data freshrss_extensions >/dev/null 2>&1 || true
	@docker rmi $(VEILLE_IMAGE) >/dev/null 2>&1 || true

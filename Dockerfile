ARG FRESHRSS_BASE_IMAGE=freshrss/freshrss:latest
FROM ${FRESHRSS_BASE_IMAGE}

ARG FRESHRSS_USER=admin
ARG FRESHRSS_BASE_URL=http://localhost:8080
ARG FRESHRSS_LANGUAGE=en

ARG VEILLE_SINCE_HOURS=168
ARG VEILLE_MAX_POSTS=400

ARG VEILLE_QUERY_NAME=last7days
ARG VEILLE_QUERY_GET=a
ARG VEILLE_QUERY_ORDER=DESC
ARG VEILLE_QUERY_STATE=15
ARG VEILLE_QUERY_SEARCH=

WORKDIR /var/www/FreshRSS

COPY ./docker/feeds.opml ./docker/ensure_query.php /usr/local/share/veille/

RUN --mount=type=secret,id=freshrss_password,required=true \
	set -eu; \
	php ./cli/do-install.php \
		--default-user="${FRESHRSS_USER}" \
		--base-url="${FRESHRSS_BASE_URL}" \
		--language="${FRESHRSS_LANGUAGE}" \
		--auth-type=form \
		--api-enabled \
		--db-type=sqlite || [ $? -eq 3 ]; \
	php ./cli/create-user.php \
		--user="${FRESHRSS_USER}" \
		--password="$(cat /run/secrets/freshrss_password)" \
		--language="${FRESHRSS_LANGUAGE}" \
		--since-hours-posts-per-rss="${VEILLE_SINCE_HOURS}" \
		--max-posts-per-rss="${VEILLE_MAX_POSTS}" \
		--no-default-feeds || [ $? -eq 3 ]; \
	php ./cli/import-for-user.php \
		--user="${FRESHRSS_USER}" \
		--filename=/usr/local/share/veille/feeds.opml; \
	php ./cli/actualize-user.php --user=${FRESHRSS_USER}; \
	FRSS_USER="${FRESHRSS_USER}" \
	FRSS_QUERY_NAME="${VEILLE_QUERY_NAME}" \
	FRSS_QUERY_GET="${VEILLE_QUERY_GET}" \
	FRSS_QUERY_ORDER="${VEILLE_QUERY_ORDER}" \
	FRSS_QUERY_STATE="${VEILLE_QUERY_STATE}" \
	FRSS_QUERY_SEARCH="${VEILLE_QUERY_SEARCH}" \
	php /usr/local/share/veille/ensure_query.php; \
	./cli/access-permissions.sh

ENV TZ=Europe/Paris
ENV CRON_MIN=1,31

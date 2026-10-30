#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"

if [ ! -f .env ]; then
    umask 077
    secret() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }
    {
        printf 'POSTGRES_PASSWORD=%s\n' "$(secret)"
        printf 'DJANGO_SECRET_KEY=%s\n' "$(secret)"
        printf 'OUTBOX_INTERNAL_TOKEN=%s\n' "$(secret)"
    } > .env
    printf '%s\n' 'Created local .env with random credentials.'
fi

docker compose up --build

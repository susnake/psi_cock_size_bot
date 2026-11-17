#!/bin/sh
set -e

if [ "$(id -u)" = "0" ]; then
    chown -R bot:bot /app/data
    exec setpriv --reuid=bot --regid=bot --init-groups "$@"
fi

exec "$@"

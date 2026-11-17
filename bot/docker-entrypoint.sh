#!/bin/sh
# Короткая фаза от root, затем сброс привилегий до пользователя bot (uid 1000).
#
# Зачем root: файлы Docker secrets на хосте имеют права 600 root, compose (не swarm)
# монтирует их с этими правами, и uid 1000 их не прочитает. Поэтому:
#   1) читаем секреты от root и кладём в окружение процесса (не на диск, не в аргументы);
#   2) исправляем владельца файлов в /app/data (старые volume создавались от root);
#   3) exec setpriv: дальше работает только bot, без возможности вернуть права.
# Никаких данных извне в этой фазе нет; eval не используется.
set -eu

SECRETS_DIR="${SECRETS_DIR:-/run/secrets}"

if [ "$(id -u)" = "0" ]; then
    # 1. Секреты → переменные окружения. Имя переменной = имя файла.
    if [ -d "$SECRETS_DIR" ]; then
        for f in "$SECRETS_DIR"/*; do
            [ -f "$f" ] || continue
            name=$(basename "$f")
            case "$name" in
                ''|[0-9]*|*[!A-Za-z0-9_]*)
                    echo "entrypoint: пропускаю секрет с недопустимым именем" >&2
                    continue ;;
            esac
            value=$(cat "$f")        # $(...) срезает завершающие переводы строк
            export "$name=$value"
        done
        unset f name value
    fi

    # 2. Владелец /app/data. Не chown -R: find -xdev не выходит за пределы volume,
    #    chown -h меняет саму символьную ссылку и никогда не идёт по ней.
    if [ -d /app/data ]; then
        find /app/data -xdev ! -user bot -exec chown -h bot:bot {} +
    fi

    # 3. Сброс привилегий. --no-new-privs: setuid-бинарники уже не дадут вернуть права.
    exec setpriv --reuid=bot --regid=bot --init-groups --no-new-privs "$@"
fi

exec "$@"

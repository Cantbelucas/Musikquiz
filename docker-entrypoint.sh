#!/bin/sh
# Sikrer at /data ejes af app-brugeren (uid 10001) og starter derefter
# kommandoen (gunicorn) som den bruger — aldrig som root.
set -eu

APP_UID=10001
APP_GID=10001
DATA_DIR="${DATA_DIR:-/data}"

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR/media"

    # Docker opretter ./data som root, hvis mappen ikke findes på værten.
    # Ret kun ejerskab hvis noget faktisk er forkert (undgår chown af alle filer ved hver start).
    if [ -n "$(find "$DATA_DIR" \( ! -user "$APP_UID" -o ! -group "$APP_GID" \) -print -quit)" ]; then
        echo "docker-entrypoint: retter ejerskab af $DATA_DIR til $APP_UID:$APP_GID"
        chown -R "$APP_UID:$APP_GID" "$DATA_DIR"
    fi

    # Drop root: sæt gid, fjern supplerende grupper, sæt uid, og exec kommandoen.
    exec python -c '
import os, sys
os.setgroups([])
os.setgid(int(sys.argv[2]))
os.setuid(int(sys.argv[1]))
if os.getuid() == 0 or os.geteuid() == 0:
    sys.exit("docker-entrypoint: kunne ikke droppe root")
os.execvp(sys.argv[3], sys.argv[3:])
' "$APP_UID" "$APP_GID" "$@"
fi

# Allerede ikke-root (fx `user: "10001:10001"` i compose)
exec "$@"

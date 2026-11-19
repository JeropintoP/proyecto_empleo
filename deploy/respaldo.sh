#!/usr/bin/env bash
# Copia de seguridad diaria de la base de datos.
#  1. pg_dump comprimido y CIFRADO (gpg, AES-256) con la clave RESPALDO_CLAVE del .env.
#  2. Se conservan 14 dias en el servidor.
#  3. Si RESPALDO_DESTINO esta definido, se sube con rclone a un almacenamiento externo
#     (Backblaze B2: 10 GB gratis, sin tarjeta) y alla se conservan 30 dias.
#     Asi, si el servidor se pierde, los datos no se pierden con el.
#
# Cron (5:30 a.m., antes de la actualizacion de las 6):
#   30 5 * * * /home/ubuntu/proyecto_empleo/deploy/respaldo.sh >> /home/ubuntu/respaldos/respaldo.log 2>&1
#
# Restaurar:
#   gpg --batch --pinentry-mode loopback --passphrase "$RESPALDO_CLAVE" -d proyecto_empleo_FECHA.sql.gz.gpg | gunzip \
#     | docker compose exec -T db psql -U empleo -d proyecto_empleo
set -euo pipefail
cd "$(dirname "$0")/.."
DESTINO="$HOME/respaldos"
mkdir -p "$DESTINO"
set -a; source .env; set +a

: "${RESPALDO_CLAVE:?Defina RESPALDO_CLAVE en el .env (openssl rand -hex 32) y guardela tambien fuera del servidor}"

ARCHIVO="$DESTINO/proyecto_empleo_$(date +%F).sql.gz.gpg"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner \
  | gzip \
  | gpg --batch --yes --pinentry-mode loopback --symmetric --cipher-algo AES256 --passphrase-fd 3 -o "$ARCHIVO" 3<<<"$RESPALDO_CLAVE"
find "$DESTINO" -name 'proyecto_empleo_*.sql.gz*' -mtime +14 -delete
echo "$(date '+%F %T') respaldo local ok: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"

if [ -n "${RESPALDO_DESTINO:-}" ]; then
  rclone copy "$ARCHIVO" "$RESPALDO_DESTINO"
  rclone delete "$RESPALDO_DESTINO" --min-age 30d
  echo "$(date '+%F %T') copia externa ok: $RESPALDO_DESTINO"
fi

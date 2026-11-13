#!/usr/bin/env bash
# Copia de seguridad diaria de la base de datos. Conserva 14 dias.
# Cron (5:30 a.m., antes de la actualizacion de las 6):
#   30 5 * * * /home/ubuntu/proyecto_empleo/deploy/respaldo.sh >> /home/ubuntu/respaldos/respaldo.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
DESTINO="$HOME/respaldos"
mkdir -p "$DESTINO"
set -a; source .env; set +a

ARCHIVO="$DESTINO/proyecto_empleo_$(date +%F).sql.gz"
docker compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner | gzip > "$ARCHIVO"
find "$DESTINO" -name 'proyecto_empleo_*.sql.gz' -mtime +14 -delete
echo "$(date '+%F %T') respaldo ok: $ARCHIVO ($(du -h "$ARCHIVO" | cut -f1))"

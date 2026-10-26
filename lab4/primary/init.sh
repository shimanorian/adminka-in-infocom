#!/bin/bash
set -e
# пользователь для репликации
psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c \
  "CREATE ROLE replicator WITH REPLICATION LOGIN PASSWORD 'replpass';"
# разрешаем реплике подключаться для репликации
echo "host replication replicator all scram-sha-256" >> "$PGDATA/pg_hba.conf"

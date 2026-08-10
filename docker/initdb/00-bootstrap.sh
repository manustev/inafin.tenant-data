#!/bin/bash
# Dev-stack wrapper around bootstrap/00_cluster.sql.
# Runs inside the postgres container on first init, against POSTGRES_DB (tenant_db).
# Production runs the .sql directly — this file exists only to feed it env vars.
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
     --username "$POSTGRES_USER" \
     --dbname "$POSTGRES_DB" \
     -v migrate_pw="${TENANT_MIGRATE_PASSWORD}" \
     -v app_pw="${APP_LOGIN_PASSWORD}" \
     -f /bootstrap/00_cluster.sql

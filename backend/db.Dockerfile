# Tiny custom Postgres image that bakes schema.sql into the init scripts.
# Avoids needing a host bind mount for the schema, which trips macOS
# Desktop/Documents file-access protections.
FROM postgres:16-alpine
COPY schema.sql /docker-entrypoint-initdb.d/01-schema.sql

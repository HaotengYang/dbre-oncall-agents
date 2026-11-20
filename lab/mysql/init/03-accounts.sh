# Creates the lab's four accounts. Run by the MySQL image's entrypoint during
# first start, after the schema and seed scripts.
#
# The four accounts never share privileges:
#   shop_app       the synthetic workload: read and write rows in shop
#   shop_diag      diagnosis agents: read-only, least privilege
#   shop_exporter  metrics exporter: server status only
#   shop_gate      change safety gate: add or drop an index in shop, and full
#                  control of the separate shadow database, nothing else
#
# This file is kept non-executable so the entrypoint sources it. It must not
# call "exit" or change shell options, or it would affect the entrypoint.

lab_accounts_ready=yes
for lab_password_name in APP_DB_PASSWORD DIAG_DB_PASSWORD EXPORTER_DB_PASSWORD GATE_DB_PASSWORD; do
  lab_password_value="$(printenv "$lab_password_name" || true)"
  case "$lab_password_value" in
    "")
      echo "lab accounts: $lab_password_name is empty" >&2
      lab_accounts_ready=no
      ;;
    *[!A-Za-z0-9_-]*)
      # Passwords are placed inside SQL string literals below. Restricting the
      # alphabet removes any need to escape them.
      echo "lab accounts: $lab_password_name may only contain letters, digits, - and _" >&2
      lab_accounts_ready=no
      ;;
  esac
done
unset lab_password_name lab_password_value

if [ "$lab_accounts_ready" != yes ]; then
  echo "lab accounts: refusing to start with invalid account passwords" >&2
  return 1 2>/dev/null || exit 1
fi
unset lab_accounts_ready

# Written only after every init step has succeeded. The container health
# check requires it, so a data volume left behind by a failed first start can
# never be reported as healthy. It lives in the data volume, so it is removed
# together with the data by "make reset".
lab_init_marker=/var/lib/mysql/lab-init-complete

if mysql --protocol=socket -uroot -p"${MYSQL_ROOT_PASSWORD}" <<SQL
CREATE DATABASE shop_shadow;

CREATE USER 'shop_app'@'%' IDENTIFIED BY '${APP_DB_PASSWORD}';
GRANT SELECT, INSERT, UPDATE, DELETE ON shop.* TO 'shop_app'@'%';

-- PROCESS: see other sessions' statements and read INNODB_TRX.
-- SELECT on shop: EXPLAIN needs the privileges the statement itself needs.
CREATE USER 'shop_diag'@'%' IDENTIFIED BY '${DIAG_DB_PASSWORD}';
GRANT PROCESS ON *.* TO 'shop_diag'@'%';
GRANT SELECT ON performance_schema.* TO 'shop_diag'@'%';
GRANT SELECT ON shop.* TO 'shop_diag'@'%';

CREATE USER 'shop_exporter'@'%' IDENTIFIED BY '${EXPORTER_DB_PASSWORD}'
  WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'shop_exporter'@'%';
GRANT SELECT ON performance_schema.* TO 'shop_exporter'@'%';

-- INDEX covers CREATE INDEX and DROP INDEX and nothing else. SELECT lets the
-- gate copy a sample into the shadow database and explain queries.
CREATE USER 'shop_gate'@'%' IDENTIFIED BY '${GATE_DB_PASSWORD}';
GRANT SELECT, INDEX ON shop.* TO 'shop_gate'@'%';
GRANT CREATE, DROP, ALTER, INDEX, SELECT, INSERT ON shop_shadow.* TO 'shop_gate'@'%';
SQL
then
  touch "$lab_init_marker"
  unset lab_init_marker
else
  echo "lab accounts: creating accounts failed" >&2
  return 1 2>/dev/null || exit 1
fi

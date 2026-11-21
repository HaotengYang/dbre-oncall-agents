# Creates the lab's four accounts. Run by the MySQL image's entrypoint during
# first start, after the schema and seed scripts.
#
# The accounts never share privileges:
#   shop_app       the synthetic workload: read and write rows in shop
#   shop_diag      diagnosis agents: read-only, least privilege
#   shop_exporter  metrics exporter: server status only
#   shop_gate      change safety gate: add or drop an index in shop, and full
#                  control of the separate shadow database, nothing else
#   shop_fault     fault injector: add or drop an index in shop (to create and
#                  undo schema faults) and read the lab marker, nothing else
#
# It also creates the lab marker, lab_meta.environment. The fault injector
# refuses to act on any server that does not carry it.
#
# This file is kept non-executable so the entrypoint sources it. It must not
# call "exit" or change shell options, or it would affect the entrypoint.

lab_accounts_ready=yes
for lab_password_name in APP_DB_PASSWORD DIAG_DB_PASSWORD EXPORTER_DB_PASSWORD GATE_DB_PASSWORD FAULT_DB_PASSWORD; do
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
-- May connect to the administrative port, which has no connection limit.
-- Measured side effect (2026-11-21): the same privilege also lets the account
-- take the one extra connection MySQL keeps in reserve on the normal port
-- when max_connections is reached. Still no writes and no session control.
GRANT SERVICE_CONNECTION_ADMIN ON *.* TO 'shop_diag'@'%';

-- The same read-only account without any administrative channel, for
-- exercising diagnosis on an installation that has none: when connections are
-- exhausted this account is locked out like everyone else.
CREATE USER 'shop_diag_noadmin'@'%' IDENTIFIED BY '${DIAG_DB_PASSWORD}';
GRANT PROCESS ON *.* TO 'shop_diag_noadmin'@'%';
GRANT SELECT ON performance_schema.* TO 'shop_diag_noadmin'@'%';
GRANT SELECT ON shop.* TO 'shop_diag_noadmin'@'%';

CREATE USER 'shop_exporter'@'%' IDENTIFIED BY '${EXPORTER_DB_PASSWORD}'
  WITH MAX_USER_CONNECTIONS 3;
GRANT PROCESS, REPLICATION CLIENT ON *.* TO 'shop_exporter'@'%';
GRANT SELECT ON performance_schema.* TO 'shop_exporter'@'%';

-- INDEX covers CREATE INDEX and DROP INDEX and nothing else. SELECT lets the
-- gate copy a sample into the shadow database and explain queries.
CREATE USER 'shop_gate'@'%' IDENTIFIED BY '${GATE_DB_PASSWORD}';
-- The underscore is escaped: in a database-level grant an unescaped "_" is a
-- one-character wildcard and would also match names such as "shopXshadow".
GRANT SELECT, INDEX ON shop.* TO 'shop_gate'@'%';
GRANT CREATE, DROP, ALTER, INDEX, SELECT, INSERT ON \`shop\\_shadow\`.* TO 'shop_gate'@'%';

CREATE DATABASE lab_meta;
CREATE TABLE lab_meta.environment (name VARCHAR(64) NOT NULL PRIMARY KEY);
INSERT INTO lab_meta.environment (name) VALUES ('dbre-lab');

-- No SELECT on shop: the injector's schema account cannot read or change rows.
CREATE USER 'shop_fault'@'%' IDENTIFIED BY '${FAULT_DB_PASSWORD}';
GRANT INDEX ON shop.* TO 'shop_fault'@'%';
GRANT SELECT ON \`lab\\_meta\`.* TO 'shop_fault'@'%';
SQL
then
  touch "$lab_init_marker"
  unset lab_init_marker
else
  echo "lab accounts: creating accounts failed" >&2
  return 1 2>/dev/null || exit 1
fi

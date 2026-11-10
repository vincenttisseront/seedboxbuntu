#!/usr/bin/env bash
# Tests sans Docker ni droits root : contrôles statiques, sauvegarde, alertes, catalogue et versions,
# rôles et règles Authelia, mise à jour N-1 -> N, durcissement, migration.
# Usage : tests/run.sh          (depuis la racine des sources)
set -u
cd "$(dirname "$0")/.."
unset HTTPS_PROXY HTTP_PROXY https_proxy http_proxy
fail=0
for t in static_check backup_test alerts_test catalog2_test roles_test upgrade_test appliance_test hardening_unit_test migrate_test; do
  printf '%-24s' "$t"
  if out=$(timeout 300 python3 "tests/$t.py" 2>&1); then echo "OK   $(tail -1 <<<"$out")"; else echo "ÉCHEC"; echo "$out" | tail -25; fail=1; fi
done
exit $fail

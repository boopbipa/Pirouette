#!/usr/bin/env bash
# Double-clique sur ce fichier dans le Finder pour lancer Pirouette.
cd "$(dirname "$0")"
./run.sh "$@"
status=$?
if [ $status -ne 0 ]; then
  echo ""
  read -r -p "Une erreur est survenue (voir ci-dessus). Appuie sur Entrée pour fermer…"
fi
exit $status

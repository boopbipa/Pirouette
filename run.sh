#!/usr/bin/env bash
# Lance Pirouette sur http://localhost:8000
# Usage : ./run.sh            (moteur local présélectionné)
#         ./run.sh claude     (moteur Claude présélectionné)
set -euo pipefail
cd "$(dirname "$0")"

# --- 1. Trouver un Python >= 3.10 (le Python fourni par macOS est souvent en 3.9) ---
PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3.10 python3 \
                 /opt/homebrew/bin/python3 /usr/local/bin/python3; do
  if command -v "$candidate" >/dev/null 2>&1 &&
     "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
    PYTHON="$(command -v "$candidate")"
    break
  fi
done

if [ -z "$PYTHON" ]; then
  echo ""
  echo "✗ Il faut Python 3.10 ou plus récent (version trouvée : $(python3 --version 2>&1 || echo 'aucune'))."
  echo ""
  echo "  Installe-le avec Homebrew :"
  echo "    /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
  echo "    brew install python@3.12"
  echo ""
  echo "  (ou télécharge l'installateur macOS sur https://www.python.org/downloads/)"
  echo "  Puis relance ./run.sh"
  exit 1
fi
echo "✓ Python : $("$PYTHON" --version)"

# --- 2. Environnement virtuel (recréé s'il a été fait avec un Python trop ancien) ---
if [ -d .venv ] && ! .venv/bin/python -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
  echo "→ Ancien environnement incompatible, recréation…"
  rm -rf .venv
fi
if [ ! -d .venv ]; then
  echo "→ Création de l'environnement Python…"
  "$PYTHON" -m venv .venv
fi
echo "→ Installation des dépendances (la première fois, ça prend une minute)…"
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt
echo "✓ Dépendances installées"

[ -f .env ] || cp .env.example .env

case "${1:-}" in
  claude) export QUIZZ_DEFAULT_PROVIDER=claude ;;
  local)  export QUIZZ_DEFAULT_PROVIDER=local ;;
esac

# --- 3. Vérifications non bloquantes ---
if ! curl -s -o /dev/null --max-time 2 http://localhost:11434/api/tags; then
  echo "ℹ︎ Ollama ne répond pas : la version locale sera indisponible (ouvre l'app Ollama pour l'activer)."
fi
if ! grep -qE '^ANTHROPIC_API_KEY=sk-ant-[A-Za-z0-9_-]{10,}' .env && [ -z "${ANTHROPIC_API_KEY:-}" ]; then
  echo "ℹ︎ Pas de clé API Claude dans .env : la version Claude sera indisponible."
fi

if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "✗ Le port 8000 est déjà utilisé (Pirouette tourne peut-être déjà dans une autre fenêtre)."
  echo "  Ouvre http://localhost:8000 ou ferme l'autre fenêtre du Terminal."
  exit 1
fi

echo ""
echo "✓ Pirouette démarre sur http://localhost:8000"
echo "  Laisse cette fenêtre ouverte pendant que tu utilises l'app (Ctrl+C pour l'arrêter)."
echo ""
( sleep 2 && open http://localhost:8000 2>/dev/null || true ) &
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000

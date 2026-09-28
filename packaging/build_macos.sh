#!/usr/bin/env bash
# Fabrique Pirouette.app puis Pirouette-<version>-<Apple-Silicon|Intel>.dmg.
# À lancer sur un Mac (c'est ce que fait GitHub Actions), après : pip install -r requirements-desktop.txt
set -euo pipefail
cd "$(dirname "$0")/.."

PY="${PYTHON:-python3}"
VERSION=$("$PY" -c "import re; print(re.search(r'__version__ = \"(.+)\"', open('app/__init__.py').read()).group(1))")
case "$(uname -m)" in
  arm64) ARCH_LABEL="Apple-Silicon" ;;
  *)     ARCH_LABEL="Intel" ;;
esac

echo "→ Icône"
ICONSET="build/Pirouette.iconset"
rm -rf "$ICONSET" && mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" packaging/icon-1024.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size * 2))
  sips -z "$double" "$double" packaging/icon-1024.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/Pirouette.icns

echo "→ Application"
"$PY" -m PyInstaller --noconfirm --clean packaging/Pirouette.spec

echo "→ Signature ad hoc (sans certificat Apple ; indispensable sur Apple Silicon)"
codesign --force --deep --sign - dist/Pirouette.app
codesign --verify --deep dist/Pirouette.app

echo "→ Image disque"
STAGE="build/dmg"
rm -rf "$STAGE" && mkdir -p "$STAGE"
cp -R dist/Pirouette.app "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp packaging/LISEZ-MOI.txt "$STAGE/Lisez-moi.txt"
DMG="dist/Pirouette-${VERSION}-${ARCH_LABEL}.dmg"
# hdiutil échoue parfois (« Resource busy ») sur les Mac de GitHub : on réessaie quelques fois.
for attempt in 1 2 3 4; do
  if hdiutil create -volname "Pirouette" -srcfolder "$STAGE" -ov -format UDZO "$DMG"; then break; fi
  if [ "$attempt" = 4 ]; then exit 1; fi
  echo "hdiutil a échoué, nouvel essai dans $((attempt * 5)) s…"
  sleep $((attempt * 5))
done
echo "✓ $DMG"

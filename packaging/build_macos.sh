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

# Signature. Avec le certificat de Pirouette (secret MAC_SIGN_P12) : la même signature d'une version à l'autre, et
# macOS se souvient des autorisations données (Calendrier, Pages…) après une mise à jour. Sans lui : signature
# « ad hoc », différente à chaque version (macOS redemande alors les autorisations).
sign_with_certificate() {
  KEYCHAIN="$PWD/build/signature.keychain-db"
  KEYPASS="$(uuidgen)"
  rm -f "$KEYCHAIN"
  # Le secret collé dans GitHub peut contenir des espaces ou des retours à la ligne : on ne garde que le base64
  python3 -c "import base64, os, re, sys
raw = re.sub(r'[^A-Za-z0-9+/=]', '', os.environ['MAC_SIGN_P12'])
print(f'  secret : {len(raw)} caractères', file=sys.stderr)
open('build/signature.p12', 'wb').write(base64.b64decode(raw + '=' * (-len(raw) % 4)))" || return 1
  security create-keychain -p "$KEYPASS" "$KEYCHAIN" || return 1
  security set-keychain-settings -lut 3600 "$KEYCHAIN"
  security unlock-keychain -p "$KEYPASS" "$KEYCHAIN" || return 1
  PASS="$(printf %s "${MAC_SIGN_PASSWORD:-}" | tr -d '[:space:]')"
  security import build/signature.p12 -k "$KEYCHAIN" -P "$PASS" -T /usr/bin/codesign || { rm -f build/signature.p12; return 1; }
  rm -f build/signature.p12
  security set-key-partition-list -S apple-tool:,apple: -s -k "$KEYPASS" "$KEYCHAIN" >/dev/null || return 1
  security list-keychains -d user -s "$KEYCHAIN" $(security list-keychains -d user | tr -d '"')
  codesign --force --deep --keychain "$KEYCHAIN" --sign "Pirouette" dist/Pirouette.app
}
if [ -n "${MAC_SIGN_P12:-}" ] && { echo "→ Signature avec le certificat de Pirouette"; sign_with_certificate; }; then
  :
else
  # Sans certificat (ou secret illisible) : signature ad hoc, pour publier quand même
  [ -n "${MAC_SIGN_P12:-}" ] && echo "::warning title=Signature::certificat de Pirouette illisible (secrets MAC_SIGN_P12 / MAC_SIGN_PASSWORD), signature ad hoc"
  echo "→ Signature ad hoc (sans certificat ; indispensable sur Apple Silicon)"
  codesign --force --deep --sign - dist/Pirouette.app
fi
codesign --verify --deep dist/Pirouette.app
codesign -dr - dist/Pirouette.app 2>&1 | tail -1  # l'exigence que macOS retient pour les autorisations

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

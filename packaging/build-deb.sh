#!/bin/bash
# Build deskdora_1.0.0_amd64.deb from this source tree.
#
#   packaging/build-deb.sh [output-dir]
#
# The package is fully offline-installable: DeskDora's Python
# dependencies are bundled as wheels under /opt/deskdora/wheelhouse and
# installed into a private virtualenv by the postinst script.  System
# dependencies (AT-SPI, xdotool, scrot, ...) are declared in the
# control file and resolved by apt/dpkg as usual.
#
# Wheels are downloaded at *build* time only (pip download, pinned by
# packaging/requirements-deb.txt).  Set WHEELHOUSE=/path/to/wheels to
# reuse a pre-downloaded wheelhouse and build fully offline.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
PKG="$REPO/packaging"
OUT="${1:-$PKG/dist}"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$REPO/pyproject.toml" | head -1)"
DEB_NAME="deskdora_${VERSION}_amd64.deb"

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"

echo "==> staging $DEB_NAME in $STAGE"

# ---- payload: application source tree -------------------------------------
APP="$STAGE/opt/deskdora/app"
mkdir -p "$APP"
cp -r "$REPO/src" "$REPO/fixtures" "$APP/"
cp "$REPO/README.md" "$REPO/LICENSE" "$REPO/pyproject.toml" "$APP/"
find "$APP" -name __pycache__ -type d -prune -exec rm -rf {} +
rm -rf "$APP/src/deskdora.egg-info"
cp "$PKG/requirements-deb.txt" "$STAGE/opt/deskdora/requirements.txt"

# ---- payload: offline wheelhouse -------------------------------------------
WH="$STAGE/opt/deskdora/wheelhouse"
mkdir -p "$WH"
if [ -n "${WHEELHOUSE:-}" ]; then
  cp "$WHEELHOUSE"/*.whl "$WH/"
else
  python3 -m pip download -r "$PKG/requirements-deb.txt" -d "$WH" >/dev/null
fi
echo "    wheels: $(ls "$WH" | wc -l)"

# ---- launchers --------------------------------------------------------------
mkdir -p "$STAGE/usr/bin"
install -m 0755 "$PKG/bin/deskdora" "$STAGE/usr/bin/deskdora"
install -m 0755 "$PKG/bin/deskdora-launcher" "$STAGE/usr/bin/deskdora-launcher"

# ---- desktop entry + icons ----------------------------------------------------
mkdir -p "$STAGE/usr/share/applications"
install -m 0644 "$PKG/deskdora.desktop" "$STAGE/usr/share/applications/deskdora.desktop"
for S in 48 128 256 512; do
  D="$STAGE/usr/share/icons/hicolor/${S}x${S}/apps"
  mkdir -p "$D"
  install -m 0644 "$PKG/icons/deskdora-$S.png" "$D/deskdora.png"
done
D="$STAGE/usr/share/icons/hicolor/scalable/apps"
mkdir -p "$D"
install -m 0644 "$REPO/src/deskdora/static/logo.svg" "$D/deskdora.svg"

# ---- control + maintainer scripts ---------------------------------------------
mkdir -p "$STAGE/DEBIAN"
SIZE_KB="$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)"
sed "s/^Version:.*/Version: $VERSION/" "$PKG/control" > "$STAGE/DEBIAN/control"
echo "Installed-Size: $SIZE_KB" >> "$STAGE/DEBIAN/control"
install -m 0755 "$PKG/postinst" "$STAGE/DEBIAN/postinst"
install -m 0755 "$PKG/prerm"    "$STAGE/DEBIAN/prerm"
install -m 0755 "$PKG/postrm"   "$STAGE/DEBIAN/postrm"

# ---- build ----------------------------------------------------------------------
# Normalise modes: the payload must be world-readable/executable or the
# desktop user cannot run the app out of /opt.
chmod -R a+rX "$STAGE/opt" "$STAGE/usr"
chmod 0755 "$STAGE" "$STAGE/DEBIAN"
dpkg-deb --build --root-owner-group "$STAGE" "$OUT/$DEB_NAME"
echo "==> built $OUT/$DEB_NAME"

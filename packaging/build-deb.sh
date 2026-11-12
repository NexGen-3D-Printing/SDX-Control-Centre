#!/usr/bin/env bash
# Build dist/sdx-control-centre_<version>_all.deb (pure Python, so architecture "all").
# Needs only dpkg-deb. Run from anywhere: ./packaging/build-deb.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$here"
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/surface_companion/__init__.py)"
[[ -n $version ]] || { echo "could not read version" >&2; exit 1; }
pkg=sdx-control-centre
maint="$(git config user.name 2>/dev/null || echo 'NexGen3D') <$(git config user.email 2>/dev/null || echo 'nexgen3d@example.com')>"
out="dist/${pkg}_${version}_all.deb"
stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
root="$stage/root"

d() { install -d -m 755 "$root/$1"; }
f() { install -D -m "${3:-644}" "$here/$1" "$root/$2"; }

d usr/lib/sdx-control-centre
cp -r src/surface_companion "$root/usr/lib/sdx-control-centre/"
find "$root" -name __pycache__ -type d -prune -exec rm -rf {} +
find "$root/usr/lib/sdx-control-centre" -type f -exec chmod 644 {} +

install -D -m 755 /dev/stdin "$root/usr/bin/sdx-control-centre" <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/sdx-control-centre exec python3 -m surface_companion "$@"
W
install -D -m 755 /dev/stdin "$root/usr/bin/sdx-control-centred" <<'W'
#!/bin/sh
PYTHONPATH=/usr/lib/sdx-control-centre exec python3 -m surface_companion.daemon.service "$@"
W

f data/org.sdxcontrolcentre.policy usr/share/polkit-1/actions/org.sdxcontrolcentre.policy
f data/org.sdxcontrolcentre.Daemon.conf usr/share/dbus-1/system.d/org.sdxcontrolcentre.Daemon.conf
f data/org.sdxcontrolcentre.Daemon.service usr/share/dbus-1/system-services/org.sdxcontrolcentre.Daemon.service
f data/sdx-control-centre.service usr/lib/systemd/system/sdx-control-centre.service
f data/sdx-control-centre.desktop usr/share/applications/sdx-control-centre.desktop
f data/sdx-control-centre-autostart.desktop etc/xdg/autostart/sdx-control-centre.desktop
f data/sdx-control-centre.svg usr/share/icons/hicolor/scalable/apps/sdx-control-centre.svg
f README.md usr/share/doc/$pkg/README.md

# Debian changelog, taken from CHANGELOG.md's newest section
install -d -m 755 "$root/usr/share/doc/$pkg"
{
  echo "$pkg ($version) unstable; urgency=low"
  echo
  awk '/^## /{n++; next} n==1 && NF {print}' CHANGELOG.md | sed 's/^- /* /' | fold -s -w 74 | sed 's/^/  /; s/ *$//'
  echo
  echo " -- $maint  $(date -R)"
} | gzip -9n > "$root/usr/share/doc/$pkg/changelog.gz"
chmod 644 "$root/usr/share/doc/$pkg/changelog.gz"

cat > "$root/usr/share/doc/$pkg/copyright" <<C
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: SDX Control Centre

Files: *
Copyright: 2026 SDX Control Centre contributors
License: GPL-3.0
 This program is free software: you can redistribute it and/or modify
 it under the terms of the GNU General Public License as published by
 the Free Software Foundation, version 3.
 .
 This program is distributed in the hope that it will be useful,
 but WITHOUT ANY WARRANTY; without even the implied warranty of
 MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 GNU General Public License for more details.
C
chmod 644 "$root/usr/share/doc/$pkg/copyright"

find "$root" -type d -exec chmod 755 {} +      # install -D leaves parents group-writable under some umasks
find "$root/usr/lib/sdx-control-centre" -type f -exec chmod 644 {} +

# ---- control files
ctl="$root/DEBIAN"
install -d -m 755 "$ctl"
size="$(du -sk --exclude=DEBIAN "$root" | cut -f1)"
cat > "$ctl/control" <<C
Package: $pkg
Version: $version
Section: utils
Priority: optional
Architecture: all
Maintainer: $maint
Installed-Size: $size
Depends: python3 (>= 3.10), python3-gi, gir1.2-glib-2.0, python3-pyqt6, dbus, polkitd | policykit-1, systemd
Recommends: thermald
Suggests: iptsd, libcamera-tools
Description: Control and fix Microsoft Surface hardware on Linux
 One window for brightness, keyboard backlight, performance profile, sleep
 and wake, battery, temperatures and fans, touch and pen, cameras and the
 Surface Book detach latch. Also lists the linux-surface project's documented
 workarounds for your model, and installs and updates the linux-surface
 kernel and drivers.
 .
 A small root service applies changes and asks polkit before each one. The
 window looks the same on every desktop environment.
C
echo "/etc/xdg/autostart/sdx-control-centre.desktop" > "$ctl/conffiles"

cat > "$ctl/postinst" <<'S'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    python3 -m compileall -q /usr/lib/sdx-control-centre >/dev/null 2>&1 || true
    gtk-update-icon-cache -q /usr/share/icons/hicolor 2>/dev/null || true
    update-desktop-database -q /usr/share/applications 2>/dev/null || true
    if [ -d /run/systemd/system ]; then
        systemctl disable --now surface-companion.service 2>/dev/null || true
        systemctl daemon-reload || true
        systemctl reload dbus 2>/dev/null || systemctl reload dbus-broker 2>/dev/null || true
        systemctl enable sdx-control-centre.service || true
        systemctl restart sdx-control-centre.service || true
    fi
fi
exit 0
S
cat > "$ctl/prerm" <<'S'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = deconfigure ]; then
    if [ -d /run/systemd/system ]; then
        systemctl disable --now sdx-control-centre.service 2>/dev/null || true
        systemctl disable --now surface-companion.service 2>/dev/null || true
    fi
fi
exit 0
S
cat > "$ctl/postrm" <<'S'
#!/bin/sh
set -e
case "$1" in
    remove|purge)
        rm -rf /usr/lib/sdx-control-centre /usr/lib/surface-companion
        [ -d /run/systemd/system ] && systemctl daemon-reload 2>/dev/null || true
        ;;
esac
if [ "$1" = purge ]; then
    rm -rf /var/lib/sdx-control-centre /var/lib/surface-companion
fi
exit 0
S
chmod 755 "$ctl/postinst" "$ctl/prerm" "$ctl/postrm"

( cd "$root" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort | xargs md5sum > DEBIAN/md5sums )
chmod 644 "$ctl/md5sums"

mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$root" "$out" >/dev/null
( cd dist && sha256sum "$(basename "$out")" > "$(basename "$out").sha256" )
echo "Built $out"
cat "$out.sha256"

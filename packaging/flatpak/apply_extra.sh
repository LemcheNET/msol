#!/usr/bin/sh
# Runs when Flatpak installs or updates msol, in /app/extra, after Flatpak has
# downloaded and checked edge.deb: unpack Edge from Microsoft's package.
set -e
bsdtar -Oxf edge.deb 'data.tar*' |
  bsdtar -xf - --strip-components=4 ./opt/microsoft/msedge
rm edge.deb
# Chromium's setuid sandbox helper cannot work in Flatpak; zypak replaces it.
install -Dm755 /app/bin/stub_sandbox msedge-sandbox

#!/usr/bin/sh
# Runs when Flatpak installs or updates msol, in /app/extra, after Flatpak has
# downloaded and checked edge.deb: unpack Edge from Microsoft's package.
set -e
export LC_ALL=C
# Unpack the inner archive to a file first: reading it from a pipe makes bsdtar
# print "lseek error" warnings.
bsdtar -xf edge.deb 'data.tar*'
bsdtar -xf data.tar* --strip-components=4 ./opt/microsoft/msedge
rm -f edge.deb data.tar* control.tar* debian-binary
# Chromium's setuid sandbox helper cannot work in Flatpak; zypak replaces it.
install -Dm755 /app/bin/stub_sandbox msedge-sandbox

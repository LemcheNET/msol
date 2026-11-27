#!/usr/bin/env bash
# Builds the msol APT repository from .deb files.
#   build-repo.sh <dir with .debs> <output dir>
# Signs it when APT_SIGNING_KEY (an armored private key) is set.
set -euo pipefail
debs="$1" out="$2"
here="$(cd "$(dirname "$0")" && pwd)"

mkdir -p "$out/pool/main/m/msol"
cp "$debs"/*.deb "$out/pool/main/m/msol/"
cd "$out"
for arch in amd64 all; do
  d="dists/stable/main/binary-$arch"
  mkdir -p "$d"
  apt-ftparchive --arch "$arch" packages pool > "$d/Packages"
  gzip -9nkf "$d/Packages"
done
apt-ftparchive \
  -o APT::FTPArchive::Release::Origin=LemcheNET \
  -o APT::FTPArchive::Release::Label=msol \
  -o APT::FTPArchive::Release::Suite=stable \
  -o APT::FTPArchive::Release::Codename=stable \
  -o APT::FTPArchive::Release::Architectures="amd64 all" \
  -o APT::FTPArchive::Release::Components=main \
  -o APT::FTPArchive::Release::Description="msol: Microsoft 365 web apps as separate windows on Linux" \
  release dists/stable > dists/stable/Release

cp "$here/msol-archive-keyring.gpg" "$here/msol-archive-keyring.asc" "$here/msol.sources" .

if [[ -n "${APT_SIGNING_KEY:-}" ]]; then
  export GNUPGHOME; GNUPGHOME="$(mktemp -d)"
  trap 'rm -rf "$GNUPGHOME"' EXIT
  gpg --batch --quiet --import <<<"$APT_SIGNING_KEY"
  gpg --batch --yes --clearsign -o dists/stable/InRelease dists/stable/Release
  gpg --batch --yes --armor --detach-sign -o dists/stable/Release.gpg dists/stable/Release
  echo "Signed with $(gpg --batch --with-colons --list-secret-keys | awk -F: '/^fpr:/{print $10; exit}')"
else
  echo "APT_SIGNING_KEY not set: repository left unsigned" >&2
fi

#!/usr/bin/env python3
"""Fetch Microsoft's official app icons for msol (run by `msol icons official`).

Reads "id|source|url" lines on stdin (from apps.conf) and saves one icon per app
as <outdir>/<id>.svg or <id>.png. Sources:
  fabric:<name>  Microsoft's Fluent brand-icon CDN (SVG)
  site           the icon the app's own site declares (web manifest or <link rel=icon>)
  <https URL>    that page's declared icon, or that image directly
Pages that only redirect to a Microsoft login page are skipped: their icon is
the generic Microsoft logo, not the app's. Existing icons are kept unless
MSOL_REFRESH is set. Icons stay on this computer; nothing is sent anywhere.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

FABRIC = "https://res-1.cdn.office.net/files/fabric-cdn-prod_20230815.002/assets/brand-icons/product/svg/{}_48x1.svg"
LOGIN_HOSTS = ("login.microsoftonline.com", "login.live.com", "login.microsoft.com", "login.windows.net")
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) msol-icons"}
MAX_BYTES = 2_000_000


def fetch(url: str) -> tuple[str, str, bytes]:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.geturl(), r.headers.get("Content-Type", ""), r.read(MAX_BYTES)


def is_svg(data: bytes) -> bool:
    head = data[:400].lstrip().lower()
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in data[:2000].lower())


def size_of(s: str) -> int:
    m = re.search(r"(\d+)x(\d+)", s or "")
    return int(m.group(1)) if m else (10_000 if (s or "").strip() == "any" else 0)


def icon_candidates(page_url: str, html: str) -> list[str]:
    """Icon URLs a page declares, best first: SVG, then the largest bitmap."""
    found: list[tuple[int, str]] = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        rel = (re.search(r'rel=["\']([^"\']+)', tag, re.I) or [None, ""])[1].lower()
        href = (re.search(r'href=["\']([^"\']+)', tag, re.I) or [None, ""])[1]
        if not href:
            continue
        url = urllib.parse.urljoin(page_url, href)
        if "manifest" in rel:
            try:
                _, _, data = fetch(url)
                for ic in json.loads(data).get("icons", []):
                    src = urllib.parse.urljoin(url, ic.get("src", ""))
                    score = size_of(ic.get("sizes", "")) + (20_000 if src.lower().split("?")[0].endswith(".svg") else 0)
                    found.append((score, src))
            except Exception:
                pass
        elif "icon" in rel:
            sizes = (re.search(r'sizes=["\']([^"\']+)', tag, re.I) or [None, ""])[1]
            score = size_of(sizes) + (20_000 if url.lower().split("?")[0].endswith(".svg") else 0)
            score += 5 if "apple-touch" in rel else 0
            found.append((score, url))
    found.append((1, urllib.parse.urljoin(page_url, "/favicon.ico")))
    seen, out = set(), []
    for _, u in sorted(found, key=lambda x: -x[0]):
        if u not in seen:
            seen.add(u); out.append(u)
    return out


def to_png(data: bytes) -> bytes | None:
    """Convert any bitmap (ICO, PNG, ...) to PNG with Qt, keeping the largest frame."""
    try:
        from PyQt6.QtCore import QBuffer, QByteArray, QIODevice
        from PyQt6.QtGui import QImage, QImageReader
    except ImportError:
        return data if data[:8] == b"\x89PNG\r\n\x1a\n" else None
    from PyQt6.QtGui import QGuiApplication
    if QGuiApplication.instance() is None:  # image plugins (ICO) need an application object
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        to_png.app = QGuiApplication([])  # keep a reference
    buf = QBuffer(); buf.setData(QByteArray(data)); buf.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buf)
    best = QImage()
    for _ in range(max(1, reader.imageCount())):
        img = reader.read()
        if not img.isNull() and img.width() > best.width():
            best = img
        if not reader.jumpToNextImage():
            break
    if best.isNull() or best.width() < 24:
        return None
    out = QBuffer(); out.open(QIODevice.OpenModeFlag.WriteOnly)
    best.save(out, "PNG")
    return bytes(out.data())


def save_image(data: bytes, dest: Path) -> str | None:
    if is_svg(data):
        dest.with_suffix(".png").unlink(missing_ok=True)
        dest.with_suffix(".svg").write_bytes(data)
        return "svg"
    png = to_png(data)
    if png:
        dest.with_suffix(".svg").unlink(missing_ok=True)
        dest.with_suffix(".png").write_bytes(png)
        return "png"
    return None


def get_icon(app_id: str, source: str, app_url: str, dest: Path) -> str:
    if source.startswith("fabric:"):
        try:
            _, _, data = fetch(FABRIC.format(source.split(":", 1)[1]))
            if (kind := save_image(data, dest)):
                return f"Fluent CDN ({kind})"
        except Exception as e:
            return f"not found on the Fluent CDN ({e.__class__.__name__})"
        return "Fluent CDN returned no image"
    page = app_url if source in ("", "site") else source
    try:
        final, ctype, data = fetch(page)
    except Exception as e:
        return f"site unreachable ({e.__class__.__name__})"
    if ctype.startswith("image/") or is_svg(data):
        return f"{page} ({save_image(data, dest) or 'unusable image'})"
    host = urllib.parse.urlparse(final).hostname or ""
    if host in LOGIN_HOSTS or host.startswith("login."):
        return "skipped: the site only shows the Microsoft login page when signed out"
    for icon_url in icon_candidates(final, data.decode("utf-8", "replace")):
        try:
            _, _, img = fetch(icon_url)
        except Exception:
            continue
        if (kind := save_image(img, dest)):
            return f"{urllib.parse.urlparse(icon_url).hostname} ({kind})"
    return "no usable icon declared by the site"


def main() -> int:
    outdir = Path(sys.argv[1]); outdir.mkdir(parents=True, exist_ok=True)
    refresh = bool(os.environ.get("MSOL_REFRESH"))
    for line in sys.stdin:
        parts = line.rstrip("\n").split("|")
        if len(parts) < 3 or not parts[0]:
            continue
        app_id, source, url = parts[0], parts[1], parts[2]
        dest = outdir / app_id
        if not refresh and (dest.with_suffix(".svg").exists() or dest.with_suffix(".png").exists()):
            print(f"  {app_id:<14} kept (use --refresh to fetch again)")
            continue
        print(f"  {app_id:<14} {get_icon(app_id, source, url, dest)}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

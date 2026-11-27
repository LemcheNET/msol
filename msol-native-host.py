#!/usr/bin/env python3
"""Native-messaging host for the msol helper extension (extension/).

Edge starts this when an app window tries to open a link in a browser tab.
It gets {"url": ...}, decides how to open it, and answers {"action": ...}:
  opened  the link was opened as an app window (the extension closes the tab)
  keep    not a Microsoft app link; the browser tab stays

A link to a known app's start page (or one of its "also opens" addresses in
apps.conf) opens that app the normal way (`msol launch <id>`), so it gets the
app's own taskbar entry and icon. Any other link on a Microsoft site, such as
a document, opens in a window of its own (`msol open-url`).
"""
from __future__ import annotations

import json
import os
import re
import struct
import subprocess
import sys
import urllib.parse
from pathlib import Path

HERE = Path(__file__).resolve().parent
MSOL = HERE / "msol"
def _apps_file() -> Path:
    """MSOL_APPS_FILE, else your own ~/.config/msol/apps.conf, else the one that comes with msol."""
    if os.environ.get("MSOL_APPS_FILE"):
        return Path(os.environ["MSOL_APPS_FILE"])
    own = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "msol" / "apps.conf"
    return own if own.is_file() else HERE / "apps.conf"


ENV = {**os.environ, "MSOL_CALLER": "a link in an app window"}
MICROSOFT_DOMAINS = ("office.com", "cloud.microsoft", "microsoft.com", "sharepoint.com", "live.com",
                     "microsoft365.com", "office365.com", "onenote.com", "powerbi.com", "powerapps.com",
                     "powerautomate.com", "clipchamp.com", "dynamics.com", "onedrive.com")
NEVER = ("login.", "account.", "aka.ms", "go.microsoft.com", "support.microsoft.com", "learn.microsoft.com")


def norm(host: str, path: str) -> str:
    return host.lower() + "/" + path.strip("/").lower()


def apps() -> list[tuple[str, list[str]]]:
    """(id, [prefixes]) per app: its start URL plus the 'also opens' column."""
    out = []
    for line in _apps_file().read_text().splitlines():
        line = re.sub(r"(^|\s)#.*", "", line).strip()
        if not line:
            continue
        cols = [c.strip() for c in line.split("|")] + [""] * 9
        prefixes = [cols[2]] + [a.strip() for a in cols[8].split(",") if a.strip()]
        out.append((cols[0], [norm(*_hp(p)) for p in prefixes if p]))
    return out


def _hp(p: str) -> tuple[str, str]:
    u = urllib.parse.urlparse(p if "://" in p else "https://" + p)
    return u.hostname or "", u.path


def decide(url: str) -> tuple[str, str]:
    """Returns (action, detail): ('launch', id) | ('open', url) | ('keep', reason)."""
    u = urllib.parse.urlparse(url)
    host = (u.hostname or "").lower()
    if u.scheme != "https" or any(host.startswith(n) or host == n for n in NEVER):
        return "keep", "not an app link"
    key = norm(host, u.path)
    best = ("", "")
    for app_id, prefixes in apps():
        for p in prefixes:
            if (key == p or key.startswith(p.rstrip("/") + "/")) and len(p) > len(best[1]):
                best = (app_id, p)
    if best[0]:
        return ("launch", best[0]) if key.rstrip("/") == best[1].rstrip("/") else ("open", url)
    if any(host == d or host.endswith("." + d) for d in MICROSOFT_DOMAINS):
        return "open", url
    return "keep", "not a Microsoft site"


def read_msg() -> dict | None:
    raw = sys.stdin.buffer.read(4)
    if len(raw) < 4:
        return None
    n = struct.unpack("<I", raw)[0]
    return json.loads(sys.stdin.buffer.read(n))


def send(obj: dict) -> None:
    data = json.dumps(obj).encode()
    sys.stdout.buffer.write(struct.pack("<I", len(data)) + data)
    sys.stdout.buffer.flush()


def main() -> int:
    msg = read_msg()
    if not msg or "url" not in msg:
        send({"action": "keep"}); return 0
    url = str(msg["url"])
    action, detail = decide(url)
    short = urllib.parse.urlparse(url)._replace(query="", fragment="").geturl()
    if action == "launch":
        subprocess.run([str(MSOL), "ev", "LINK", f"{short} -> app '{detail}'"])
        subprocess.Popen([str(MSOL), "launch", detail], start_new_session=True, env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        send({"action": "opened"})
    elif action == "open":
        subprocess.Popen([str(MSOL), "open-url", url], start_new_session=True, env=ENV,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        send({"action": "opened"})
    else:
        subprocess.run([str(MSOL), "ev", "LINK", f"{short} -> kept in browser ({detail})"])
        send({"action": "keep"})
    return 0


if __name__ == "__main__":
    sys.exit(main())

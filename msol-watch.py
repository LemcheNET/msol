#!/usr/bin/env python3
"""msol event log: records what happens while you test, so you don't have to.

Writes one line per event to ~/.local/state/msol/events.log:
  - msol commands (written by `msol` itself: launch, signin, note, ...)
  - Edge started / exited (main process of the msol profile)
  - app windows opened / retitled / closed, with a verdict from the title
    (LOGIN PROMPT, ACCOUNT PICKER or APP), via a KWin script
  - sign-in state changes: web "stay signed in" cookie, Edge profile account,
    Edge account cache

Started automatically by `msol` (one instance at a time). It exits on its own
two minutes after the last msol Edge process and the launcher have closed.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROFILE = Path(os.environ.get("MSOL_PROFILE_DIR", Path.home() / ".local/share/msol/edge-profile"))
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "msol"
LOG = STATE / "events.log"
IDENTITY_CACHE = Path.home() / ".cache/Microsoft/Edge/IdentityCache"
KWIN_NAME = "msol-watch"
IDLE_EXIT_SECONDS = 120
POLL_SECONDS = 3

KWIN_SCRIPT = r"""
function interesting(w) {
  var c = String(w.resourceClass || "");
  return c.indexOf("msedge") === 0 || c === "msol-edge" || c === "Microsoft-edge" ||
         c === "microsoft-edge" || c.indexOf("__") > 0;
}
function report(kind, w) { print("MSOLEV|" + kind + "|" + w.internalId + "|" + w.resourceClass + "|" + w.caption); }
function watch(w) {
  if (!interesting(w)) return;
  w.captionChanged.connect(function () { report("title", w); });
}
workspace.windowAdded.connect(function (w) { if (interesting(w)) { report("open", w); watch(w); } });
workspace.windowRemoved.connect(function (w) { if (interesting(w)) report("close", w); });
var all = workspace.windowList();
for (var i = 0; i < all.length; i++) { if (interesting(all[i])) { report("present", all[i]); watch(all[i]); } }
"""


def log(kind: str, msg: str) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    line = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {kind:<13} {msg}\n"
    with LOG.open("a") as f:
        f.write(line)


def verdict(title: str) -> str:
    t = title.lower()
    if ("sign in to your account" in t or t.strip() in ("sign in", "sign-in") or "sign in -" in t
            or t.startswith("login.microsoftonline.com") or t.startswith("login.live.com")):
        return "LOGIN PROMPT"
    if "pick an account" in t:
        return "ACCOUNT PICKER"
    if t.startswith("working..."):
        return "AUTO SIGN-IN"   # Microsoft's single sign-on redirect, no input needed
    if "stay signed in" in t:
        return "STAY-SIGNED-IN PROMPT"
    # Before a page sets its title, Edge shows the app name ("host_/path/").
    if not t.strip() or t.startswith("http") or "_/" in t or t in ("about:blank", "new tab"):
        return "LOADING"
    return "APP"


def app_names() -> dict[str, str]:
    """Window id -> app name, from `msol ids`."""
    try:
        out = subprocess.run([str(HERE / "msol"), "ids"], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return {}
    names = {}
    for line in out.splitlines():
        parts = line.split("|")
        if len(parts) == 2:
            names[parts[1]] = parts[0]
    return names


# --- state probes (names and expiry only; never cookie values) -----------------

def chrome_time(us: int) -> dt.datetime:
    return dt.datetime(1601, 1, 1) + dt.timedelta(microseconds=us)


def web_cookie_state() -> str:
    db = PROFILE / "Default/Cookies"
    if not db.exists():
        return "no profile"
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "c.db"
        try:
            shutil.copy2(db, copy)
            rows = sqlite3.connect(copy).execute(
                "SELECT name, creation_utc, expires_utc FROM cookies WHERE name IN "
                "('ESTSAUTHPERSISTENT','ESTSAUTH','MSPAuth','WLSSC') ORDER BY creation_utc DESC").fetchall()
        except (OSError, sqlite3.Error) as e:
            return f"unreadable ({e.__class__.__name__})"
    if not rows:
        return "none"
    name, created, expires = rows[0]
    return f"{name} created {chrome_time(created):%m-%d %H:%M} UTC, expires {chrome_time(expires):%Y-%m-%d}"


def edge_account() -> str:
    try:
        d = json.loads((PROFILE / "Local State").read_text())
        names = [v.get("user_name") for v in d["profile"]["info_cache"].values()]
        return ", ".join(n for n in names if n) or "not signed in"
    except (OSError, ValueError, KeyError):
        return "unknown"


def account_cache() -> str:
    acc = IDENTITY_CACHE / "OneAuth/accounts"
    if not acc.exists():
        return "empty"
    n = sum(1 for _ in acc.rglob("*") if _.is_file())
    return f"{n} account file(s)"


def edge_pids() -> set[int]:
    out = subprocess.run(["pgrep", "-f", f"(/app/extra|/msedge)/msedge .*--user-data-dir={PROFILE}( |$)"],
                         capture_output=True, text=True).stdout
    pids = set()
    for p in out.split():
        try:
            if b"--type=" not in Path(f"/proc/{p}/cmdline").read_bytes():
                pids.add(int(p))
        except OSError:
            pass
    return pids


def launcher_running() -> bool:
    return subprocess.run(["pgrep", "-f", "msol-launcher.py"], capture_output=True).returncode == 0


# --- KWin window events -------------------------------------------------------

def qdbus(*args: str) -> str:
    return subprocess.run(["qdbus6", "org.kde.KWin", *args], capture_output=True, text=True).stdout.strip()


def start_kwin_script() -> Path:
    path = STATE / "kwin-watch.js"
    path.write_text(KWIN_SCRIPT)
    qdbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", KWIN_NAME)
    sid = qdbus("/Scripting", "org.kde.kwin.Scripting.loadScript", str(path), KWIN_NAME)
    qdbus(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
    return path


def stop_kwin_script() -> None:
    qdbus("/Scripting", "org.kde.kwin.Scripting.unloadScript", KWIN_NAME)


def follow_windows(stop: threading.Event) -> None:
    names = app_names()
    titles: dict[str, str] = {}
    apps: dict[str, str] = {}
    proc = subprocess.Popen(["journalctl", "--user", "-f", "-n", "0", "-o", "cat", "--grep", "MSOLEV\\|"],
                            stdout=subprocess.PIPE, text=True)
    pat = re.compile(r"MSOLEV\|(\w+)\|([^|]*)\|([^|]*)\|(.*)$")
    try:
        for line in proc.stdout:
            if stop.is_set():
                break
            m = pat.search(line.rstrip("\n"))
            if not m:
                continue
            kind, wid, cls, title = m.groups()
            if cls not in names and cls.startswith("msedge-"):
                names = app_names()          # apps.conf may have changed since the watcher started
            app = apps.setdefault(wid, names.get(cls) or ("browser window" if cls in ("msol-edge", "microsoft-edge", "Microsoft-edge") else cls))
            if kind in ("open", "present"):
                titles[wid] = title
                log("WINDOW-OPEN", f"{app}: {verdict(title)}  title='{title}'")
            elif kind == "title":
                old = titles.get(wid)
                titles[wid] = title
                if verdict(title) != verdict(old or "") or kind == "title" and verdict(title) != "APP":
                    log("WINDOW-TITLE", f"{app}: {verdict(title)}  title='{title}'")
            elif kind == "close":
                log("WINDOW-CLOSE", f"{app}  (last: {verdict(titles.get(wid, ''))})")
                titles.pop(wid, None)
    finally:
        proc.terminate()


# --- main ---------------------------------------------------------------------

def main() -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    lock = open(STATE / "watch.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return 0  # already running

    def _term(*_):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)

    stop = threading.Event()
    start_kwin_script()
    threading.Thread(target=follow_windows, args=(stop,), daemon=True).start()
    log("WATCH-START", f"profile={PROFILE}")

    last = {"web": None, "account": None, "cache": None}
    pids: set[int] = set()
    idle_since = None
    try:
        while True:
            now_pids = edge_pids()
            for p in sorted(now_pids - pids):
                cmd = Path(f"/proc/{p}/cmdline").read_bytes().replace(b"\0", b" ").decode(errors="replace")
                engine = "flatpak" if cmd.startswith("/app/extra") else "private"
                log("EDGE-START", f"pid {p} ({engine})")
            for p in sorted(pids - now_pids):
                log("EDGE-EXIT", f"pid {p}")
            pids = now_pids

            for key, probe, label in (("web", web_cookie_state, "SIGNIN-WEB"),
                                      ("account", edge_account, "SIGNIN-EDGE"),
                                      ("cache", account_cache, "ACCOUNT-CACHE")):
                val = probe()
                if val != last[key]:
                    log(label, f"{val}" + ("" if last[key] is None else f"   (was: {last[key]})"))
                    last[key] = val

            if pids or launcher_running():
                idle_since = None
            else:
                idle_since = idle_since or time.monotonic()
                if time.monotonic() - idle_since > IDLE_EXIT_SECONDS:
                    break
            time.sleep(POLL_SECONDS)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        stop_kwin_script()
        log("WATCH-STOP", "no Edge or launcher running" if idle_since else "stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

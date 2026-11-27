#!/usr/bin/env python3
"""Microsoft 365 launcher: an Office-style start screen for the msol web apps.

Shows one tile per app in apps.conf, opens each as its own Edge app window
through `msol launch <id>`, and shows whether the shared profile is signed in.
"""
from __future__ import annotations

import datetime as dt
import json
import re
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from PyQt6.QtCore import QPoint, QProcess, QRect, QSize, Qt, QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QCheckBox, QDialog, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLayout,
    QInputDialog, QLabel, QMessageBox, QProgressBar, QProgressDialog, QPushButton, QRadioButton,
    QScrollArea, QStackedWidget,
    QToolButton, QVBoxLayout, QWidget,
)

HERE = Path(__file__).resolve().parent
MSOL = HERE / "msol"
def _apps_file() -> Path:
    """MSOL_APPS_FILE, else your own ~/.config/msol/apps.conf, else the one that comes with msol."""
    if os.environ.get("MSOL_APPS_FILE"):
        return Path(os.environ["MSOL_APPS_FILE"])
    own = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "msol" / "apps.conf"
    return own if own.is_file() else HERE / "apps.conf"

APPS_FILE = _apps_file()
PROFILE = Path(os.environ.get(
    "MSOL_PROFILE_DIR", Path.home() / ".local/share/msol/edge-profile"))


# Cookies Microsoft sets once you are signed in. *PERSISTENT / MSPAuth with an
# expiry mean "Stay signed in" was accepted; ESTSAUTH alone is session-only.
PERSISTENT_MARKERS = {("login.microsoftonline.com", "ESTSAUTHPERSISTENT"),
                      ("login.live.com", "MSPAuth"), ("login.live.com", "WLSSC")}
SESSION_MARKERS = {("login.microsoftonline.com", "ESTSAUTH")}


CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "msol"
ENABLED_FILE = CONFIG / "enabled-apps"
ICON_STYLE_FILE = CONFIG / "icon-style"
USER_ICON_DIR = CONFIG / "icons"
OFFICIAL_ICON_DIR = Path.home() / ".local/share/msol/icons/official"


@dataclass
class App:
    id: str
    name: str
    url: str
    icon: str
    comment: str
    group: str = "More"
    default: str = "on"
    official: str = "site"


def read_apps() -> list[App]:
    apps = []
    for line in APPS_FILE.read_text().splitlines():
        line = re.sub(r"(^|\s)#.*", "", line).strip()   # comments; '#' inside URLs is kept
        if not line:
            continue
        parts = [p.strip() for p in line.split("|")] + [""] * 8
        a = App(*parts[:8])
        a.group = a.group or "More"; a.default = a.default or "on"; a.official = a.official or "site"
        apps.append(a)
    return apps


def enabled_ids(apps: list[App]) -> list[str]:
    """Your saved choice (msol enable/disable, Customize apps), else the defaults."""
    if ENABLED_FILE.exists():
        return [l.strip() for l in ENABLED_FILE.read_text().splitlines()
                if l.strip() and not l.lstrip().startswith("#")]
    return [a.id for a in apps if a.default == "on"]


def icon_style() -> str:
    try:
        return ICON_STYLE_FILE.read_text().strip() or "generic"
    except OSError:
        return "generic"


def app_icon(app: App) -> QIcon:
    """Your icon > project icons/ > official icon (if chosen) > generic theme icon."""
    dirs = [USER_ICON_DIR, HERE / "icons"] + ([OFFICIAL_ICON_DIR] if icon_style() == "official" else [])
    for d in dirs:
        for ext in ("svg", "png"):
            f = d / f"{app.id}.{ext}"
            if f.exists():
                return QIcon(str(f))
    if app.icon.startswith("/"):
        return QIcon(app.icon)
    return QIcon.fromTheme(app.icon, QIcon.fromTheme("applications-internet"))


def chrome_time_to_dt(us: int) -> dt.datetime:
    # Chromium stores times as microseconds since 1601-01-01 UTC.
    return dt.datetime(1601, 1, 1, tzinfo=dt.timezone.utc) + dt.timedelta(microseconds=us)


def sign_in_state() -> tuple[str, str]:
    """Returns (state, detail): state is 'persistent', 'session', 'none' or 'new'."""
    db = next((p for p in (PROFILE / "Default/Network/Cookies", PROFILE / "Default/Cookies")
               if p.exists()), None)
    if db is None:
        return "new", "No profile yet"
    # Edge keeps the database locked while it runs, so read a copy.
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "c.db"
        shutil.copy2(db, copy)
        try:
            rows = sqlite3.connect(copy).execute(
                "SELECT host_key, name, expires_utc, has_expires FROM cookies "
                "WHERE host_key LIKE '%login.microsoftonline.com' OR host_key LIKE '%login.live.com'"
            ).fetchall()
        except sqlite3.Error as e:
            return "none", f"Could not read profile ({e})"
    now = dt.datetime.now(dt.timezone.utc)
    best_expiry = None
    session = False
    for host, name, expires, has_expires in rows:
        key = (host.lstrip("."), name)
        if key in PERSISTENT_MARKERS and has_expires:
            exp = chrome_time_to_dt(expires)
            if exp > now and (best_expiry is None or exp > best_expiry):
                best_expiry = exp
        elif key in SESSION_MARKERS or key in PERSISTENT_MARKERS:
            session = True
    if best_expiry:
        return "persistent", f"Signed in, stays signed in until {best_expiry.astimezone():%d %b %Y}"
    if session:
        return "session", "Signed in for this session only: sign in again and choose “Stay signed in”"
    return "none", "Not signed in"


def edge_account() -> str | None:
    """Email of the account the Edge profile itself is signed in to, if any.

    Edge uses this account to sign in to Microsoft sites automatically. Work
    accounts (for example federated AD FS tenants) often need it: without it the
    web sign-in does not survive a restart and the apps ask for a password again.
    Local State is updated as soon as a sign-in completes; Preferences later.
    """
    try:
        state = json.loads((PROFILE / "Local State").read_text())
        for info in state.get("profile", {}).get("info_cache", {}).values():
            if info.get("user_name"):
                return info["user_name"]
    except (OSError, ValueError):
        pass
    try:
        prefs = json.loads((PROFILE / "Default/Preferences").read_text())
    except (OSError, ValueError):
        return None
    for acct in prefs.get("account_info", []):
        if acct.get("email"):
            return acct["email"]
    return None


def edge_pids() -> list[int]:
    """Main (non-helper) Edge processes running the msol profile."""
    out = subprocess.run(["pgrep", "-f", f"(/app/extra|/msedge)/msedge .*--user-data-dir={PROFILE}( |$)"],
                         capture_output=True, text=True).stdout
    pids = []
    for pid in out.split():
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            continue
        if b"--type=" not in cmd:
            pids.append(int(pid))
    return pids


def close_edge_windows(pids: list[int]) -> None:
    """Close the windows of these Edge processes the way the close button does.

    A clean close lets Edge save the sign-in; killing the process can lose it.
    """
    if not pids:
        return
    js = ("var pids = %s;\n"
          "for (const w of workspace.windowList()) { if (pids.indexOf(w.pid) >= 0) w.closeWindow(); }\n"
          % json.dumps(pids))
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
        f.write(js)
    name = f"msol-close-{os.getpid()}"
    q = lambda *a: subprocess.run(["qdbus6", "org.kde.KWin", *a], capture_output=True, text=True).stdout.strip()
    sid = q("/Scripting", "org.kde.kwin.Scripting.loadScript", f.name, name)
    q(f"/Scripting/Script{sid}", "org.kde.kwin.Script.run")
    time.sleep(0.5)
    q("/Scripting", "org.kde.kwin.Scripting.unloadScript", name)
    os.unlink(f.name)


def wait_until(cond, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        QApplication.processEvents()
        time.sleep(0.1)
    return cond()


def edge_engine() -> dict[str, str]:
    """`msol engine` as a dict: engine (private|flatpak), setting (auto|...), private (installed|missing)."""
    out = subprocess.run([str(MSOL), "engine"], capture_output=True, text=True).stdout
    return {k.strip(): v.strip() for k, _, v in (l.partition(":") for l in out.splitlines()) if v}


def run_msol(*args: str) -> None:
    subprocess.Popen([str(MSOL), *args], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


STYLE = """
QWidget#root { background: palette(window); }
QLabel#title { font-size: 22pt; font-weight: 600; }
QLabel#subtitle { color: palette(placeholder-text); }
QToolButton.tile {
  border: none; border-radius: 10px; background: transparent;
  padding: 8px 4px 6px 4px; font-size: 10pt;
}
QToolButton.tile:hover { background: palette(alternate-base); }
QToolButton.tile:pressed { background: palette(midlight); }
QFrame#warning { background: rgba(210, 153, 34, 0.18); border: 1px solid #D29922; border-radius: 8px; }
QLabel#dot { border-radius: 6px; min-width: 12px; max-width: 12px; min-height: 12px; max-height: 12px; }
"""


class SignInWizard(QDialog):
    """Takes you through signing the Edge profile in, step by step.

    1. Explains what happens (and closes running Microsoft apps if needed).
    2. Opens Edge's own sign-in page in a small window and waits for the
       sign-in to finish (watching the profile, not the page).
    3. Closes that window cleanly by itself and confirms.
    """

    INTRO, WAITING, DONE, FAILED = range(4)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sign in to Microsoft 365")
        self.setMinimumWidth(520)
        self.stack = QStackedWidget()
        self.stack.addWidget(self._intro_page())
        self.stack.addWidget(self._waiting_page())
        self.stack.addWidget(self._done_page())
        self.stack.addWidget(self._failed_page())
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 16); lay.addWidget(self.stack)
        self.poll = QTimer(self); self.poll.timeout.connect(self._check); self.poll.setInterval(1000)
        self._update_intro()

    # --- pages ---------------------------------------------------------------
    @staticmethod
    def _page(title: str, body: QWidget | None = None) -> tuple[QWidget, QVBoxLayout]:
        w = QWidget(); v = QVBoxLayout(w); v.setSpacing(12)
        h = QLabel(title); h.setStyleSheet("font-size: 16pt; font-weight: 600;"); v.addWidget(h)
        return w, v

    @staticmethod
    def _text(s: str) -> QLabel:
        l = QLabel(s); l.setWordWrap(True); l.setTextFormat(Qt.TextFormat.RichText); return l

    def _intro_page(self) -> QWidget:
        w, v = self._page("Sign in once for all Microsoft apps")
        v.addWidget(self._text(
            "A small Edge window opens with <b>Let's get you signed in</b>.<ol>"
            "<li>Click your <b>work account</b> (or <i>Microsoft account</i> to use another one).</li>"
            "<li>Click <b>Continue</b>.</li>"
            "<li>If asked, enter your password and approve the sign-in on your phone.</li></ol>"
            "This window notices when you are done and closes the Edge window for you. "
            "After signing in it can take a few seconds; <b>don't close the Edge window yourself</b>. "
            "After that, every app signs you in automatically, also after a restart."))
        self.intro_note = self._text(""); v.addWidget(self.intro_note)
        v.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        self.close_apps_btn = QPushButton("Close the apps for me"); self.close_apps_btn.clicked.connect(self._close_apps)
        self.start_btn = QPushButton("Start sign-in"); self.start_btn.setDefault(True); self.start_btn.clicked.connect(self._start)
        row.addWidget(cancel); row.addWidget(self.close_apps_btn); row.addWidget(self.start_btn); v.addLayout(row)
        return w

    def _waiting_page(self) -> QWidget:
        w, v = self._page("Waiting for you to sign in…")
        v.addWidget(self._text(
            "Finish the sign-in in the Edge window: pick your account, click <b>Continue</b>, "
            "and complete any password or phone prompt.<br><br>"
            "<b>After you sign in, it can take a few seconds before anything happens. "
            "Don't close the Edge window yourself</b>: this window closes it for you once "
            "Edge has saved the sign-in.<br><br>"
            "Can't see the window? It may be behind this one."))
        bar = QProgressBar(); bar.setRange(0, 0); v.addWidget(bar)
        v.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        cancel = QPushButton("Cancel sign-in"); cancel.clicked.connect(self._cancel)
        row.addWidget(cancel); v.addLayout(row)
        return w

    def _done_page(self) -> QWidget:
        w, v = self._page("You're signed in")
        self.done_text = self._text(""); v.addWidget(self.done_text); v.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        ok = QPushButton("Done"); ok.setDefault(True); ok.clicked.connect(self.accept)
        row.addWidget(ok); v.addLayout(row)
        return w

    def _failed_page(self) -> QWidget:
        w, v = self._page("The sign-in didn't finish")
        v.addWidget(self._text(
            "The Edge window closed before the account was signed in. "
            "You can try again; if it keeps failing, run <tt>msol log</tt> to see what happened."))
        v.addStretch(1)
        row = QHBoxLayout(); row.addStretch(1)
        cancel = QPushButton("Close"); cancel.clicked.connect(self.reject)
        retry = QPushButton("Try again"); retry.setDefault(True)
        retry.clicked.connect(lambda: (self.stack.setCurrentIndex(self.INTRO), self._update_intro()))
        row.addWidget(cancel); row.addWidget(retry); v.addLayout(row)
        return w

    # --- logic ---------------------------------------------------------------
    def _update_intro(self) -> None:
        running = bool(edge_pids())
        who = edge_account()
        notes = []
        if who:
            notes.append(f"Edge is already signed in as <b>{who}</b>. You only need this to use another account.")
        if running:
            notes.append("<b>Microsoft apps are open.</b> They have to close first (unsaved work in them may be lost).")
        self.intro_note.setText("<br>".join(notes))
        self.close_apps_btn.setVisible(running)
        self.start_btn.setEnabled(not running)

    def _close_apps(self) -> None:
        close_edge_windows(edge_pids())
        if not wait_until(lambda: not edge_pids(), 10):
            QMessageBox.warning(self, "Close apps", "Some Microsoft app windows are still open. Close them, then try again.")
        self._update_intro()

    def _start(self) -> None:
        if edge_pids():
            self._update_intro(); return
        subprocess.run([str(MSOL), "note", "sign-in wizard: started"], capture_output=True)
        run_msol("signin-edge")
        self.started = time.monotonic()
        self.stack.setCurrentIndex(self.WAITING)
        self.poll.start()

    def _check(self) -> None:
        who = edge_account()
        alive = bool(edge_pids())
        if who:
            self.poll.stop()
            # Give Edge a moment to store the account, then close its window cleanly.
            wait_until(lambda: False, 3)
            close_edge_windows(edge_pids())
            wait_until(lambda: not edge_pids(), 15)
            subprocess.run([str(MSOL), "note", f"sign-in wizard: signed in as {who}"], capture_output=True)
            self.done_text.setText(
                f"Edge is signed in as <b>{who}</b>.<br><br>"
                "Open any app from the launcher: it signs you in automatically, "
                "also after you close all apps or restart.")
            self.stack.setCurrentIndex(self.DONE)
        elif not alive and time.monotonic() - self.started > 8:
            self.poll.stop()
            subprocess.run([str(MSOL), "note", "sign-in wizard: window closed before sign-in finished"], capture_output=True)
            self.stack.setCurrentIndex(self.FAILED)

    def _cancel(self) -> None:
        self.poll.stop()
        close_edge_windows(edge_pids())
        subprocess.run([str(MSOL), "note", "sign-in wizard: cancelled"], capture_output=True)
        self.reject()


class FlowLayout(QLayout):
    """Lays widgets out left to right and wraps to the next row at the width."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 6) -> None:
        super().__init__(parent)
        self._items: list = []
        self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None: self._items.append(item)
    def count(self) -> int: return len(self._items)
    def itemAt(self, i): return self._items[i] if 0 <= i < len(self._items) else None
    def takeAt(self, i): return self._items.pop(i) if 0 <= i < len(self._items) else None
    def expandingDirections(self): return Qt.Orientation(0)
    def hasHeightForWidth(self) -> bool: return True
    def heightForWidth(self, width: int) -> int: return self._place(QRect(0, 0, width, 0), apply=False)
    def setGeometry(self, rect: QRect) -> None:
        super().setGeometry(rect); self._place(rect, apply=True)
    def sizeHint(self) -> QSize: return self.minimumSize()
    def minimumSize(self) -> QSize:
        s = QSize()
        for it in self._items:
            s = s.expandedTo(it.minimumSize())
        return s

    def _place(self, rect: QRect, apply: bool) -> int:
        x, y, row_h, gap = rect.x(), rect.y(), 0, self.spacing()
        for it in self._items:
            w = it.sizeHint()
            if x + w.width() > rect.right() + 1 and row_h > 0:
                x, y, row_h = rect.x(), y + row_h + gap, 0
            if apply:
                it.setGeometry(QRect(QPoint(x, y), w))
            x += w.width() + gap
            row_h = max(row_h, w.height())
        return y + row_h - rect.y()


class CustomizeDialog(QDialog):
    """Choose which apps are shown (per app or per group) and the icon style."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Customize apps")
        self.resize(620, 680)
        self.apps = read_apps()
        shown = set(enabled_ids(self.apps))
        self.boxes: dict[str, QCheckBox] = {}

        body = QWidget(); v = QVBoxLayout(body)
        groups: dict[str, list[App]] = {}
        for a in self.apps:
            groups.setdefault(a.group, []).append(a)
        for group, members in groups.items():
            gb = QGroupBox(); gl = QGridLayout(gb)
            all_box = QCheckBox(group); all_box.setTristate(True)
            all_box.setStyleSheet("font-weight: 600;")
            gl.addWidget(all_box, 0, 0, 1, 2)
            boxes = []
            for i, a in enumerate(members):
                cb = QCheckBox(a.name); cb.setIcon(app_icon(a)); cb.setToolTip(f"{a.comment}\n{a.url}")
                cb.setChecked(a.id in shown)
                gl.addWidget(cb, 1 + i // 2, i % 2)
                self.boxes[a.id] = cb; boxes.append(cb)
            self._link_group(all_box, boxes)
            v.addWidget(gb)
        v.addStretch(1)
        scroll = QScrollArea(); scroll.setWidget(body); scroll.setWidgetResizable(True)

        icons = QGroupBox("Icons"); il = QVBoxLayout(icons)
        self.generic = QRadioButton("Generic icons (from your desktop's icon theme)")
        self.official = QRadioButton("Official Microsoft icons")
        note = QLabel("Downloaded once from Microsoft (its Fluent icon CDN and the apps' own sites) "
                      "and kept on this computer. Apps without an official icon keep the generic one. "
                      "Your own icons in ~/.config/msol/icons/<id>.png always win.")
        note.setWordWrap(True); note.setObjectName("subtitle")
        grp = QButtonGroup(self); grp.addButton(self.generic); grp.addButton(self.official)
        (self.official if icon_style() == "official" else self.generic).setChecked(True)
        il.addWidget(self.generic); il.addWidget(self.official); il.addWidget(note)

        row = QHBoxLayout()
        defaults = QPushButton("Restore defaults"); defaults.clicked.connect(self._defaults)
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        save = QPushButton("Save"); save.setDefault(True); save.clicked.connect(self._save)
        row.addWidget(defaults); row.addStretch(1); row.addWidget(cancel); row.addWidget(save)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Choose the apps to show in the launcher and the app menu."))
        lay.addWidget(scroll, 1); lay.addWidget(icons); lay.addLayout(row)

    @staticmethod
    def _link_group(all_box: QCheckBox, boxes: list[QCheckBox]) -> None:
        def sync_head() -> None:
            n = sum(b.isChecked() for b in boxes)
            all_box.blockSignals(True)
            all_box.setCheckState(Qt.CheckState.Checked if n == len(boxes) else
                                  Qt.CheckState.Unchecked if n == 0 else Qt.CheckState.PartiallyChecked)
            all_box.blockSignals(False)

        def head_clicked() -> None:
            # Clicking the group: everything on, unless everything already is.
            on = not all(b.isChecked() for b in boxes)
            for b in boxes:
                b.setChecked(on)
            sync_head()

        all_box.clicked.connect(head_clicked)
        for b in boxes:
            b.toggled.connect(sync_head)
        sync_head()

    def _defaults(self) -> None:
        for a in self.apps:
            self.boxes[a.id].setChecked(a.default == "on")

    def _save(self) -> None:
        chosen = [a.id for a in self.apps if self.boxes[a.id].isChecked()]
        CONFIG.mkdir(parents=True, exist_ok=True)
        ENABLED_FILE.write_text("# Apps shown by msol (one id per line). Edit with: msol enable|disable\n"
                                + "".join(f"{i}\n" for i in chosen))
        subprocess.run([str(MSOL), "note", f"customize: shown apps = {' '.join(chosen)}"], capture_output=True)
        style = "official" if self.official.isChecked() else "generic"
        if style != icon_style() or style == "official" and not any(OFFICIAL_ICON_DIR.glob("*")):
            if not self._run_icons(style):
                return
        else:
            subprocess.run([str(MSOL), "install"], capture_output=True)   # refresh the app menu
        self.accept()

    def _run_icons(self, style: str) -> bool:
        """Runs `msol icons <style>` (which also refreshes the menu) with a progress dialog."""
        busy = QProgressDialog("Fetching Microsoft's icons…" if style == "official" else "Updating icons…",
                               None, 0, 0, self)
        busy.setWindowTitle("Icons"); busy.setMinimumDuration(0); busy.show()
        proc = QProcess(self)
        proc.start(str(MSOL), ["icons", style])
        while not proc.waitForFinished(100):
            QApplication.processEvents()
        busy.close()
        if proc.exitCode() != 0:
            QMessageBox.warning(self, "Icons", bytes(proc.readAllStandardError()).decode() or "Fetching icons failed.")
            return False
        return True


class EdgeInstallDialog(QDialog):
    """Runs `msol edge-install` (then `msol install`) and shows its progress."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Install msol's Edge")
        self.setMinimumWidth(520)
        self.label = QLabel("Downloading and checking Microsoft Edge…"); self.label.setWordWrap(True)
        self.bar = QProgressBar(); self.bar.setRange(0, 0)
        self.detail = QLabel(); self.detail.setObjectName("subtitle"); self.detail.setWordWrap(True)
        self.button = QPushButton("Cancel"); self.button.clicked.connect(self._button)
        row = QHBoxLayout(); row.addStretch(1); row.addWidget(self.button)
        lay = QVBoxLayout(self); lay.setContentsMargins(24, 20, 24, 16); lay.setSpacing(10)
        lay.addWidget(self.label); lay.addWidget(self.bar); lay.addWidget(self.detail); lay.addLayout(row)
        self.output = ""
        self.ok: bool | None = None          # None while running
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self._read)
        self.proc.finished.connect(self._finished)
        self.proc.start(str(MSOL), ["edge-install"])

    def _read(self) -> None:
        self.output += bytes(self.proc.readAllStandardOutput()).decode(errors="replace")
        lines = [l.strip() for l in re.split(r"[\r\n]", self.output) if l.strip()]
        is_progress = lambda l: "#" in l and re.search(r"\d%$", l)   # curl's progress bar
        text = [l for l in lines if not is_progress(l)]
        if text:
            self.detail.setText(text[-1])
        if lines and is_progress(lines[-1]):
            pct = float(re.search(r"(\d+(?:[.,]\d+)?)%$", lines[-1]).group(1).replace(",", "."))
            self.bar.setRange(0, 100); self.bar.setValue(int(pct))
        elif self.bar.maximum() == 100:
            self.bar.setRange(0, 0)           # download done: checking and unpacking

    def _finished(self, code: int, _status) -> None:
        self.bar.setRange(0, 1); self.bar.setValue(1)
        self.button.setText("Close")
        self.ok = code == 0
        if not self.ok:
            lines = [l.strip() for l in re.split(r"[\r\n]", self.output) if l.strip()]
            errors = [l for l in lines if l.startswith("msol:") or "curl:" in l]
            self.label.setText("<b>Edge could not be installed.</b>")
            self.detail.setText((errors or lines or ["Unknown error"])[-1])
            return
        subprocess.run([str(MSOL), "install"], capture_output=True)   # menu entries for the new Edge
        self.label.setText("<b>msol's own Edge is installed.</b>")
        running = bool(edge_pids())
        self.detail.setText(
            ("Close all Microsoft app windows, then open them again from here or the menu, so they "
             "switch to msol's Edge. " if running else "")
            + "You will probably have to sign in once more (Set up automatic sign-in…): the "
            "Flatpak Edge and msol's Edge can't read each other's saved sign-in.")
        self.button.setDefault(True)

    def _button(self) -> None:
        self.accept() if self.ok else self.reject()

    def reject(self) -> None:
        if self.proc.state() != QProcess.ProcessState.NotRunning:
            self.proc.kill(); self.proc.waitForFinished(3000)
        super().reject()


class Launcher(QWidget):

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle("Microsoft 365")
        self.setWindowIcon(QIcon.fromTheme("applications-office"))
        self.setStyleSheet(STYLE)

        title = QLabel(self._greeting()); title.setObjectName("title")
        subtitle = QLabel("Each app opens in its own window. One sign-in covers them all.")
        subtitle.setObjectName("subtitle")

        # Shown when msol is not using its own Edge (see edge_check).
        self.warning = QFrame(); self.warning.setObjectName("warning")
        self.warning_text = QLabel(); self.warning_text.setWordWrap(True)
        self.install_edge_btn = QPushButton(QIcon.fromTheme("download"), "Install msol's Edge…")
        self.install_edge_btn.clicked.connect(self.install_edge)
        wl = QHBoxLayout(self.warning); wl.setContentsMargins(12, 8, 12, 8)
        icon = QLabel(); icon.setPixmap(QIcon.fromTheme("dialog-warning").pixmap(22, 22))
        wl.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop); wl.addWidget(self.warning_text, 1)
        wl.addWidget(self.install_edge_btn, 0, Qt.AlignmentFlag.AlignVCenter)
        self.warning.hide()

        self.tiles = QWidget()
        self.tiles_layout = QVBoxLayout(self.tiles); self.tiles_layout.setContentsMargins(0, 0, 8, 0)
        scroll = QScrollArea(); scroll.setWidget(self.tiles); scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setMinimumHeight(260)
        self.build_tiles()

        self.dot = QLabel(); self.dot.setObjectName("dot")
        self.status = QLabel(); self.status.setWordWrap(True)
        self.running = QLabel(); self.running.setObjectName("subtitle")
        self.signin_btn = QPushButton(QIcon.fromTheme("im-user-online"), "Sign in…")
        self.signin_btn.clicked.connect(self.sign_in_edge)
        self.account = QLabel(); self.account.setObjectName("subtitle"); self.account.setWordWrap(True)
        self.edge_btn = QPushButton(QIcon.fromTheme("user-identity"), "Set up automatic sign-in…")
        self.edge_btn.setToolTip("Sign the Edge profile in to your account, so the apps sign you in automatically")
        self.edge_btn.clicked.connect(self.sign_in_edge)
        signout_btn = QPushButton(QIcon.fromTheme("system-log-out"), "Sign out…")
        signout_btn.clicked.connect(self.sign_out)

        status_row = QHBoxLayout()
        status_row.addWidget(self.dot); status_row.addWidget(self.status, 1)
        status_row.addWidget(self.running)
        status_row.addWidget(self.signin_btn); status_row.addWidget(signout_btn)
        note_btn = QPushButton(QIcon.fromTheme("document-edit"), "Add note…")
        note_btn.setToolTip("Write a line into the event log (msol log), e.g. what you are about to test")
        note_btn.clicked.connect(self.add_note)
        account_row = QHBoxLayout()
        account_row.addStretch(1); account_row.addWidget(self.edge_btn)
        account_row.addWidget(note_btn)
        customize_btn = QPushButton(QIcon.fromTheme("configure"), "Customize apps…")
        customize_btn.setToolTip("Choose which Microsoft apps to show, and generic or official icons")
        customize_btn.clicked.connect(self.customize)
        account_row.addWidget(customize_btn)
        uninstall_btn = QPushButton(QIcon.fromTheme("edit-delete"), "Uninstall…")
        uninstall_btn.setToolTip("Remove the menu entries, or everything msol has set up on this computer")
        uninstall_btn.clicked.connect(self.uninstall)
        account_row.addWidget(uninstall_btn)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20); layout.setSpacing(14)
        layout.addWidget(title); layout.addWidget(subtitle); layout.addWidget(self.warning)
        layout.addSpacing(6); layout.addWidget(scroll, 1)
        layout.addLayout(status_row); layout.addWidget(self.account); layout.addLayout(account_row)

        self.refresh()
        timer = QTimer(self); timer.timeout.connect(self.refresh); timer.start(4000)
        QTimer.singleShot(300, lambda: self.edge_check(ask=True))
        # First start (e.g. after installing the package): create the menu entries.
        if not (Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
                / "applications" / "msol-launcher.desktop").exists():
            run_msol("install")

    @staticmethod
    def _greeting() -> str:
        h = dt.datetime.now().hour
        return "Good morning" if h < 12 else "Good afternoon" if h < 18 else "Good evening"

    def build_tiles(self) -> None:
        while self.tiles_layout.count():
            item = self.tiles_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        apps = read_apps()
        shown = set(enabled_ids(apps))
        groups: dict[str, list[App]] = {}
        for a in apps:
            if a.id in shown:
                groups.setdefault(a.group, []).append(a)
        for group, members in groups.items():
            if len(groups) > 1:
                head = QLabel(group); head.setStyleSheet("font-size: 12pt; font-weight: 600; margin-top: 6px;")
                self.tiles_layout.addWidget(head)
            box = QWidget(); flow = FlowLayout(box, spacing=6)
            for app in members:
                flow.addWidget(self._tile(app))
            self.tiles_layout.addWidget(box)
        if not groups:
            self.tiles_layout.addWidget(QLabel("No apps are shown. Use “Customize apps…” to choose some."))
        self.tiles_layout.addStretch(1)

    def customize(self) -> None:
        if CustomizeDialog(self).exec():
            self.build_tiles()

    def _tile(self, app: App) -> QToolButton:
        b = QToolButton()
        b.setProperty("class", "tile")
        # Long names wrap onto two lines ("Microsoft 365" / "Copilot").
        name = app.name if len(app.name) <= 13 else app.name[::-1].replace(" ", "\n", 1)[::-1]
        b.setText(name)
        b.setToolTip(f"{app.comment}\n{app.url}")
        b.setIcon(app_icon(app)); b.setIconSize(QSize(56, 56))
        b.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        b.setAutoRaise(True)
        b.setFixedSize(QSize(118, 112))
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.clicked.connect(lambda _=False, a=app.id: run_msol("launch", a))
        return b

    def refresh(self) -> None:
        state, detail = sign_in_state()
        email = edge_account()
        if email:
            self.account.setText(f"Edge profile signed in as {email}: the apps sign you in automatically.")
        else:
            self.account.setText("Edge profile is not signed in. Work accounts may then ask for a "
                                 "password after every restart.")
            if state == "persistent":
                state, detail = "session", "Signed in to the web apps, but this may not survive a restart"
        self.edge_btn.setVisible(not email)
        color = {"persistent": "#2E9E4F", "session": "#D29922"}.get(state, "#C42B1C")
        self.dot.setStyleSheet(f"background: {color};")
        self.status.setText(detail)
        self.signin_btn.setVisible(state != "persistent")
        n = len(edge_pids())
        self.running.setText("Apps running" if n else "No apps open")

    def edge_check(self, ask: bool = False) -> None:
        """Warn (and on start, offer to install) when msol's own Edge is missing."""
        e = edge_engine()
        if e.get("engine") == "private":
            self.warning.hide(); return
        chosen = e.get("setting") == "flatpak"
        self.warning_text.setText(
            "<b>Not using msol's own Edge.</b> The apps run in the Flatpak Edge"
            + (" (MSOL_ENGINE=flatpak in msol.conf)" if chosen else ", because msol's own Edge is not installed")
            + ": links from Microsoft's app menu open in a full browser window, and the app "
            "windows may share one taskbar entry and look blurry.")
        self.install_edge_btn.setVisible(not chosen)
        self.warning.show()
        if not ask or chosen:
            return
        answer = QMessageBox.question(
            self, "Install msol's own Edge?",
            "msol's own copy of Microsoft Edge is not installed, so the apps would run in the "
            "Flatpak Edge instead. There, links from Microsoft's app menu open in a full browser "
            "window, and the app windows can't each have their own taskbar entry.\n\n"
            "Install it now? This downloads Microsoft Edge (about 170 MB) from Microsoft's "
            "package server (packages.microsoft.com), checks Microsoft's signature, and unpacks "
            f"it into {Path.home() / '.local/share/msol/edge'}. No administrator rights are needed.",
            QMessageBox.StandardButton.No | QMessageBox.StandardButton.Yes, QMessageBox.StandardButton.Yes)
        if answer == QMessageBox.StandardButton.Yes:
            self.install_edge()

    def install_edge(self) -> None:
        if EdgeInstallDialog(self).exec():
            self.edge_check()
            self.build_tiles()
            self.refresh()

    def add_note(self) -> None:
        text, ok = QInputDialog.getText(self, "Add note", "What are you testing or seeing?")
        if ok and text.strip():
            subprocess.run([str(MSOL), "note", text.strip()], capture_output=True)

    def sign_in_edge(self) -> None:
        SignInWizard(self).exec()
        self.refresh()

    def sign_out(self) -> None:
        answer = QMessageBox.warning(
            self, "Sign out of all Microsoft apps?",
            "This closes every Microsoft app window and permanently deletes the "
            f"saved profile:\n\n{PROFILE}\n\nThat removes your sign-in, cookies and "
            "cached data. It cannot be undone; you will need to sign in again.",
            QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
            QMessageBox.StandardButton.Cancel)
        if answer != QMessageBox.StandardButton.Yes:
            return
        if not self._close_all_apps("Sign out"):
            return
        shutil.rmtree(PROFILE, ignore_errors=True)
        self.refresh()

    def _close_all_apps(self, title: str) -> bool:
        """Close every Microsoft app window (cleanly, then by force); False if Edge stays."""
        close_edge_windows(edge_pids())
        wait_until(lambda: not edge_pids(), 10)
        for pid in edge_pids():
            os.kill(pid, signal.SIGTERM)
        wait_until(lambda: not edge_pids(), 5)
        if edge_pids():
            QMessageBox.critical(self, title, "Edge did not close. Close the app windows and try again.")
            return False
        return True

    def uninstall(self) -> None:
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Uninstall")
        box.setText("What do you want to remove?")
        box.setInformativeText(
            "<b>Menu entries</b> removes the Microsoft apps from the application menu. "
            "Your sign-in, msol's Edge and your settings stay, and “msol install” brings them back.<br><br>"
            "<b>Everything</b> also closes all Microsoft app windows and permanently deletes your "
            f"sign-in profile ({PROFILE}), msol's own Edge, the official icons, your app choices "
            "and the event log. You will be signed out. This cannot be undone.<br><br>"
            f"msol's program files in {HERE} are not deleted either way.")
        menu = box.addButton("Remove menu entries", QMessageBox.ButtonRole.AcceptRole)
        everything = box.addButton("Remove everything", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() not in (menu, everything):
            return
        args = ["uninstall"]
        if box.clickedButton() is everything:
            if QMessageBox.warning(self, "Remove everything?",
                                   "This signs you out and deletes all of msol's data. Continue?",
                                   QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes,
                                   QMessageBox.StandardButton.Cancel) != QMessageBox.StandardButton.Yes:
                return
            if not self._close_all_apps("Uninstall"):
                return
            args += ["--all", "--yes"]
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            proc = subprocess.run([str(MSOL), *args], capture_output=True, text=True)
        finally:
            QApplication.restoreOverrideCursor()
        if proc.returncode != 0:
            QMessageBox.critical(self, "Uninstall", proc.stderr.strip() or proc.stdout.strip() or "Uninstall failed.")
            return
        QMessageBox.information(self, "Uninstall", proc.stdout.strip())
        if "--all" in args:
            QApplication.quit()


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Microsoft 365")
    app.setDesktopFileName("msol-launcher")  # Wayland app id -> matches msol-launcher.desktop
    # Event log watcher (single instance; exits by itself when nothing runs).
    subprocess.Popen([sys.executable, str(HERE / "msol-watch.py")], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     env={**os.environ, "MSOL_PROFILE_DIR": str(PROFILE)})
    if "--sign-in" in sys.argv:          # "Microsoft Account (sign-in)" menu entry: just the wizard
        return 0 if SignInWizard().exec() else 1
    w = Launcher(); w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

<img src="icons/msol.svg" alt="msol icon: a penguin in front of an app grid" width="96" align="right">

# Microsoft on Linux (`msol`)

[![CI](https://github.com/LemcheNET/msol/actions/workflows/ci.yml/badge.svg)](https://github.com/LemcheNET/msol/actions/workflows/ci.yml)
[![CodeQL](https://github.com/LemcheNET/msol/actions/workflows/codeql.yml/badge.svg)](https://github.com/LemcheNET/msol/actions/workflows/codeql.yml)

Runs Microsoft's web apps (Outlook, Word, Excel, PowerPoint, OneNote, OneDrive,
Teams, …) in **Microsoft Edge** as **separate app windows**, each with its own
menu entry, taskbar entry and icon, like locally installed Office. They all
share **one sign-in**. Made for KDE Plasma 6 on Wayland.

Website: <https://products.lemche.net/msol/>

msol is an independent project, not made, endorsed or supported by Microsoft.

## How it works

- **One shared Edge instance.** Every app window runs in the same dedicated Edge
  profile (`~/.local/share/msol/edge-profile`), apart from your normal Edge
  browsing. All windows share its cookies, so after you sign in once, every other
  Microsoft app signs you in automatically (SSO).
- **The sign-in lasts.** The profile is stored on disk, so you stay signed in
  after closing every app or rebooting, as long as you answered *Yes* to
  "Stay signed in?".
- **Its own Edge.** `msol edge-install` downloads the official Edge `.deb` from
  packages.microsoft.com, verifies it, and unpacks it into
  `~/.local/share/msol/edge`. It needs no root, adds no apt repository, and
  leaves your everyday Edge (Flatpak) alone. It checks Microsoft's repository
  signature, then the package index checksum, then the package SHA256.
- **Separate windows, sharp on Wayland.** Each app opens with
  `msedge --app=<url>` on native Wayland. Native Edge gives every app window its
  own Wayland app ID (for example `msedge-www.office.com__launch_word-Default`).
  Each generated `.desktop` file names that ID in `StartupWMClass`, so KDE gives
  every app its own taskbar entry and icon. KDE scales the windows itself (no
  stretching), and they use your system GPU drivers. Tested on KDE Plasma 6
  (Wayland) with Edge 158.
- **Flatpak fallback.** Without the private copy, `msol` uses the Edge Flatpak.
  The Flatpak gives every Wayland window the same ID, so in that case it runs
  under XWayland to keep the windows separate (see Notes).

## Setup

**From the package** (Debian, Ubuntu, Kubuntu, KDE neon): download
`msol_<version>_all.deb` from the [latest release](https://github.com/LemcheNET/msol/releases/latest),
then

```bash
sudo apt install ./msol_*_all.deb
```

and open **Microsoft 365 Launcher** from the application menu. On first start it
offers to download msol's own Edge and creates the menu entries. The package puts
msol in `/usr/lib/msol` with `msol` on your `PATH`. To change the app list, copy
`/usr/lib/msol/apps.conf` to `~/.config/msol/apps.conf` and edit that copy.

**From source:**

```bash
./msol edge-install   # once: msol's own Edge (about 200 MB download)
./msol install
```

`msol install` creates one menu entry per app in `apps.conf` (search for "Word",
"Outlook", …), plus **Microsoft Account (sign-in)**.

First time only:

1. Open **Microsoft Account (sign-in)**, or run `./msol signin`.
2. Sign in to your Microsoft account. When asked "Stay signed in?", choose **Yes**.
3. Optional: click the Edge profile icon and sign the profile in with the same
   account. Edge then signs you in to Microsoft sites on its own.
4. Close the window and open apps from the menu. They are already signed in.

## Choosing apps

`apps.conf` lists 31 Microsoft web apps in groups: **Office**, **Collaboration**,
**Power Platform**, **Viva** and **More**. By default only the Office group is
shown: Microsoft 365 Copilot, Microsoft 365, Outlook, Calendar, Word, Excel,
PowerPoint, OneNote, OneDrive and Teams.

To change which apps are shown, click **Customize apps…** in the launcher.
Tick single apps, or click a group name to tick the whole group. **Restore
defaults** goes back to the Office set. From a terminal:

```bash
./msol list                        # all apps; * = shown
./msol enable "Power Platform" loop
./msol disable calendar
./msol enable defaults
```

Your choice is saved in `~/.config/msol/enabled-apps`, and the app menu is
updated to match. `apps.conf` itself keeps the defaults. A few addresses could
not be checked while signed out (Connections, Lists, Learning, Viva and
Add-ins). If one opens the wrong page, `msol log` shows what it loaded; change
its URL in `apps.conf`.

## Icons

There are two icon styles, chosen under **Customize apps… → Icons** or from a terminal:

```bash
./msol icons official    # Microsoft's own icons
./msol icons generic     # icons from your desktop's icon theme (default)
./msol icons status
```

`official` downloads the icons once, into `~/.local/share/msol/icons/official`.
Word, Excel, PowerPoint, Outlook, OneNote, OneDrive, Teams, SharePoint, Visio,
Forms, To Do, Loop and Microsoft 365 come from Microsoft's Fluent brand-icon CDN.
The others come from the icon each app's own site declares. Apps whose site
only shows a login page when signed out (and any other that fails) keep their
generic icon. Run `msol icons official --refresh` to fetch them again. The
icons are Microsoft's trademarks: they are fetched for use on your own
computer and are not part of this project.

Your own icons always win: put `~/.config/msol/icons/<id>.png` or `.svg` there
(the ids are in `msol list`), then run `msol install`.

## Microsoft 365 Launcher

`msol install` also adds **Microsoft 365 Launcher** (or run `./msol gui`). It is
a small start screen, like Office's, built with PyQt6:

- one tile per app in `apps.conf`; click a tile to open that app in its own window
- sign-in status, which refreshes every few seconds:
  - green: signed in, and stays signed in (shows the expiry date)
  - amber: signed in for this session only, so sign in again and choose "Stay signed in"
  - red: not signed in
- **Sign in…** opens the shared profile in a normal window
- **Sign out…** closes all app windows and deletes the saved profile. It asks
  for confirmation first.
- if msol's own Edge is not installed, the launcher asks on start whether to
  install it (about 170 MB from packages.microsoft.com, with a progress bar), and
  otherwise shows an amber warning with an **Install msol's Edge…** button. Without
  it msol falls back to the Flatpak Edge, where links from the app menu open in a
  full browser window. If you chose the Flatpak Edge yourself (`MSOL_ENGINE=flatpak`),
  it only shows the warning. Each fallback launch also writes a `WARNING` to the event log.
- **Uninstall…** lets you choose between removing only the menu entries and
  removing everything (see [Uninstalling](#uninstalling)).

The status comes from the names and expiry dates of Microsoft's sign-in
cookies in the profile (`ESTSAUTHPERSISTENT` for work/school, `MSPAuth`/`WLSSC`
for personal accounts). It never reads cookie values.

## Links between apps

The apps link to each other, for example through Microsoft's app menu (the
grid icon in the top corner) or a document in Teams. An app window would
normally open such a link in a full Edge browser window. msol loads a small
helper extension (`extension/`) and a native-messaging helper
(`msol-native-host.py`) that catch these links:

- A link to a known app (its start address, or one of the **Also opens**
  addresses in `apps.conf`, such as `word.cloud.microsoft`) opens that app the
  normal way, with its own taskbar entry and icon.
- Any other link on a Microsoft site, such as a document, opens in a window of
  its own.
- Everything else, including sign-in pages and other websites, stays in the
  browser.

Only links opened *from an app window* are handled. Browser windows you open
yourself are left alone. Each decision is written to the event log as `LINK`.
The helper only works with the private Edge (`msol edge-install`); a Flatpak
Edge can't run it. Turn it off with `MSOL_LINKS=off` in
`~/.config/msol/msol.conf`. Edge loads it when it starts, so close all apps
once after updating msol.

## Event log (for testing)

While you test, msol writes down what happens, so you don't have to. The
log is `~/.local/state/msol/events.log`. Show it with `msol log` (last 60
lines), `msol log 200`, or follow it live with `msol log -f`.

| Event | Meaning |
|---|---|
| `LAUNCH` | An app was started, and from where: the launcher, a menu/taskbar entry, or a terminal. Also shows whether Edge was already running |
| `SIGNIN-OPEN` | The sign-in window was opened |
| `EDGE-START` / `EDGE-EXIT` | The msol Edge process started or exited (closing the last app window ends it) |
| `WINDOW-OPEN` / `WINDOW-TITLE` / `WINDOW-CLOSE` | An app window, with a verdict from its title: `LOGIN PROMPT`, `ACCOUNT PICKER`, `STAY-SIGNED-IN PROMPT`, `AUTO SIGN-IN` (Microsoft's "Working..." single sign-on step, no input needed; a `LOGIN PROMPT` that turns into this within a second was automatic too), `LOADING` or `APP` (signed in and working) |
| `SIGNIN-WEB` | The web "stay signed in" cookie: name, when it was created, when it expires. A new creation time means you signed in again |
| `SIGNIN-EDGE` | The account the Edge profile is signed in to |
| `ACCOUNT-CACHE` | Whether Edge's account cache (`~/.cache/Microsoft/Edge/IdentityCache`) holds an account |
| `NOTE` | Your own note |

Add a note before each test case:

```bash
./msol note "case 3: closed all apps, reopened Outlook from the taskbar"
```

The log stays on this computer. It holds window titles, which can include
document names, plus cookie names and dates, but never cookie values. A small
background watcher (`msol-watch.py`) records the window and sign-in events. It
starts with Edge or the launcher, and stops by itself two minutes after both
have closed. It follows windows through a KWin script named `msol-watch`, which
it unloads when it stops. To turn logging off, put `MSOL_LOGGING=off` in
`~/.config/msol/msol.conf`.

## Commands

| Command | What it does |
|---|---|
| `msol install` | (Re)create the menu entries from `apps.conf` |
| `msol gui` | Open the Microsoft 365 Launcher |
| `msol open-url <url>` | Open any https link as a window of its own |
| `msol launch <id>` | Open one app, e.g. `msol launch excel` |
| `msol signin-edge` | Open Edge's own account sign-in page in a small window (the wizard uses this) |
| `msol signin [url]` | Open a sign-in window in the shared profile (`--browser` for a full browser window) |
| `msol list` | Show all apps, their group, and which ones are shown |
| `msol enable` / `disable <id\|group\|all\|defaults>` | Choose which apps are shown |
| `msol icons [generic\|official\|status]` | Choose the icon style |
| `msol edge-install` / `edge-update` | Install, or update to, the newest Edge in `~/.local/share/msol/edge`. Native Edge doesn't update itself outside apt, so run `edge-update` now and then |
| `msol engine` | Show which Edge is in use (`private` or `flatpak`) |
| `msol note <text>` | Add your own line to the event log |
| `msol log [N\|-f]` | Show the last N events, or follow them live |
| `msol uninstall` | Remove the menu entries and the link helper; keep the sign-in, msol's Edge and settings |
| `msol uninstall --all` | Remove everything msol created (asks first; `--yes` skips the question) |

## Uninstalling

There are two levels, in the launcher (**Uninstall…**) and on the command line:

- `msol uninstall` removes the menu entries and the link helper registration.
  Your sign-in, msol's own Edge, app choices and event log stay, so
  `msol install` brings everything back as it was.
- `msol uninstall --all` also deletes, after asking:
  - the sign-in profile `~/.local/share/msol/edge-profile` (this signs you out)
  - msol's own Edge `~/.local/share/msol/edge`
  - the official icons `~/.local/share/msol/icons/official`
  - your settings and own icons `~/.config/msol`
  - the event log `~/.local/state/msol`
  - Edge's account cache `~/.cache/Microsoft/Edge` and `~/.config/microsoft-edge`,
    but only when no system-wide Edge is installed (`/opt/microsoft/msedge*`), which
    would share them.

  Close the Microsoft app windows first; the launcher does that for you.

If you installed the package, remove it afterwards with `sudo apt remove msol`.
Neither touches the Flatpak Edge, its data, or the GL driver installed for it.
msol's program files stay in this folder; delete it to finish removing msol.
To start over after `--all`, run `msol edge-install` and then `msol install`.
Without msol's own Edge, msol uses the Flatpak Edge, and links from Microsoft's app menu
open in a full browser window again.

## Customising

- **Apps:** edit `apps.conf`, then run `msol install` again. Every app needs a
  different host/path in its URL, because the window class comes from the URL.
- **Personal Microsoft account** (outlook.com/hotmail): change the Outlook URLs
  to `https://outlook.live.com/mail/` and `https://outlook.live.com/calendar/`.
- **Settings:** create `~/.config/msol/msol.conf` (it is a bash file). For example:

  ```bash
  # Which Edge to use: auto (private copy if installed), private or flatpak
  MSOL_ENGINE=auto
  # Use Edge Beta or Dev for the private copy (then run msol edge-install):
  MSOL_EDGE_CHANNEL=beta
  # Flatpak engine only: force a zoom factor instead of following KDE:
  MSOL_SCALE=1.25
  ```

## Troubleshooting

**The apps ask for a password after every restart.** The Edge profile itself
needs to be signed in, not only the web pages. For work accounts, especially
ones that use AD FS for sign-in, the "Stay signed in" cookie alone is often not
enough. Use the guided sign-in: in the launcher, click **Sign in…** (or **Set up
automatic sign-in…**). It:

1. explains the steps, and offers to close any open Microsoft apps (Edge must
   be closed to start the sign-in page);
2. opens a small Edge window on Edge's own *Let's get you signed in* page
   (`edge://unified-signin`), without the favorites bar or other tabs. Pick your
   account, click **Continue**, and finish any password or phone prompt;
3. notices when the profile is signed in, closes that window cleanly (like the
   close button, so Edge saves the sign-in), and confirms.

From a terminal, `msol signin-edge` opens the same page, without the guidance.
msol starts Edge with `--disable-sync`, so the sign-in doesn't copy your
everyday Edge's bookmarks, extensions or tabs. When you switch between the
Flatpak and the private Edge, sign in again: each one locks its saved data with
its own key, so it can't read what the other saved.

**Clicking around is slow (Flatpak engine).** Edge is drawing everything without the GPU
(software rendering). The Edge Flatpak uses runtime
`org.freedesktop.Platform//25.08`, which needs the matching 64-bit GL driver
extension. If only `GL32.default` is installed, every Edge window falls back to
the CPU, including your everyday Edge. Install the 64-bit driver extension:

```bash
flatpak install flathub org.freedesktop.Platform.GL.default//25.08
```

Then close all Edge windows and start the apps again. `flatpak list --runtime | grep GL`
should list `GL.default 25.08`.

**Microsoft 365 Copilot asks for your password and an MFA approval when the
other apps don't.** That's your organization's sign-in policy, not a separate
profile: Copilot asks Microsoft for an extra permission
(`M365Copilot.Read.All`), and the policy can require a fresh sign-in with MFA
for that. The event log then shows a `LOGIN PROMPT` for Copilot (your
organization's own sign-in page) followed by the MFA page, while the other
apps go straight to `APP`. Edge offers to save the password because you typed
it into a login form in this profile.

## Notes

- **Sharp text with the Flatpak engine.** The windows run under XWayland. Set KDE's
  *Display Configuration → Legacy applications (X11)* to **Apply scaling
  themselves**. Otherwise KDE draws them at 100% and stretches them, which looks
  blurry. `msol` reads that factor (`[Xwayland] Scale` in `kwinrc`) and passes
  it to Edge as `--force-device-scale-factor`. With mixed monitors (125% and
  100%), Edge windows on the 100% monitor appear 25% larger.
- A link opened from an app (for example a document from OneDrive) opens in the
  same shared instance, so it is already signed in.
- The private copy and the Flatpak can use the same profile, but not at the
  same time. Close all Microsoft app windows before you switch engines.

## Development

- CI (`.github/workflows/ci.yml`) runs shellcheck, ruff and checks on
  `apps.conf`, then builds the `.deb` with [nfpm](https://nfpm.goreleaser.com)
  (`packaging/nfpm.yaml`) and test-installs it.
- CodeQL scans the Python, JavaScript and workflow files on every push, on pull
  requests and weekly.
- A release: push a tag such as `v1.0.0`. The release workflow builds the
  `.deb` and publishes it with a `SHA256SUMS` file.
- The website is `site/`, published to GitHub Pages when it changes on `main`.
- `main` is protected: changes go in through pull requests that pass CI and CodeQL.

## Licence

MIT, see [LICENSE](LICENSE).


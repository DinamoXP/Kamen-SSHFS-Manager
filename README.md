# Kamen SSHFS Manager

[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-0070ba?logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=MQFRBZWV6TD6W)

A Windows app for working with your SSH servers: mount remote folders as drive
letters, open SSH tunnels to web panels and databases, browse through a SOCKS
proxy, and open a terminal in one click. Built on SSHFS-Win, with saved
connections, 2FA support and automatic reconnect.

**[Download the latest version](https://github.com/DinamoXP/Kamen-SSHFS-Manager/releases/latest/download/Kamen.SSHFS.Manager.exe)** (Windows 10/11, 64-bit) and see
[Install](#install).

![Kamen SSHFS Manager main window with three connections, two of them mounted](images/main.jpg?v=8a681a97)

## Install

1. Install **WinFsp**, then **SSHFS-Win** (in this order):
   - WinFsp: https://github.com/winfsp/winfsp/releases
   - SSHFS-Win: https://github.com/winfsp/sshfs-win/releases
2. Download **[Kamen.SSHFS.Manager.exe](https://github.com/DinamoXP/Kamen-SSHFS-Manager/releases/latest/download/Kamen.SSHFS.Manager.exe)** from the
   [latest release](https://github.com/DinamoXP/Kamen-SSHFS-Manager/releases/latest).
3. Put it in a folder of your own, e.g. `Documents\Kamen SSHFS Manager`. Use
   a folder you can write to (not `Program Files`), so **Update Now** can
   replace it later.
4. Start it. The first time, Windows SmartScreen may say "Windows protected
   your PC" because the app isn't code-signed yet: click **More info**, then
   **Run anyway** (see [Code Signing](#code-signing)).

There's no installer: the exe is the whole app. It starts with Windows from
then on (you can turn that off in Settings). If WinFsp or SSHFS-Win is
missing, the app shows a banner with download links.

The **SSH** button uses Windows' built-in OpenSSH Client (Settings, Apps,
Optional features), which is installed by default on current Windows 10/11.

## Updating

**Version 1.7.4 or newer:** the app finds new versions by itself (at start and
once a day) and shows a banner. Click **Update Now**: it downloads the new
version, checks it and restarts. To look right away, use **Settings > Check
for Updates**. Details in [Updates](#updates).

**Older versions** can't update themselves, so update once by hand:

1. Exit the app (**Exit**, then **Yes** to disconnect).
2. Download **[Kamen.SSHFS.Manager.exe](https://github.com/DinamoXP/Kamen-SSHFS-Manager/releases/latest/download/Kamen.SSHFS.Manager.exe)** and replace your old exe
   with it: same folder, same file name, so **Start with Windows** still
   finds it. (Older downloads may be named `Kamen SSHFS Manager.exe`; keep
   whichever name you had, or turn Start with Windows off and on again in
   Settings after renaming.)
3. Start it. Your connections and settings are kept (they're stored in
   `%APPDATA%\kamen-sshfs-manager`, not next to the exe).

From then on, updates come through **Update Now**.

## Screenshots

| New connection | Settings |
|:---:|:---:|
| ![Connection details form with Test Connection and Browse](images/new_connection.jpg?v=516857a0) | ![Settings window with theme, startup, connections, drive, update and logging options](images/settings.jpg?v=cebc69a4) |

## Features

- Add, edit and delete SSH connections
- Connect and disconnect per connection, or **Disconnect All**
- Hosts by name, IPv4 or IPv6 address (see [Host Addresses](#host-addresses))
- Three auth methods: Password, Private Key, Ask Each Time
- **Key login...** sets up a login key on the server for you, so no password
  is needed after that (see [Key Login](#key-login))
- Read/Write (default) or Read-only access per drive: a read-only drive's files
  can be opened and copied, not changed
- **Apply & Save** in the Edit window saves and reconnects the drive right
  away, so changes take effect without disconnecting by hand
- **Jump host** support: reach a server through another server
  (see [Jump Host](#jump-host))
- Interactive login (2FA codes) and a **Trust this server?** prompt for new host keys
- Passwords encrypted with Windows DPAPI (readable only by your Windows account)
- **Test Connection** and a remote folder browser for picking the remote path
- Drive letter selector (D: - Z:), custom port, extra SSHFS arguments
- **SSH** button: opens a terminal session in the connection's remote folder,
  using the saved password automatically
- **SSH tunnels**: reach web panels and databases on the server, or browse
  through it with a SOCKS proxy, or let the server reach this PC with a
  remote tunnel (see [SSH Tunnels](#ssh-tunnels))
- Connect automatically when the app starts (per connection)
- Start with Windows, minimized to the tray (on by default); auto-connect
  retries for about a minute at login while the network comes up
- Dark and light themes
- Debug log panel with Copy Log / Clear Log (can be turned off in Settings)
- Checks for WinFsp and SSHFS-Win on startup and links to the downloads
- Exit disconnects all drives in the background with a progress window
- Open a mounted drive in Explorer by clicking its drive tile (e.g. `I:\`)
- Free space and ping time shown on each connected drive's card (the ping
  is a normal ICMP ping every 30 s; nothing appears if the server blocks ping)
- Tray menu: connect, disconnect, open drives and start tunnels without
  opening the window; the tray icon gets an orange dot if a drive hangs
- Right-click a card for more actions, including **Duplicate** and
  **Move Up** / **Move Down** / **Move to Top** / **Move to Bottom** to
  change the order
- Export and import connections (passwords are never exported)
- Checks GitHub for a new release at start and once a day, and updates
  itself with **Update Now** (see [Updates](#updates); can be turned off)
- **Log File** button opens the folder with the log file
- Live status polling (every 5 s) that can't freeze the app, with
  **Restart** / **Kill** for a drive that stops responding
- Automatic reconnect when the network drops; one copy of the app at a time
- The version is shown in the title bar and next to the app name; hover over
  it to see the build date and time

## Running from Source

```
pip install -r requirements.txt
python kamen_sshfs_manager.py
```

Python 3.9+ required (release builds use 3.11). WinFsp and SSHFS-Win are
needed as above.

## Settings

Open with the **⚙ Settings** button. Defaults are shown in bold.

| Setting                 | Options                                             |
|-------------------------|-----------------------------------------------------|
| Appearance              | **Dark** / Light                                    |
| Start with Windows      | **On** / Off (enabled on first run of the exe)      |
| Start minimized to tray | **On** / Off (applies when Windows starts the app)  |
| Restart a drive that stops responding | **On** / Off (after 60 s; interrupts any copy still running on that drive) |
| Enable debug log        | **On** / Off (also written to `kamen.log`)          |
| Check for updates when the app starts | **On** / Off (at start and once a day; see [Updates](#updates)) |
| Check for Updates (button) | Asks GitHub right now, even with the setting above off |
| Connections             | **Export...** / **Import...** a `.json` file of your connections (no passwords) |

Imported connections get new ids, and if a drive letter is already used the
import picks a free one. Connections that used a saved password need it
typed in again (the import lists them).

## Updates

With **Check for updates when the app starts** on (the default), the app asks
GitHub for the newest release a few seconds after it starts and then once a
day while it runs. If GitHub can't be reached, it tries again after 10
minutes.

To check right away, click **Check for Updates** in Settings (this works
even with the daily check turned off).

When a newer version is out, a tray notification and a banner at the top of
the window say so. The banner has:

- **Update Now**: downloads the new exe from the GitHub release, checks it
  against the release's `SHA256SUMS.txt`, swaps it in and restarts the app.
  Connected drives are disconnected first and connected again after the
  restart; running tunnels are stopped. A download that doesn't match the
  checksum is never used. Before it starts, it asks what to do with the old
  version:
  - **Delete it** (default): it is removed after the restart.
  - **Keep it as a backup**: it is renamed to `Kamen.SSHFS.Manager.exe.old`
    in the app's folder (or `Kamen.SSHFS.Manager2.exe.old`, `3`, ... if a
    backup is already there; older backups are never touched). To use it
    again, rename it back to `.exe`.
- **Release Notes**: opens the release page in your browser.
- **Dismiss**: hides the banner until the next version.

Update Now is only offered for the exe (not when running from source). If the
exe's folder can't be written to, download the new version from the release
page instead.

## Host Addresses

The **Host** field accepts a hostname, an IPv4 address, or an IPv6 address,
with or without square brackets:

| Example                          | Notes                                         |
|----------------------------------|-----------------------------------------------|
| `server.lan`                     | Hostname (IPv4 or IPv6, whichever it resolves to) |
| `192.168.1.10`                   | IPv4                                          |
| `2001:db8::5` or `[2001:db8::5]` | Global IPv6                                   |
| `fe80::1%12`                     | Link-local IPv6 - needs the `%` interface number |

The app adds the brackets sshfs needs for IPv6 automatically. Link-local
(`fe80::`) addresses only work on the local network and need the interface
number of your network adapter (shown by `ipconfig`, e.g. `%12`).

## Auth Methods

| Method        | Notes                                                        |
|---------------|--------------------------------------------------------------|
| Password      | Saved encrypted (DPAPI). Used for mounting and the SSH button. |
| Private Key   | Path to your `.pem` or OpenSSH key file; a passphrase is asked in a popup |
| Ask Each Time | A popup asks for the password on each connect; never saved   |

### Key Login

In the connection's Edit window, click **Key login...** to stop using a
password. The app:

1. Creates a new ed25519 key in `%APPDATA%\kamen-sshfs-manager\keys\`
   (using Windows' own `ssh-keygen`).
2. Logs in once with your password and adds the public key to
   `~/.ssh/authorized_keys` on the server.
3. Checks that a login with only the key works, then switches the connection
   to **Private Key** and offers to forget the saved password.

### Jump Host

For a server you can't reach directly (e.g. behind a gateway or bastion
server), fill in **Jump Host** in the Edit window: `jump.example.com`,
`user@jump.example.com` or `user@jump.example.com:2222` (an IPv6 address
with a port goes in brackets: `user@[2001:db8::5]:22`). The username defaults
to the connection's.

The drive, **Test Connection**, the folder browser, **Key login...**, the
**SSH** button and tunnels all go through the jump host. It uses the same
login as the server (the same key, or the saved password); if its password is
different, a popup asks for it. The card shows "via" and the jump host.

### Mount Options

**Options** in the Edit window (hover over each for a short explanation):

| Option | Default | What it does |
|---|---|---|
| Follow symlinks | On | Shows what symlinks on the server point to |
| Compress data | Off | Faster on slow connections |
| Hide dot files | Off | Files like `.bashrc` get Windows' hidden attribute |
| No folder cache | Off | Always shows the newest files (e.g. changed by others); folders open a little slower |
| Rename fix | Off | For older servers that refuse saving over an existing file by renaming |

Anything else can still go in **Extra Args** (sshfs `-o` options).

### Read-only Mounts

Set **Access** to **Read-only** in the New or Edit Connection window
(**Read/Write** is the default).
Files can be opened and copied off the drive, but not changed, created or
deleted. The server's SFTP program is started in read-only mode, so this
needs OpenSSH's `sftp-server` in one of the usual places
(`/usr/lib/openssh/`, `/usr/libexec/openssh/`, `/usr/lib/ssh/`, `/usr/libexec/`,
`/usr/lib/`). If it isn't found, connecting fails with a hint in the debug log.

### Interactive login (2FA)

Servers that ask questions during login (keyboard-interactive, e.g. a
password followed by a 2FA / OTP code) are supported for mounting, the SSH
button, Test Connection and Browse:

- The saved password answers the first password prompt automatically.
- Any other question (2FA code, Duo passcode, key passphrase) opens an
  **SSH login** popup.
- If the saved password is wrong, it's tried once and then the popup asks.
- Mounting waits up to 2 minutes so there's time to type a code.

Under the hood ssh runs the app as its `SSH_ASKPASS` helper. ssh is only told
the connection's ID; the helper reads the encrypted password from the config
itself, so the password never appears in an environment variable or command
line.

### Host key verification

With **Verify SSH host key** ticked, a server whose key isn't in
`%USERPROFILE%\.ssh\known_hosts` triggers a **Trust this server?** popup
showing its key fingerprint. **Trust** saves the key and connects;
**Cancel** refuses the connection. A server whose key has *changed* is always
refused (no popup). The same server reached by a different address (e.g. its
IPv6 address instead of its IPv4 one) counts as a new host.

## Connection Behavior

**Connecting when the server can't be reached**

| How the connect starts                          | Retries                                           |
|-------------------------------------------------|---------------------------------------------------|
| Windows login ("Start with Windows" + a connection set to connect automatically) | 6 attempts, 10 s apart - about 1 minute if the network is down, up to ~2½ minutes if each attempt has to time out |
| App started manually, or **Connect** clicked    | 1 attempt, no retry                               |

**When the connection drops while a drive is mounted**

sshfs sends a keep-alive every 15 s and treats the link as dead after 3 missed
replies (about 45 s). It then reconnects by itself, using the saved password or
asking in a popup, and the drive keeps its letter.

**When a drive stops responding**

If the sshfs process behind a drive stops answering (the drive is there, but
opening it hangs), the app notices within about 10 seconds:

- The card shows **"not responding"** next to the drive letter, with
  **Restart** (kill the mount and mount it again) and **Kill** buttons.
- A tray notification and a `[HUNG]` log line say which drive and which sshfs
  process.
- The rest of the app keeps working, and the app still opens normally while a
  drive is stuck.
- With **Restart a drive that stops responding** on (the default), the app
  restarts it by itself after 60 seconds.

Only one copy of the app runs at a time. Opening it again brings the running
window to the front.

## SSH Tunnels

Click **Tunnels** on a connection's card to manage its tunnels. The button shows
how many are running, e.g. **Tunnels (2)**.

| Tunnels | + Add Tunnel |
|:---:|:---:|
| ![Tunnels window with a web panel, a database and a SOCKS proxy](images/tunnels.jpg?v=4fc19683) | ![New Tunnel dialog with the Portainer preset](images/new_tunnel.jpg?v=1bfe399e) |

| Type | Use it for | How to use it |
|---|---|---|
| **Web panel / database** | A service on the server (or on a machine the server can reach) that isn't open to your network | Open `127.0.0.1:<local port>` on this PC. **Open** starts your browser; for databases, **Copy** copies the address for your database client. Clicking a running tunnel's name or address does the same |
| **SOCKS proxy** | Browsing through the server | Set your browser's proxy to **SOCKS5**, host `127.0.0.1`, the tunnel's local port |
| **Remote** | The opposite direction: let the server reach something on this PC (or your network), e.g. a web project you're working on | The server opens **Server port** on its own `127.0.0.1` and passes connections to **Target host** / **Target port** as seen from this PC. On the server, use `127.0.0.1:<server port>` |

Presets fill in common ports: Web panel (8080 -> 80), Portainer (9000),
MySQL / MariaDB (3306), PostgreSQL (5432) and SOCKS proxy (1080). In the
tunnel settings, **Target host** and **Target port** are where the tunnel
leads, as seen from the server: `127.0.0.1` means the server itself, or enter
another machine the server can reach (e.g. a NAS on the server's network).

- Logins work like mounting: saved password, 2FA popups and the
  **Trust this server?** prompt.
- **Start when the app starts** and **Restart if it drops** (on by default) are
  set per tunnel. A dropped tunnel retries after 10 s, then up to every 60 s.
  Wrong passwords, rejected host keys and busy ports are not retried.
- A local port that's already in use on this PC is reported straight away.
- Tunnels only listen on `127.0.0.1`, so other devices on your network can't
  use them.
- Tunnels stop when the app exits, including if it crashes.

## Troubleshooting

| Log message                                    | Meaning / fix                                   |
|------------------------------------------------|-------------------------------------------------|
| `<path>: No such file or directory`            | Remote path doesn't exist - Edit → Test Connection → Browse… |
| `read: Connection reset by peer`               | ssh quit before mounting - run Test Connection for the real reason (password, host key, network) |
| `sshfs.exe not found`                          | Install WinFsp, then SSHFS-Win                  |
| `[HUNG] X: not responding since ...`           | The drive's sshfs stopped answering. Click **Restart** on its card, or **Kill** to remove the drive |
| Banner: WinFsp / SSHFS-Win not installed       | Use the Get… buttons, install, then click Re-check |

## Building the .exe

Official releases are built by GitHub Actions
([`.github/workflows/release.yml`](.github/workflows/release.yml)) on GitHub's
Windows servers and attached to a GitHub Release.
Pushing a tag that matches `version.json` (e.g. `v1.5.0`) starts a release
build. Each release includes `SHA256SUMS.txt` for verifying the download.

To build the same exe yourself:

```
pip install -r requirements.txt pyinstaller==6.17.0
python tools/make_version_info.py
pyinstaller --onefile --windowed --name "Kamen SSHFS Manager" --icon icon.ico --add-data "version.json;." --exclude-module numpy --version-file version_info.txt kamen_sshfs_manager.py
```

The exe is written to `dist\Kamen SSHFS Manager.exe`.

Run the tests with `python -m unittest discover -s tests`. GitHub runs them on
every push and before every release build; a release whose tests fail gets no
`SHA256SUMS.txt`, so the app's **Update Now** won't offer it.

`kamen_sshfs_manager.py` only starts the app; the code is in the `kamen/`
package (`config`, `winutil`, `ssh`, `mounts`, `ui`), which PyInstaller picks up
on its own.

- `tools/make_version_info.py` writes `version_info.txt` from `version.json`,
  so the exe's Windows properties show the product name and version.
- `--add-data "version.json;."` bundles the version file, so the app shows the
  right version.
- `--exclude-module numpy` leaves out numpy, which Pillow pulls in but the app
  doesn't use (saves ~12 MB).

## Versioning

`version.json` holds the app version (`major.minor.patch`; minor and patch
each roll over after 9). It is bumped with every code change.

## Data Files

All in `%APPDATA%\kamen-sshfs-manager\`:

| File               | Contents                                          |
|--------------------|---------------------------------------------------|
| `connections.json` | Saved connections (passwords DPAPI-encrypted)     |
| `settings.json`    | Theme, logging and startup preferences            |
| `kamen.log`        | Log file (rotated at 1 MB, last 3 kept; never contains passwords) |
| `askpass.cmd`      | Helper used by the SSH button when run from source |
| `keys\`            | Login keys created by **Key login...**            |

"Start with Windows" is stored as a `Kamen SSHFS Manager` entry under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

## Uninstall

The app is a single exe with no installer. To remove it completely:

1. Open the app, click **Disconnect All**, then in **⚙ Settings** untick
   **Start with Windows** and click **Save**.
2. Exit the app and delete `Kamen SSHFS Manager.exe`.
3. Delete the folder `%APPDATA%\kamen-sshfs-manager` (saved connections and
   settings).

Host keys you chose to trust are kept in `%USERPROFILE%\.ssh\known_hosts`,
which is shared with other SSH tools; remove those lines only if you no longer
need them.

## Code Signing

Releases are not code-signed yet, so Windows SmartScreen may warn that the app
is unrecognized. Click **More info**, then **Run anyway**.

To check that a download is genuine:

- Release builds are produced only by the GitHub Actions workflow in this
  repository, from the tagged public source code.
- Each release includes `SHA256SUMS.txt`. Compare it with the hash of your
  download, e.g. in PowerShell:
  `Get-FileHash "Kamen.SSHFS.Manager.exe" -Algorithm SHA256`

## Privacy

The app collects no data and sends nothing about you or your connections
anywhere. It connects to:

- the SSH servers you configure;
- GitHub, at start and once a day, to read the newest release version, while
  **Check for updates when the app starts** is on (the default). Nothing is
  sent except a normal web request. Turn it off in Settings to stop this.
- GitHub, to download the new exe, only when you click **Update Now**.

Download pages open in your browser only when you click a download button.

## Support

Kamen SSHFS Manager is free and open source. If it's useful to you, you can
support its development with a donation:

[Donate with PayPal](https://www.paypal.com/donate/?hosted_button_id=MQFRBZWV6TD6W)

## License

[MIT](LICENSE) © 2026 Irnes Karaduz

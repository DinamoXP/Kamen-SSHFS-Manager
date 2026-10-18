# Kamen SSHFS Manager

[![Donate with PayPal](https://img.shields.io/badge/Donate-PayPal-0070ba?logo=paypal&logoColor=white)](https://www.paypal.com/donate/?hosted_button_id=MQFRBZWV6TD6W)

A Windows app for working with your SSH servers: mount remote folders as drive
letters, open SSH tunnels to web panels and databases, browse through a SOCKS
proxy, and open a terminal in one click. Built on SSHFS-Win, with saved
connections, 2FA support and automatic reconnect.

![Kamen SSHFS Manager main window with two mounted connections](images/main.jpg)

## Screenshots

| New connection | Settings |
|:---:|:---:|
| ![Connection details form with Test Connection and Browse](images/new_connection.jpg) | ![Settings window with theme, startup and logging options](images/settings.jpg) |

## Features

- Add, edit and delete SSH connections
- Connect and disconnect per connection, or **Disconnect All**
- Hosts by name, IPv4 or IPv6 address (see [Host Addresses](#host-addresses))
- Three auth methods: Password, Private Key, Ask Each Time
- Interactive login (2FA codes) and a **Trust this server?** prompt for new host keys
- Passwords encrypted with Windows DPAPI (readable only by your Windows account)
- **Test Connection** and a remote folder browser for picking the remote path
- Drive letter selector (D: - Z:), custom port, extra SSHFS arguments
- **SSH** button: opens a terminal session in the connection's remote folder,
  using the saved password automatically
- **SSH tunnels**: reach web panels and databases on the server, or browse
  through it with a SOCKS proxy (see [SSH Tunnels](#ssh-tunnels))
- Connect automatically when the app starts (per connection)
- Start with Windows, minimized to the tray (on by default); auto-connect
  retries for about a minute at login while the network comes up
- Dark and light themes
- Debug log panel with Copy Log / Clear Log (can be turned off in Settings)
- Checks for WinFsp and SSHFS-Win on startup and links to the downloads
- Exit disconnects all drives in the background with a progress window
- Open a mounted drive in Explorer
- Live status polling (every 5 s) that can't freeze the app, with
  **Restart** / **Kill** for a drive that stops responding
- Automatic reconnect when the network drops; one copy of the app at a time
- Hover over the version in the bottom bar to see the build date and time

## Requirements

### 1. WinFsp + SSHFS-Win

Install these first, in this order:

1. WinFsp: https://github.com/winfsp/winfsp/releases
2. SSHFS-Win: https://github.com/winfsp/sshfs-win/releases

SSHFS-Win installs `sshfs.exe` to `C:\Program Files\SSHFS-Win\bin\sshfs.exe`.
The app checks for both on startup and shows a warning banner with download
links if either is missing.

The **SSH** button uses Windows' built-in OpenSSH Client
(Settings → Apps → Optional features), which is installed by default on
current Windows 10/11.

### 2. Python dependencies

```
pip install -r requirements.txt
```

Python 3.9+ required (release builds use 3.11).

## Usage

```
python kamen_sshfs_manager.py
```

## Settings

Open with the **⚙ Settings** button. Defaults are shown in bold.

| Setting                 | Options                                             |
|-------------------------|-----------------------------------------------------|
| Appearance              | **Dark** / Light                                    |
| Start with Windows      | **On** / Off (enabled on first run of the exe)      |
| Start minimized to tray | **On** / Off (applies when Windows starts the app)  |
| Restart a drive that stops responding | On / **Off** (after 60 s; interrupts any copy still running on that drive) |
| Enable debug log        | **On** / Off (also written to `kamen.log`)          |

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
- With **Restart a drive that stops responding** turned on in Settings, the
  app restarts it by itself after 60 seconds.

Only one copy of the app runs at a time. Opening it again brings the running
window to the front.

## SSH Tunnels

Click **Tunnels** on a connection's card to manage its tunnels. The button shows
how many are running, e.g. **Tunnels (2)**.

| Tunnels | + Add Tunnel |
|:---:|:---:|
| ![Tunnels window with a web panel, a database and a SOCKS proxy](images/tunnels.jpg) | ![New Tunnel dialog with the Portainer preset](images/new_tunnel.jpg) |

| Type | Use it for | How to use it |
|---|---|---|
| **Web panel / database** | A service on the server (or on a machine the server can reach) that isn't open to your network | Open `127.0.0.1:<local port>` on this PC. **Open** starts your browser; for databases, **Copy** copies the address for your database client |
| **SOCKS proxy** | Browsing through the server | Set your browser's proxy to **SOCKS5**, host `127.0.0.1`, the tunnel's local port |

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

This program will not transfer any information to other networked systems
unless specifically requested by the user or the person installing or
operating it.

The app connects only to the SSH servers you configure, and opens download
pages in your browser only when you click a download button.

## Support

Kamen SSHFS Manager is free and open source. If it's useful to you, you can
support its development with a donation:

[Donate with PayPal](https://www.paypal.com/donate/?hosted_button_id=MQFRBZWV6TD6W)

## License

[MIT](LICENSE) © 2026 Irnes Karaduz

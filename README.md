# Kamen SSHFS Manager

A Windows desktop app for mounting remote folders over SSH as drive letters,
built with Python and Tkinter on top of SSHFS-Win.

The current version is in `version.json` and shown in the app's bottom bar.

## Features

- Add, edit and delete SSH connections
- Connect and disconnect per connection, or **Disconnect All**
- Hosts by name, IPv4 or IPv6 address (see [Host Addresses](#host-addresses))
- Three auth methods: Password, Private Key, Ask Each Time
- Interactive login (2FA codes) and a **Trust this server?** prompt for new host keys
- Passwords encrypted with Windows DPAPI (readable only by your Windows account)
- **Test Connection** and a remote folder browser for picking the remote path
- Drive letter selector (D: – Z:), custom port, extra SSHFS arguments
- **SSH** button: opens a terminal session in the connection's remote folder,
  using the saved password automatically
- Connect automatically when the app starts (per connection)
- Start with Windows, minimized to the tray (on by default); auto-connect
  retries for about a minute at login while the network comes up
- Dark and light themes
- Debug log panel with Copy Log / Clear Log (can be turned off in Settings)
- Checks for WinFsp and SSHFS-Win on startup and links to the downloads
- Exit disconnects all drives in the background with a progress window
- Open a mounted drive in Explorer
- Live status polling (every 5 s)
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
pip install pillow pystray paramiko
```

Python 3.9+ required.

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
| Enable debug log        | **On** / Off                                        |

## Host Addresses

The **Host** field accepts a hostname, an IPv4 address, or an IPv6 address,
with or without square brackets:

| Example                          | Notes                                         |
|----------------------------------|-----------------------------------------------|
| `server.lan`                     | Hostname (IPv4 or IPv6, whichever it resolves to) |
| `192.168.1.10`                   | IPv4                                          |
| `2001:db8::5` or `[2001:db8::5]` | Global IPv6                                   |
| `fe80::1%12`                     | Link-local IPv6 — needs the `%` interface number |

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
| Windows login ("Start with Windows" + a connection set to connect automatically) | 6 attempts, 10 s apart — about 1 minute if the network is down, up to ~2½ minutes if each attempt has to time out |
| App started manually, or **Connect** clicked    | 1 attempt, no retry                               |

**When the connection drops while a drive is mounted**

1. Nothing notices right away — no keep-alive checks are configured.
2. Opening files or folders on the drive hangs until Windows gives up on the
   network connection (one to several minutes).
3. ssh exits, the drive disappears, and the card shows **Disconnected**
   within 5 seconds (status is polled every 5 s).
4. The app does **not** reconnect on its own — click **Connect** again.

## Troubleshooting

| Log message                                    | Meaning / fix                                   |
|------------------------------------------------|-------------------------------------------------|
| `<path>: No such file or directory`            | Remote path doesn't exist — Edit → Test Connection → Browse… |
| `read: Connection reset by peer`               | ssh quit before mounting — run Test Connection for the real reason (password, host key, network) |
| `sshfs.exe not found`                          | Install WinFsp, then SSHFS-Win                  |
| Banner: WinFsp / SSHFS-Win not installed       | Use the Get… buttons, install, then click Re-check |

## Building the .exe

```
pip install pyinstaller
pyinstaller --onefile --windowed --name "Kamen SSHFS Manager" --icon icon.ico --add-data "version.json;." --exclude-module numpy kamen_sshfs_manager.py
```

The exe is written to `dist\Kamen SSHFS Manager.exe`.

- `--add-data "version.json;."` bundles the version file, so the exe shows the
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
| `askpass.cmd`      | Helper used by the SSH button when run from source |

"Start with Windows" is stored as a `Kamen SSHFS Manager` entry under
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`.

## License

[MIT](LICENSE) © 2026 Irnes Karaduz

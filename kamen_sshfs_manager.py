"""
Kamen SSHFS Manager - mount remote folders over SSH as Windows drive letters.
Python packages: pillow, pystray, paramiko (UI itself is built-in tkinter).
Requires: WinFsp + SSHFS-Win (https://github.com/winfsp/sshfs-win/releases)
"""

import ctypes
import ctypes.wintypes as wt
import json
import os
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

APP_NAME    = "Kamen SSHFS Manager"

def _load_version():
    """Read version from version.json next to this script (or in CWD as fallback).
    Falls back to a baked-in default if the file is missing or unreadable."""
    here = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    for p in (here / "version.json", Path("version.json")):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f).get("version", "0.0.0")
        except Exception:
            continue
    return "1.1.0"

APP_VERSION = _load_version()

def build_info():
    """Text for the version tooltip. A frozen build's exe file is written at the
    end of the PyInstaller run, so its timestamp is the build date/time (and it
    survives copying the exe)."""
    import datetime
    frozen = getattr(sys, "frozen", False)
    path = sys.executable if frozen else os.path.abspath(__file__)
    try:
        ts = datetime.datetime.fromtimestamp(os.path.getmtime(path))
        # US format, e.g. 10/03/2026 2:31:47 PM
        when = f"{ts:%m/%d/%Y} {ts.hour % 12 or 12}:{ts:%M:%S %p}"
    except OSError:
        when = "unknown"
    if frozen:
        return f"Version {APP_VERSION}\nBuilt: {when}"
    return f"Version {APP_VERSION}\nRunning from source\nScript modified: {when}"
_APPDATA    = Path(os.environ.get("APPDATA", "."))
CONFIG_PATH = _APPDATA / "kamen-sshfs-manager" / "connections.json"
CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

# Colour palettes. apply_theme() copies one into the module globals below, so
# widgets pick up the active theme when they're created.
THEMES = {
    "dark": {
        "BG": "#1E2128", "BG_CARD": "#272B34", "BG_INPUT": "#2E333E",
        "BG_HEAD": "#1A1D24", "FG": "#E8EAF0", "FG_MUTED": "#6B7280",
        "BORDER": "#3A3E4A",
        "ACCENT": "#4C9EEB", "ACCENT_H": "#3A7BC8",
        "SSH_BG": "#2563EB", "SSH_H": "#1D4ED8",
        "SUCCESS": "#22C55E", "SUCCESS_H": "#16A34A",
        "DANGER": "#EF4444", "DANGER_H": "#B91C1C",
        "WARNING": "#F59E0B", "WARNING_H": "#D97706",
        "EXIT_BG": "#7F2020", "EXIT_H": "#A03030",
        "TRAY_BG": "#1E5C2E", "TRAY_H": "#276B38",
        "BANNER_BG": "#3A2E12", "BANNER_FG": "#F59E0B",
        "LOG_BG": "#0D1117", "LOG_FG": "#A3E635",
    },
    "light": {
        "BG": "#F3F4F6", "BG_CARD": "#FFFFFF", "BG_INPUT": "#E5E7EB",
        "BG_HEAD": "#E2E5EA", "FG": "#1F2937", "FG_MUTED": "#6B7280",
        "BORDER": "#C9CED6",
        "ACCENT": "#2F80D8", "ACCENT_H": "#2366B0",
        "SSH_BG": "#1D4ED8", "SSH_H": "#1E40AF",
        "SUCCESS": "#16A34A", "SUCCESS_H": "#15803D",
        "DANGER": "#DC2626", "DANGER_H": "#B91C1C",
        "WARNING": "#B45309", "WARNING_H": "#92400E",
        "EXIT_BG": "#B91C1C", "EXIT_H": "#991B1B",
        "TRAY_BG": "#15803D", "TRAY_H": "#166534",
        "BANNER_BG": "#FEF3C7", "BANNER_FG": "#92400E",
        "LOG_BG": "#FFFFFF", "LOG_FG": "#166534",
    },
}

# Declared here so editors/linters know the names; real values come from apply_theme.
BG = BG_CARD = BG_INPUT = BG_HEAD = FG = FG_MUTED = BORDER = ""
ACCENT = ACCENT_H = SSH_BG = SSH_H = SUCCESS = SUCCESS_H = DANGER = DANGER_H = WARNING = WARNING_H = ""
EXIT_BG = EXIT_H = TRAY_BG = TRAY_H = BANNER_BG = BANNER_FG = LOG_BG = LOG_FG = ""

def apply_theme(name):
    globals().update(THEMES.get(name, THEMES["dark"]))

apply_theme("dark")

FONT       = ("Segoe UI", 10)
FONT_BOLD  = ("Segoe UI", 10, "bold")
FONT_LARGE = ("Segoe UI", 12, "bold")
FONT_SMALL = ("Segoe UI", 9)
FONT_MONO  = ("Consolas", 9)

def get_available_drives():
    """Return list of drive letters from D: onward not currently in use."""
    import string
    used = {f"{c}:" for c in string.ascii_uppercase if os.path.exists(f"{c}:\\")}
    return [f"{c}:" for c in string.ascii_uppercase[3:] if f"{c}:" not in used]


class _DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

_crypt32 = ctypes.windll.crypt32
_kernel32 = ctypes.windll.kernel32
_DPAPI_TAG = "dpapi:"

def _blob_to_bytes(blob):
    data = ctypes.string_at(blob.pbData, blob.cbData)
    _kernel32.LocalFree(blob.pbData)
    return data

def _dpapi_encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    raw = plaintext.encode("utf-8")
    src = _DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)),
                                          ctypes.POINTER(ctypes.c_byte)))
    dst = _DATA_BLOB()
    if not _crypt32.CryptProtectData(ctypes.byref(src), None, None, None, None, 0,
                                     ctypes.byref(dst)):
        raise ctypes.WinError()
    import base64
    return _DPAPI_TAG + base64.b64encode(_blob_to_bytes(dst)).decode("ascii")

def _dpapi_decrypt(token: str) -> str:
    if not token:
        return ""
    if not token.startswith(_DPAPI_TAG):
        return token  # legacy plaintext; will be re-encrypted on next save
    import base64
    raw = base64.b64decode(token[len(_DPAPI_TAG):])
    src = _DATA_BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw, len(raw)),
                                          ctypes.POINTER(ctypes.c_byte)))
    dst = _DATA_BLOB()
    if not _crypt32.CryptUnprotectData(ctypes.byref(src), None, None, None, None, 0,
                                       ctypes.byref(dst)):
        raise ctypes.WinError()
    return _blob_to_bytes(dst).decode("utf-8")


SETTINGS_PATH = CONFIG_PATH.parent / "settings.json"
DEFAULT_SETTINGS = {"theme": "dark", "log_enabled": True, "start_minimized": True}

# "Start with Windows" lives in the registry, not settings.json, so it stays
# correct if the user turns it off in Task Manager -> Startup.
RUN_KEY  = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Kamen SSHFS Manager"
STARTUP_ARG = "--startup"

def _startup_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {STARTUP_ARG}'
    py = Path(sys.executable)
    pyw = py.with_name("pythonw.exe")  # no console window at login
    return f'"{pyw if pyw.exists() else py}" "{Path(__file__).resolve()}" {STARTUP_ARG}'

def get_start_with_windows():
    """The registered startup command, or None if not registered."""
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return winreg.QueryValueEx(k, RUN_NAME)[0]
    except OSError:
        return None

def set_start_with_windows(enabled):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if enabled:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, _startup_command())
        else:
            try: winreg.DeleteValue(k, RUN_NAME)
            except FileNotFoundError: pass

def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            s.update(json.load(f))
    except Exception:
        pass
    if s.get("theme") not in THEMES:
        s["theme"] = "dark"
    return s

def save_settings(settings):
    with open(SETTINGS_PATH, "w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2)

def load_connections():
    if not CONFIG_PATH.exists():
        return []
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            conns = json.load(f)
    except Exception:
        return []
    for c in conns:
        try:
            c["password"] = _dpapi_decrypt(c.get("password", ""))
        except Exception:
            c["password"] = ""
    return conns

def save_connections(conns):
    encrypted = []
    for c in conns:
        copy = dict(c)
        try:
            copy["password"] = _dpapi_encrypt(c.get("password", ""))
        except Exception:
            copy["password"] = ""
        encrypted.append(copy)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(encrypted, f, indent=2)

def new_connection():
    return {"id": str(int(time.time()*1000)), "name": "New Connection",
            "host": "", "port": 22, "user": "", "remote_path": "/",
            "drive_letter": "Z:", "auth_type": "password",
            "password": "", "key_path": "", "extra_args": "",
            "auto_connect": False, "verify_host_key": False}


_procs = {}

def is_mounted(drive):
    return os.path.exists(drive + "\\")

def _notify_explorer_drive_change(drive: str, added: bool):
    """Force Explorer to refresh its drive list. Combines several mechanisms
    because WinFsp drives bypass the normal device-arrival notifications."""
    try:
        shell32 = ctypes.windll.shell32
        user32  = ctypes.windll.user32

        SHCNE_DRIVEADD       = 0x00000100
        SHCNE_DRIVEREMOVED   = 0x00000080
        SHCNE_MEDIAINSERTED  = 0x00000020
        SHCNE_MEDIAREMOVED   = 0x00000040
        SHCNE_UPDATEDIR      = 0x00001000
        SHCNE_ASSOCCHANGED   = 0x08000000
        SHCNF_PATHW          = 0x0005  # path is unicode
        SHCNF_FLUSH          = 0x1000
        SHCNF_FLUSHNOWAIT    = 0x2000

        path = ctypes.c_wchar_p(drive.rstrip("\\") + "\\")

        if added:
            events = [SHCNE_DRIVEADD, SHCNE_MEDIAINSERTED, SHCNE_UPDATEDIR]
        else:
            events = [SHCNE_DRIVEREMOVED, SHCNE_MEDIAREMOVED, SHCNE_UPDATEDIR]

        for ev in events:
            shell32.SHChangeNotify(ev, SHCNF_PATHW | SHCNF_FLUSH, path, None)

        # Broadcast device-change to top-level windows (this is what Windows
        # itself sends when physical media changes - Explorer listens for it).
        WM_DEVICECHANGE              = 0x0219
        DBT_DEVICEARRIVAL            = 0x8000
        DBT_DEVICEREMOVECOMPLETE     = 0x8004
        DBT_DEVTYP_VOLUME            = 0x00000002
        HWND_BROADCAST               = 0xFFFF

        class DEV_BROADCAST_VOLUME(ctypes.Structure):
            _fields_ = [
                ("dbcv_size",       ctypes.c_ulong),
                ("dbcv_devicetype", ctypes.c_ulong),
                ("dbcv_reserved",   ctypes.c_ulong),
                ("dbcv_unitmask",   ctypes.c_ulong),
                ("dbcv_flags",      ctypes.c_ushort),
            ]

        letter = drive.rstrip(":\\").upper()
        if len(letter) == 1 and "A" <= letter <= "Z":
            unitmask = 1 << (ord(letter) - ord("A"))
            dbv = DEV_BROADCAST_VOLUME(
                ctypes.sizeof(DEV_BROADCAST_VOLUME),
                DBT_DEVTYP_VOLUME, 0, unitmask, 0)
            wparam = DBT_DEVICEARRIVAL if added else DBT_DEVICEREMOVECOMPLETE
            user32.SendMessageTimeoutW(
                HWND_BROADCAST, WM_DEVICECHANGE, wparam,
                ctypes.byref(dbv), 0x0002, 1000, None)  # SMTO_ABORTIFHUNG
    except Exception:
        pass

def _find_sshfs():
    for p in [r"C:\Program Files\SSHFS-Win\bin\sshfs.exe",
              r"C:\Program Files (x86)\SSHFS-Win\bin\sshfs.exe"]:
        if os.path.exists(p):
            return p
    # `where` also searches the CWD and matches case-insensitively, so a frozen
    # build named "SSHFS.exe" would find *itself* and spawn new app windows in
    # a loop. Skip anything that resolves to our own executable.
    self_exe = os.path.normcase(os.path.realpath(sys.executable))
    try:
        r = subprocess.run(["where","sshfs"], capture_output=True, text=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            for p in r.stdout.strip().splitlines():
                p = p.strip()
                if p and os.path.normcase(os.path.realpath(p)) != self_exe:
                    return p
    except Exception:
        pass
    return None

WINFSP_URL    = "https://github.com/winfsp/winfsp/releases"
SSHFS_WIN_URL = "https://github.com/winfsp/sshfs-win/releases"

def _winfsp_installed():
    """WinFsp is the filesystem driver SSHFS-Win mounts through. Its installer
    records InstallDir under HKLM\\SOFTWARE\\WinFsp (32-bit registry view)."""
    try:
        import winreg
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WinFsp",
                                    0, winreg.KEY_READ | view) as k:
                    d, _ = winreg.QueryValueEx(k, "InstallDir")
                    if d and os.path.isdir(d):
                        return True
            except OSError:
                continue
    except Exception:
        pass
    return any(os.path.isdir(p) for p in (r"C:\Program Files (x86)\WinFsp",
                                          r"C:\Program Files\WinFsp"))

def missing_requirements():
    """Return [(name, download_url)] for each required component not installed,
    in the order they must be installed."""
    missing = []
    if not _winfsp_installed():
        missing.append(("WinFsp", WINFSP_URL))
    if not _find_sshfs():
        missing.append(("SSHFS-Win", SSHFS_WIN_URL))
    return missing

# ssh.exe can't take a password non-interactively, so probing uses paramiko.

def _password_auth(transport, user, password, prompt):
    """Password auth that also handles keyboard-interactive (incl. 2FA):
    the saved password answers the first password prompt, anything else
    goes to `prompt` (or gets the password again if there's no prompt)."""
    import paramiko
    used = [False]
    def handler(title, instructions, prompts):
        answers = []
        for text, echo in prompts:
            if "password" in text.lower() and password and not used[0]:
                used[0] = True
                answers.append(password)
            elif prompt:
                ans = prompt("text" if echo else "secret", text)
                if ans is None:
                    raise paramiko.AuthenticationException("Cancelled.")
                answers.append(ans)
            else:
                answers.append(password or "")
        return answers
    try:
        remaining = transport.auth_password(user, password or "", fallback=False)
    except paramiko.BadAuthenticationType as e:
        if "keyboard-interactive" not in e.allowed_types:
            raise
        remaining = ["keyboard-interactive"]
    except paramiko.AuthenticationException:
        if not prompt:
            raise
        used[0] = True  # saved password was wrong: ask instead
        remaining = ["keyboard-interactive"]
    if not transport.is_authenticated():
        if "keyboard-interactive" not in (remaining or []):
            raise paramiko.AuthenticationException("Authentication failed.")
        transport.auth_interactive(user, handler)

class _AskHostKeyPolicy:
    """Unknown host key -> ask via `prompt`; if trusted, append it to known_hosts."""
    def __init__(self, prompt, known_hosts):
        self.prompt, self.known_hosts = prompt, known_hosts
    def missing_host_key(self, client, hostname, key):
        import base64, hashlib, paramiko
        fp = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        text = (f"The authenticity of host '{hostname}' can't be established.\n"
                f"{key.get_name()} key fingerprint is SHA256:{fp}.\n"
                f"Are you sure you want to continue connecting (yes/no)?")
        if self.prompt("confirm", text) is None:
            raise paramiko.SSHException("Host key not trusted.")
        self.known_hosts.parent.mkdir(parents=True, exist_ok=True)
        with open(self.known_hosts, "a", encoding="utf-8") as f:
            f.write(f"{hostname} {key.get_name()} {key.get_base64()}\n")
        client.get_host_keys().add(hostname, key.get_name(), key)

class SSHProbe:
    """Short-lived SSH/SFTP session built from a connection dict.
    Raises RuntimeError with a human-readable message on failure."""

    def __init__(self, conn, password=None, timeout=10, prompt=None):
        """`prompt(kind, text)` -> answer or None; kind is "confirm", "secret"
        or "text". Called from this (worker) thread for 2FA questions and
        unknown host keys; without it those fail as before."""
        try:
            import paramiko
        except Exception as e:
            raise RuntimeError(f"Can't load the 'paramiko' SSH library ({e}) — "
                               f"run: pip install paramiko")
        self._paramiko = paramiko
        client = paramiko.SSHClient()
        if conn.get("verify_host_key", False):
            kh = Path.home() / ".ssh" / "known_hosts"
            if kh.exists():
                client.load_host_keys(str(kh))
            client.set_missing_host_key_policy(
                _AskHostKeyPolicy(prompt, kh) if prompt else paramiko.RejectPolicy())
        else:
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        kw = dict(hostname=bare_host(conn["host"]), port=int(conn.get("port", 22)),
                  username=conn["user"], timeout=timeout, banner_timeout=timeout,
                  auth_timeout=timeout, allow_agent=False, look_for_keys=False)
        if conn.get("auth_type") == "key":
            if not conn.get("key_path"):
                raise RuntimeError("No private key file selected.")
            kw["key_filename"] = conn["key_path"]
        pw = None
        if conn.get("auth_type") != "key":
            pw = password if password is not None else conn.get("password", "")

        try:
            try:
                client.connect(**kw)  # key auth here; password auth below
            except paramiko.SSHException as e:
                if kw.get("key_filename") or "No authentication methods" not in str(e):
                    raise
                _password_auth(client.get_transport(), conn["user"], pw, prompt)
            self.sftp = client.open_sftp()
        except paramiko.AuthenticationException:
            client.close()
            raise RuntimeError("Authentication failed — check username and password/key.")
        except paramiko.SSHException as e:
            client.close()
            msg = str(e)
            if "not found in known_hosts" in msg:
                msg = ("Server's host key is not in your known_hosts file. Untick "
                       "'Verify SSH host key', or connect once with ssh to trust it.")
            raise RuntimeError(msg)
        except OSError as e:
            client.close()
            raise RuntimeError(f"Can't reach {conn['host']}:{kw['port']} — "
                               f"{e.strerror or e}")
        self.client = client
        self.home = self.sftp.normalize(".")

    def resolve(self, path):
        """Absolute path as sshfs sees it: relative paths are under $HOME."""
        path = (path or "").strip() or "."
        return self.sftp.normalize(path)

    def list_dirs(self, path):
        import stat as _stat
        out = []
        for a in self.sftp.listdir_attr(path):
            if a.filename.startswith("."):
                continue
            if a.st_mode is not None and _stat.S_ISDIR(a.st_mode):
                out.append(a.filename)
            elif a.st_mode is not None and _stat.S_ISLNK(a.st_mode):
                # Symlinks to directories count (sshfs runs with follow_symlinks)
                try:
                    full = path.rstrip("/") + "/" + a.filename
                    if _stat.S_ISDIR(self.sftp.stat(full).st_mode):
                        out.append(a.filename)
                except Exception:
                    pass
        return sorted(out, key=str.lower)

    def close(self):
        try: self.client.close()
        except Exception: pass


def bare_host(host):
    """Host without surrounding [brackets], which users may type for IPv6."""
    host = (host or "").strip()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host

def sshfs_host(host):
    """Host for sshfs's user@host:path syntax: IPv6 literals (which contain
    colons, e.g. fe80::1%12) must be bracketed or sshfs splits them wrongly."""
    h = bare_host(host)
    return f"[{h}]" if ":" in h else h

def _find_ssh_client():
    """Prefer Windows' built-in OpenSSH (native console support), then any
    ssh on PATH, then the Cygwin ssh bundled with SSHFS-Win."""
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [os.path.join(sysroot, "System32", "OpenSSH", "ssh.exe")]
    import shutil
    on_path = shutil.which("ssh")
    if on_path:
        candidates.append(on_path)
    candidates.append(r"C:\Program Files\SSHFS-Win\bin\ssh.exe")
    self_exe = os.path.normcase(os.path.realpath(sys.executable))
    for c in candidates:
        if os.path.exists(c) and os.path.normcase(os.path.realpath(c)) != self_exe:
            return c
    return None

# SSH_ASKPASS support: ssh runs this app as its password helper. ssh only
# learns the connection id (via env); the helper reads the saved password from
# the DPAPI-encrypted config itself, so the password never sits in the
# environment or on a command line.
ASKPASS_ENV = "KAMEN_SSHFS_ASKPASS_ID"

def _askpass_program():
    """Program ssh should run as SSH_ASKPASS. SSH_ASKPASS takes a bare path
    (no arguments), so source runs go through a small .cmd wrapper."""
    if getattr(sys, "frozen", False):
        return sys.executable
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":  # needs a console-subsystem python for stdout
        py = py.with_name("python.exe")
    wrapper = CONFIG_PATH.parent / "askpass.cmd"
    script = Path(__file__).resolve()
    wrapper.write_text(f'@"{py}" "{script}" %*\r\n', encoding="utf-8")
    return str(wrapper)

def _process_parents():
    """{pid: (parent_pid, exe_name)} for all processes (Toolhelp snapshot)."""
    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD),
                    ("th32ProcessID", wt.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                    ("th32ModuleID", wt.DWORD), ("cntThreads", wt.DWORD),
                    ("th32ParentProcessID", wt.DWORD), ("pcPriClassBase", ctypes.c_long),
                    ("dwFlags", wt.DWORD), ("szExeFile", ctypes.c_wchar * 260)]
    k32 = ctypes.windll.kernel32
    k32.CreateToolhelp32Snapshot.restype = wt.HANDLE
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    out = {}
    e = PROCESSENTRY32W(); e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        out[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return out

def _calling_ssh_pid():
    """PID of the ssh.exe that launched this askpass helper (it may sit behind
    cmd.exe or the PyInstaller bootloader), or 0 if not found."""
    try:
        procs = _process_parents()
        pid = os.getpid()
        for _ in range(4):
            pid = procs.get(pid, (0, ""))[0]
            if procs.get(pid, (0, ""))[1] == "ssh.exe":
                return pid
    except Exception:
        pass
    return 0

def _saved_password_already_tried():
    """True the second time the same ssh process asks for a password, so a
    wrong saved password falls through to the popup instead of looping."""
    pid = _calling_ssh_pid()
    if not pid:
        return False
    marker = Path(os.environ.get("TEMP", ".")) / f"kamen-askpass-{pid}.used"
    try:
        if marker.exists() and time.time() - marker.stat().st_mtime < 600:
            return True
        marker.write_text("")
        for old in marker.parent.glob("kamen-askpass-*.used"):  # tidy up
            if time.time() - old.stat().st_mtime > 3600:
                old.unlink()
    except OSError:
        pass
    return False

def _askpass_main(prompt):
    """Answer one ssh prompt (ssh runs this app as SSH_ASKPASS):
    - host-key "(yes/no)?" questions -> Trust / Cancel popup
    - password prompts -> the saved password (once per ssh process)
    - anything else (2FA codes, passphrases, Ask Each Time) -> input popup
    Cancel exits 1, which ssh treats as no answer."""
    cid = os.environ.get(ASKPASS_ENV, "")
    conn = next((c for c in load_connections() if c.get("id") == cid), {}) or {}
    low = prompt.lower()
    # Host-key question. Match its first line too: from source the helper runs
    # via askpass.cmd, and cmd.exe cuts multi-line arguments at the first line.
    if "(yes/no" in low or "authenticity of host" in low:
        kind = "confirm"
    elif ("password" in low and conn.get("auth_type") == "password"
          and conn.get("password") and not _saved_password_already_tried()):
        return _write_stdout(conn["password"])
    else:
        kind = "secret" if any(w in low for w in ("password", "passphrase", "pin")) else "text"
    apply_theme(load_settings()["theme"])
    root = tk.Tk()
    root.withdraw()
    try:
        from PIL import ImageTk
        root._icon = ImageTk.PhotoImage(_make_logo(32))
        root.iconphoto(True, root._icon)
    except Exception:
        pass
    result = [None]
    def done(value):
        result[0] = value
        root.quit()
    PromptDialog(root, kind, prompt, _conn_label(conn), done)
    root.mainloop()
    if result[0] is None:
        return 1
    return _write_stdout("yes" if kind == "confirm" else result[0])

def _conn_label(conn):
    if not conn:
        return ""
    return f"{conn.get('name', '')} — {conn.get('user', '')}@{bare_host(conn.get('host', ''))}"

def _write_stdout(text):
    data = (text + "\n").encode("utf-8")
    # Write to the raw stdout handle: a windowed (frozen) build has no sys.stdout.
    k32 = ctypes.windll.kernel32
    k32.GetStdHandle.restype = wt.HANDLE
    h = k32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    written = wt.DWORD()
    ok = k32.WriteFile(h, data, len(data), ctypes.byref(written), None)
    return 0 if ok and written.value == len(data) else 1

def open_ssh_terminal(conn):
    """Open a console window with an interactive SSH session, starting in the
    connection's remote path. Raises RuntimeError if no ssh client exists."""
    ssh = _find_ssh_client()
    if not ssh:
        raise RuntimeError("No SSH client found. Install Windows' OpenSSH Client "
                           "(Settings → Apps → Optional features).")
    args = [ssh, "-p", str(conn.get("port", 22))]
    if not conn.get("verify_host_key", False):
        args += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    if conn.get("auth_type") == "key" and conn.get("key_path"):
        args += ["-i", conn["key_path"], "-o", "IdentitiesOnly=yes"]
    env = None
    if conn.get("auth_type") == "password" and conn.get("password"):
        # Fill in the saved password via SSH_ASKPASS. The helper uses it once;
        # further prompts (wrong password, 2FA codes, new host keys) become
        # popups. Other auth types prompt in the terminal as usual.
        env = os.environ.copy()
        env.update({"SSH_ASKPASS": _askpass_program(),
                    "SSH_ASKPASS_REQUIRE": "force",
                    ASKPASS_ENV: conn["id"]})
    args += ["-t", f"{conn['user']}@{bare_host(conn['host'])}"]

    # Start the shell in the mounted folder. Skip characters cmd.exe would
    # still interpret inside quotes (" and %), and fall back to $HOME.
    remote = conn.get("remote_path", "") or ""
    if remote and remote != "/" and not any(ch in remote for ch in '"%'):
        import shlex
        args.append(f"cd {shlex.quote(remote)} 2>/dev/null; exec $SHELL -l")

    # cmd /s /c "...": keep the window open (pause) only if ssh fails, so
    # errors like "Permission denied" stay readable instead of vanishing.
    title = "".join(ch for ch in f"SSH - {conn.get('name', 'SSH')}"
                    if ch.isalnum() or ch in " -_.@")
    cmdline = (f'cmd.exe /s /c "title {title} & {subprocess.list2cmdline(args)}'
               f' || pause"')
    subprocess.Popen(cmdline, env=env, creationflags=subprocess.CREATE_NEW_CONSOLE)

def connect(conn, log=None):
    cid, drive = conn["id"], conn["drive_letter"]
    if is_mounted(drive):
        if log: log(f"[INFO] {drive} already mounted.\n")
        return True

    sshfs = _find_sshfs()
    if not sshfs:
        if log: log(f"[ERROR] sshfs.exe not found. Install WinFsp ({WINFSP_URL}), "
                    f"then SSHFS-Win ({SSHFS_WIN_URL}).\n")
        return False

    # Use sshfs.exe not sshfs-win.exe
    if sshfs.endswith("sshfs-win.exe"):
        sshfs = sshfs.replace("sshfs-win.exe", "sshfs.exe")

    auth   = conn["auth_type"]
    host   = sshfs_host(conn["host"])
    port   = conn.get("port", 22)
    user   = conn["user"]
    remote = conn.get("remote_path", "/")
    name   = conn.get("name", "SSHFS")[:32]

    args = [
        sshfs,
        f"{user}@{host}:{remote}",
        drive,
        f"-p{port}",
        f"-ovolname={name}",
        "-oidmap=user",
        "-ouid=-1",
        "-ogid=-1",
        "-oumask=000",
        "-ocreate_umask=000",
        "-omax_readahead=1GB",
        "-oallow_other",
        "-olarge_read",
        "-okernel_cache",
        "-ofollow_symlinks",
    ]
    # Debug logging is opt-in per connection (extra_args: "-odebug -ologlevel=debug1")
    # Default-on debug was making file ops synchronous -> 38MB took forever and
    # the log flood froze the UI.

    if not conn.get("verify_host_key", False):
        args += ["-oStrictHostKeyChecking=no", "-oUserKnownHostsFile=/dev/null"]

    # All prompts (password, 2FA codes, key passphrases, new host keys) go to
    # ssh's SSH_ASKPASS helper - this app - which answers with the saved
    # password or asks the user in a popup.
    if auth in ("password", "ask"):
        args += ["-oPreferredAuthentications=password,keyboard-interactive"]
    elif auth == "key" and conn.get("key_path"):
        args += ["-oPreferredAuthentications=publickey,keyboard-interactive",
                 f"-oIdentityFile={conn['key_path'].replace(chr(92), '/')}"]

    for opt in conn.get("extra_args", "").split():
        args.append(opt)

    if log:
        safe = " ".join(
            a.replace(conn.get("password", ""), "********") if conn.get("password") else a
            for a in args
        )
        log(f"[CMD] {safe}\n")

    # Include full PATH plus the sshfs bin dir
    import os.path
    sshfs_dir = os.path.dirname(sshfs)
    env = os.environ.copy()
    env["PATH"] = sshfs_dir + os.pathsep + env.get("PATH", "")
    env.update({"SSH_ASKPASS": _askpass_program(), "SSH_ASKPASS_REQUIRE": "force",
                ASKPASS_ENV: conn["id"]})

    try:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        _procs[cid] = proc

        result = [None]    # success flag set when mount is detected
        mounted = [False]  # flips True after mount - we stop logging then

        def stream(pipe, prefix):
            """Log lines while the mount is being established. Once mounted,
            keep draining the pipe silently - if we don't read it, sshfs blocks
            on full OS pipe buffers and file ops grind to a halt."""
            try:
                for line in pipe:
                    if mounted[0]:
                        continue  # drain only - no UI dispatch
                    txt = line.decode(errors="replace").rstrip()
                    if "service sshfs has been started" in txt:
                        result[0] = True
                    if log: log(f"{prefix} {txt}\n")
                    if log and txt.endswith(f":{remote}: No such file or directory"):
                        log(f"[HINT] Remote path '{remote}' doesn't exist on the server. "
                            f"Use Edit → Test Connection → Browse… to pick one.\n")
            except Exception: pass

        threading.Thread(target=stream, args=(proc.stderr, "[SSHFS]"), daemon=True).start()
        threading.Thread(target=stream, args=(proc.stdout, "[OUT]"),   daemon=True).start()

        if log: log(f"[INFO] Waiting for {drive} to mount "
                    f"(answer any login prompt that pops up)...\n")
        # Up to 2 minutes, so there's time to type a 2FA code; an unreachable
        # server makes sshfs exit sooner, which ends the wait early.
        for i in range(240):
            time.sleep(0.5)
            if result[0] or is_mounted(drive):
                if log: log(f"[OK] {drive} mounted as '{name}'.\n")
                mounted[0] = True  # stop log streaming; threads keep draining
                _notify_explorer_drive_change(drive, added=True)
                return True
            if proc.poll() is not None:
                if log: log(f"[ERROR] sshfs exited (code {proc.returncode}).\n")
                return False
            if i > 0 and i % 20 == 0:
                if log: log(f"[INFO] Still waiting... ({i//2}s)\n")

        proc.terminate()
        if log: log(f"[ERROR] Timed out after 120s.\n")
        return False

    except Exception as e:
        if log: log(f"[ERROR] {e}\n")
        return False

def _find_sshfs_pids_for_drive(drive: str):
    """Return PIDs of sshfs.exe processes whose command line targets `drive`.
    Uses PowerShell's Get-CimInstance because wmic was removed in newer Windows."""
    drive_letter = drive.rstrip(":\\").upper()
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name='sshfs.exe'\" | "
        "Select-Object ProcessId,CommandLine | ConvertTo-Csv -NoTypeInformation"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
            creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    except Exception:
        return []
    pids = []
    import csv, io
    rows = list(csv.reader(io.StringIO(r.stdout or "")))
    if len(rows) < 2:
        return []
    for row in rows[1:]:  # skip header
        if len(row) < 2:
            continue
        pid, cmdline = row[0], row[1] or ""
        # Match the drive letter as a standalone token in the command line
        # e.g. "... user@host:/path Z: -p22 ..."  ->  match " Z: " or trailing " Z:"
        cl = cmdline.upper()
        token = f" {drive_letter}:"
        if (token + " ") in (cl + " ") and pid.isdigit() and pid != "0":
            pids.append(pid)
    return pids

def _wait_unmounted(drive, timeout=1.0):
    """Poll until `drive` disappears (or timeout). True if it's gone."""
    end = time.time() + timeout
    while is_mounted(drive):
        if time.time() >= end:
            return False
        time.sleep(0.1)
    return True

def disconnect(conn, log=None):
    cid, drive = conn["id"], conn["drive_letter"]
    if log: log(f"[INFO] Disconnecting {drive}...\n")

    # 1. Kill the tracked process for this connection only
    proc = _procs.pop(cid, None)
    terminated = False
    if proc and proc.poll() is None:
        terminated = True
        if log: log(f"[INFO] Terminating tracked sshfs process (PID {proc.pid})...\n")
        # Kill the whole tree: the tracked sshfs.exe is a launcher and the
        # process actually serving the mount is its child.
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        try: proc.wait(timeout=3)
        except Exception: proc.kill()

    # 2. If still mounted (e.g. orphaned from a previous run), find sshfs
    #    processes whose command line targets THIS drive specifically.
    #    (sshfs normally daemonizes after mounting, so the tracked process
    #    has usually exited already - only wait if we actually stopped it.)
    if not _wait_unmounted(drive, 1.0 if terminated else 0):
        pids = _find_sshfs_pids_for_drive(drive)
        for pid in pids:
            if log: log(f"[INFO] Killing orphaned sshfs.exe PID {pid} for {drive}...\n")
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if pids:
            _wait_unmounted(drive, 2.0)

    mounted = is_mounted(drive)
    if log: log(f"[INFO] {drive} {'still mounted' if mounted else 'unmounted successfully'}.\n")
    if not mounted:
        _notify_explorer_drive_change(drive, added=False)
    return not mounted


def _bind_mousewheel(widget, on_scroll, container=None):
    """Route mousewheel events to `widget` whenever the cursor is over it
    or any of its descendants. Pass `container` (a parent Frame) to also
    catch wheel events from sibling/child widgets inside it.
    on_scroll receives the raw event.delta (Windows: ±120 per notch)."""
    def wheel(e):
        try:
            first, last = widget.yview()
            if first <= 0.0 and last >= 1.0:
                return "break"
        except Exception:
            pass
        on_scroll(e.delta)
        return "break"
    target = container if container is not None else widget
    # Bind on Enter/Leave at the *container* level so wheel events from
    # any descendant widget (e.g. cards inside a scrolling canvas) work.
    def enter(_): target.bind_all("<MouseWheel>", wheel, add="+")
    def leave(_): target.unbind_all("<MouseWheel>")
    target.bind("<Enter>", enter)
    target.bind("<Leave>", leave)


def _make_logo(size: int = 64):
    """Stylized 'K' monogram - dark rounded square + accent-blue letterform.
    Returns a PIL Image, or None if PIL is unavailable.
    PIL's ImageDraw doesn't anti-alias, so draw at 8x and downsample with
    LANCZOS to get smooth diagonals and corners."""
    try:
        from PIL import Image
    except Exception:
        return None
    ss = 8
    return _draw_logo(size, ss).resize((size, size), Image.LANCZOS)

LOGO_BLUE = "#4C9EEB"  # brand colour, same in both themes
LOGO_BG   = "#22262F"

def _draw_logo(size: int, ss: int = 1):
    """Draw the logo for a `size`-px icon on a canvas `ss` times larger.
    Minimum stroke widths are in final pixels, so small icons keep a
    visible border after downsampling."""
    from PIL import Image, ImageDraw
    S = size * ss
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    r = size * 0.2 * ss  # proportional, so 16px icons stay square-ish
    border = max(1.5, size / 32) * ss
    inset = ss // 2
    d.rounded_rectangle((inset, inset, S - 1 - inset, S - 1 - inset), radius=r,
                        fill=LOGO_BG, outline=LOGO_BLUE, width=round(border))

    # K geometry: vertical stem + two diagonals meeting at mid-right
    top    = S * 0.20
    bot    = S * 0.80
    stem_x = S * 0.30
    stem_w = max(2, size * 0.10) * ss
    mid_y  = S * 0.50
    apex_x = S * 0.74

    # Vertical stem
    d.rounded_rectangle((stem_x, top, stem_x + stem_w, bot),
                        radius=stem_w/2, fill=LOGO_BLUE)
    # Upper diagonal (mid-right of stem -> top-right)
    d.polygon([
        (stem_x + stem_w*0.6, mid_y - stem_w*0.4),
        (apex_x - stem_w*0.6, top),
        (apex_x + stem_w*0.4, top + stem_w*0.6),
        (stem_x + stem_w*1.4, mid_y + stem_w*0.4),
    ], fill=LOGO_BLUE)
    # Lower diagonal (mid-right of stem -> bottom-right)
    d.polygon([
        (stem_x + stem_w*0.6, mid_y + stem_w*0.4),
        (stem_x + stem_w*1.4, mid_y - stem_w*0.4),
        (apex_x + stem_w*0.4, bot - stem_w*0.6),
        (apex_x - stem_w*0.6, bot),
    ], fill=LOGO_BLUE)
    return img


def mk_btn(parent, text, cmd, bg=None, fg=None, width=None, active_fg=None):
    if bg is None: bg = BG_INPUT
    if fg is None: fg = FG if bg == BG_INPUT else "white"
    hov_map = {BG_INPUT: BORDER, ACCENT: ACCENT_H, DANGER: DANGER_H, SUCCESS: SUCCESS_H,
               WARNING: WARNING_H, EXIT_BG: EXIT_H, TRAY_BG: TRAY_H, SSH_BG: SSH_H}
    afg = active_fg if active_fg else fg
    kw = dict(text=text, command=cmd, bg=bg, fg=fg, relief="flat", bd=0,
              cursor="hand2", font=FONT, padx=10, pady=4,
              activebackground=hov_map.get(bg, BORDER), activeforeground=afg)
    if width: kw["width"] = width
    b = tk.Button(parent, **kw)

    # Patch configure to track the *intended* (non-hover) bg/fg so callers
    # like _set() can update colors without us losing the originals.
    orig_configure = b.configure
    def patched_configure(*args, **kwargs):
        if "bg" in kwargs:
            b._orig_bg = kwargs["bg"]
            kwargs["activebackground"] = hov_map.get(kwargs["bg"], BORDER)
        if "fg" in kwargs:
            b._orig_fg = kwargs["fg"]
            kwargs["activeforeground"] = kwargs["fg"]
        return orig_configure(*args, **kwargs)
    b.configure = patched_configure
    b._orig_bg = bg
    b._orig_fg = fg

    def on_enter(e):
        if str(b.cget("state")) == "disabled":
            return
        # Use raw configure so we don't overwrite _orig_bg/_orig_fg
        orig_configure(bg=hov_map.get(b._orig_bg, BORDER), fg=b._orig_fg,
                       cursor="hand2")
    def on_leave(e):
        orig_configure(bg=b._orig_bg, fg=b._orig_fg,
                       cursor="" if str(b.cget("state")) == "disabled" else "hand2")

    b.bind("<Enter>", on_enter)
    b.bind("<Leave>", on_leave)
    return b

def mk_entry(parent, show=None, width=28):
    e = tk.Entry(parent, bg=BG_INPUT, fg=FG, insertbackground=FG,
                 relief="flat", bd=4, font=FONT, width=width)
    if show: e.configure(show=show)
    return e

def mk_label(parent, text, fg=None, font=FONT, **kw):
    bg = parent.cget("bg")
    if fg is None: fg = FG
    return tk.Label(parent, text=text, bg=bg, fg=fg, font=font, **kw)


class PromptDialog(tk.Toplevel):
    """Popup for an SSH question. kind: "confirm" (trust a host key),
    "secret" (masked input) or "text". Calls on_done(answer or None)."""

    def __init__(self, parent, kind, prompt, subtitle, on_done):
        super().__init__(parent)
        self.withdraw()
        self.kind, self.on_done = kind, on_done
        self._answered = False
        self.title("Trust this server?" if kind == "confirm" else "SSH login")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.attributes("-topmost", True)
        if parent.winfo_viewable():
            self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: self._finish(None))
        self.bind("<Escape>", lambda e: self._finish(None))

        text = prompt.strip()
        if kind == "confirm":  # drop ssh's "(yes/no)?" line: the buttons ask that
            lines = [l for l in text.splitlines() if "(yes/no" not in l.lower()]
            text = "\n".join(lines).strip()
            if "fingerprint" not in text.lower():
                text += ("\n\n(The key fingerprint wasn't passed through — this "
                         "happens when running from source. Check it with ssh first.)")
        mk_label(self, "Trust this server?" if kind == "confirm" else "SSH login",
                 font=FONT_LARGE).pack(anchor="w", padx=20, pady=(16, 2))
        if subtitle:
            mk_label(self, subtitle, fg=FG_MUTED, font=FONT_SMALL).pack(
                anchor="w", padx=20)
        tk.Label(self, text=text, bg=BG, fg=FG, font=FONT, justify="left",
                 anchor="w", wraplength=440).pack(anchor="w", padx=20, pady=(10, 6))

        if kind == "confirm":
            tk.Label(self, text="Only trust it if the fingerprint matches your server.",
                     bg=BG, fg=FG_MUTED, font=FONT_SMALL).pack(anchor="w", padx=20)
        else:
            self.entry = mk_entry(self, show="●" if kind == "secret" else None, width=40)
            self.entry.pack(fill="x", padx=20, pady=(0, 4))
            self.entry.bind("<Return>", lambda e: self._ok())

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=20, pady=(14, 16))
        mk_btn(bar, "Trust" if kind == "confirm" else "OK", self._ok,
               bg=ACCENT).pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", lambda: self._finish(None)).pack(side="right")
        self.minsize(380, 0)
        show_centered(self, parent)
        self.lift()
        self.focus_force()
        if kind != "confirm":
            self.entry.focus_set()

    def _ok(self):
        self._finish("yes" if self.kind == "confirm" else self.entry.get())

    def _finish(self, value):
        if self._answered:
            return
        self._answered = True
        self.destroy()
        self.on_done(value)

def prompt_from_thread(widget, subtitle=""):
    """A prompt(kind, text) callable for SSHProbe that shows a PromptDialog on
    the UI thread and blocks the calling worker thread until it's answered."""
    def prompt(kind, text):
        ev, res = threading.Event(), [None]
        def done(value):
            res[0] = value
            ev.set()
        try:
            widget.after(0, lambda: PromptDialog(widget, kind, text, subtitle, done))
        except Exception:
            return None
        ev.wait(600)
        return res[0]
    return prompt


class Tooltip:
    """Small hover tooltip: appears after `delay` ms over `widget`, hides on leave."""

    def __init__(self, widget, text, delay=800):
        self.widget, self.text, self.delay = widget, text, delay
        self._job = None
        self._tip = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _=None):
        self._cancel()
        self._job = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._job:
            self.widget.after_cancel(self._job)
            self._job = None

    def _show(self):
        self._job = None
        if self._tip or not self.widget.winfo_exists():
            return
        tip = self._tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)  # no title bar
        tip.attributes("-topmost", True)
        tk.Label(tip, text=self.text, bg=BG_CARD, fg=FG, font=FONT_SMALL,
                 justify="left", padx=8, pady=5,
                 highlightthickness=1, highlightbackground=BORDER).pack()
        tip.update_idletasks()
        # Above the pointer and kept on screen (the version label sits at the
        # bottom-right of the window, so "below" would often be off-screen).
        w, h = tip.winfo_reqwidth(), tip.winfo_reqheight()
        x = self.widget.winfo_pointerx() - w // 2
        y = self.widget.winfo_pointery() - h - 12
        sw = tip.winfo_screenwidth()
        tip.geometry(f"+{max(0, min(x, sw - w))}+{max(0, y)}")

    def _hide(self, _=None):
        self._cancel()
        if self._tip:
            self._tip.destroy()
            self._tip = None


class DarkScrollbar(tk.Canvas):
    """Slim dark scrollbar - replaces the white default ttk one."""
    def __init__(self, parent, command, autohide=True, **kw):
        super().__init__(parent, width=8, bg=BG_CARD, highlightthickness=0, **kw)
        self._command = command
        self._autohide = autohide
        self._thumb = self.create_rectangle(2, 0, 6, 40, fill=BORDER, outline="", tags="thumb")
        self.tag_bind("thumb", "<ButtonPress-1>", self._press)
        self.tag_bind("thumb", "<B1-Motion>",     self._drag)
        self.tag_bind("thumb", "<Enter>",  lambda e: self.itemconfig("thumb", fill=FG_MUTED))
        self.tag_bind("thumb", "<Leave>",  lambda e: self.itemconfig("thumb", fill=BORDER))
        self._drag_y = 0
        self._top = 0.0

    def set(self, top, bottom):
        self._top = float(top)
        h = self.winfo_height() or 200
        y0 = int(float(top)    * h)
        y1 = int(float(bottom) * h)
        self.coords("thumb", 2, y0, 6, max(y1, y0 + 20))
        # Auto-hide when content fits the viewport (opt-in)
        if not self._autohide:
            return
        fits = (float(top) <= 0.0 and float(bottom) >= 1.0)
        info = self.pack_info() if self.winfo_manager() == "pack" else None
        if fits and info:
            self._pack_info = info
            self.pack_forget()
        elif not fits and getattr(self, "_pack_info", None) and not info:
            self.pack(**self._pack_info)

    def _press(self, e):
        self._drag_y = e.y

    def _drag(self, e):
        h = self.winfo_height() or 200
        self._command("moveto", self._top + (e.y - self._drag_y) / h)
        self._drag_y = e.y

def show_centered(win, parent, size=None):
    """Place a (withdrawn) dialog centred over `parent`, then show it.
    Without this, Windows drops new Toplevels in the top-left corner.
    `size` = (w, h) for dialogs with a fixed geometry; otherwise the
    dialog's natural size is used."""
    win.update_idletasks()
    if size:
        w, h = size
    else:
        min_w, min_h = win.minsize()
        w = max(win.winfo_reqwidth(), min_w)
        h = max(win.winfo_reqheight(), min_h)
    sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
    if parent.winfo_viewable():
        x = parent.winfo_rootx() + (parent.winfo_width() - w) // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - h) // 2
        # geometry() positions the outer frame, but w/h are the client area:
        # subtract the title bar / border size (same as the parent's).
        x -= parent.winfo_rootx() - parent.winfo_x()
        y -= parent.winfo_rooty() - parent.winfo_y()
    else:  # parent hidden (e.g. minimised to tray): centre on screen
        x, y = (sw - w) // 2, (sh - h) // 2
    x = max(0, min(x, sw - w))
    y = max(0, min(y, sh - h - 40))  # keep clear of the taskbar
    win.geometry(f"{w}x{h}+{x}+{y}" if size else f"+{x}+{y}")
    win.deiconify()
    win.grab_set()
    win.focus_set()


class Card(tk.Frame):
    def __init__(self, parent, conn, mgr):
        super().__init__(parent, bg=BG_CARD,
                         highlightbackground=BORDER, highlightthickness=1)
        self.conn, self.mgr = conn, mgr
        self._status = "disconnected"
        self._build()
        self._refresh()

    def _build(self):
        # Dot
        self.cv = tk.Canvas(self, width=14, height=14, bg=BG_CARD, highlightthickness=0)
        self.cv.pack(side="left", padx=(14,8), pady=16)
        self._dot = self.cv.create_oval(2,2,12,12, fill=FG_MUTED, outline="")

        # Info
        info = tk.Frame(self, bg=BG_CARD)
        info.pack(side="left", fill="both", expand=True, pady=10)
        self.lbl_name = mk_label(info, self.conn["name"], font=FONT_BOLD, anchor="w")
        self.lbl_name.pack(anchor="w")
        host_row = tk.Frame(info, bg=BG_CARD)
        host_row.pack(anchor="w")
        self.lbl_host = mk_label(host_row, self._host_part(), fg=FG_MUTED,
                                 font=FONT_SMALL, anchor="w")
        self.lbl_host.pack(side="left")
        self.lbl_drive = mk_label(host_row, self.conn["drive_letter"],
                                  fg=SUCCESS, font=FONT_BOLD, anchor="w")
        self.lbl_drive.pack(side="left", padx=(2, 0))

        # Buttons
        bf = tk.Frame(self, bg=BG_CARD)
        bf.pack(side="right", padx=10)
        mk_btn(bf, "SSH", self._ssh, bg=SSH_BG).pack(side="left", padx=3)
        self.btn_con = mk_btn(bf, "Connect", self._toggle, bg=ACCENT, fg="white")
        # Keep text white even when disabled (e.g. during "Connecting...")
        self.btn_con.configure(disabledforeground="white")
        self.btn_con.pack(side="left", padx=3)
        mk_btn(bf, "Edit", self._edit).pack(side="left", padx=3)
        mk_btn(bf, "✕", self._delete).pack(side="left", padx=3)
        self.btn_open = mk_btn(bf, "📂", self._open)
        self.btn_open.pack(side="left", padx=3)

    def _host_part(self):
        c = self.conn
        return f"{c['user']}@{c['host']}  ·  port {c.get('port', 22)}  →  "

    def _refresh(self):
        self._set("connected" if is_mounted(self.conn["drive_letter"]) else "disconnected")

    def _set(self, status):
        if status == self._status:
            return  # no-op when unchanged - avoids flicker from poll loop
        self._status = status
        dot_colors = {"connected": SUCCESS, "connecting": WARNING,
                      "disconnected": FG_MUTED, "error": DANGER}
        self.cv.itemconfig(self._dot, fill=dot_colors.get(status, FG_MUTED))
        if status == "connected":
            self.btn_con.configure(text="Disconnect", bg=DANGER, fg="white", state="normal")
            self.btn_open.configure(state="normal", fg=FG)
        elif status == "connecting":
            self.btn_con.configure(text="Connecting…", bg=WARNING, fg="white", state="disabled")
            self.btn_open.configure(state="disabled", fg=FG_MUTED)
        else:
            self.btn_con.configure(text="Connect", bg=ACCENT, fg="white", state="normal")
            self.btn_open.configure(state="disabled", fg=FG_MUTED)

    def _toggle(self):
        if self._status == "connected": self._disconnect()
        else: self._connect()

    def _connect(self, retries=0):
        missing = missing_requirements()
        if missing:
            self.mgr.check_requirements()  # refresh banner
            self.mgr.show_requirements_dialog(missing)
            return
        conn = self.conn  # Ask Each Time: the login popup asks for the password
        self._set("connecting")
        cid = self.conn["id"]
        self.mgr.log(f"[INFO] Connecting to {self.conn['name']}...\n")
        def w():
            ok = connect(conn, log=self.mgr.log)
            self.mgr.set_card_status(cid, "connected" if ok else "error")
            if not ok: self.mgr.set_card_status(cid, "disconnected", delay=2500)
            if not ok and retries > 0 and conn.get("auth_type") != "ask":
                self.mgr.log(f"[INFO] Retrying in 10s ({retries} attempt(s) left)...\n")
                try: self.mgr.after(10000, self.mgr.retry_connect, cid, retries - 1)
                except RuntimeError: pass
        threading.Thread(target=w, daemon=True).start()

    def _disconnect(self):
        self.mgr.log(f"[INFO] Disconnecting {self.conn['name']}...\n")
        def w():
            disconnect(self.conn, log=self.mgr.log)
            self.mgr.set_card_status(self.conn["id"], "disconnected")
        threading.Thread(target=w, daemon=True).start()

    def _ssh(self):
        conn = self.conn
        self.mgr.log(f"[INFO] Opening SSH terminal to {conn['user']}@{conn['host']}...\n")
        try:
            open_ssh_terminal(conn)
        except Exception as e:
            self.mgr.log(f"[ERROR] {e}\n")
            messagebox.showerror("SSH", str(e), parent=self.mgr)

    def _edit(self):   self.mgr.open_edit_dialog(self.conn)
    def _delete(self):
        if messagebox.askyesno("Delete", f"Delete '{self.conn['name']}'?"):
            self.mgr.delete_connection(self.conn["id"])
    def _open(self):
        drive = self.conn["drive_letter"]
        if not is_mounted(drive):
            self._refresh()  # sync UI with reality
            messagebox.showinfo("Not Mounted",
                f"Drive {drive} is not mounted.")
            return
        try:
            os.startfile(drive + "\\")
        except OSError as e:
            messagebox.showerror("Open Failed", str(e))

    def refresh(self, conn):
        self.conn = conn
        self.lbl_name.configure(text=conn["name"])
        self.lbl_host.configure(text=self._host_part())
        self.lbl_drive.configure(text=conn["drive_letter"])
        self._refresh()


class EditDialog(tk.Toplevel):
    def __init__(self, parent, conn, on_save):
        super().__init__(parent)
        self.conn, self.on_save = dict(conn), on_save
        self.withdraw()  # build hidden, then show centred over the parent
        self.title("Edit Connection")
        self.configure(bg=BG)
        # No fixed geometry: the dialog sizes to its content, which changes
        # with the auth method and the Test Connection result line.
        self.resizable(False, False)
        self.transient(parent)
        self._build()
        show_centered(self, parent)

    def _build(self):
        mk_label(self, "Connection Details", font=FONT_LARGE).pack(pady=(14,6))

        # Form (no scrollbar - dialog is sized to fit all fields)
        form = tk.Frame(self, bg=BG)
        form.pack(fill="both", expand=True)
        form.columnconfigure(1, weight=1)

        def lbl(text, r):
            mk_label(form, text, fg=FG_MUTED, font=FONT_SMALL).grid(
                row=r, column=0, sticky="e", padx=(14,8), pady=5)

        def entry(r, show=None, width=24):
            e = mk_entry(form, show=show, width=width)
            e.grid(row=r, column=1, sticky="ew", padx=(0,14), pady=5)
            return e

        lbl("Name", 0);         self.e_name   = entry(0)
        lbl("Host", 1);         self.e_host   = entry(1)
        lbl("Port", 2);         self.e_port   = entry(2, width=8)
        lbl("Username", 3);     self.e_user   = entry(3)
        lbl("Remote Path", 4)
        rf = tk.Frame(form, bg=BG)
        rf.grid(row=4, column=1, sticky="ew", padx=(0,14), pady=5)
        rf.columnconfigure(0, weight=1)
        self.e_remote = mk_entry(rf, width=20)
        self.e_remote.grid(row=0, column=0, sticky="ew")
        self.btn_browse_remote = mk_btn(rf, "Browse…", self._browse_remote, width=7)
        self.btn_browse_remote.grid(row=0, column=1, padx=(6,0))
        self.btn_browse_remote.configure(state="disabled", fg=FG_MUTED)
        self.lbl_remote_note = tk.Label(form,
                 text="Type a path manually (e.g. /home/user/folder), or click "
                      "Test Connection to browse folders on the server.",
                 bg=BG, fg=FG_MUTED, font=("Segoe UI", 8), anchor="w",
                 justify="left", wraplength=400)
        self.lbl_remote_note.grid(row=5, column=0, columnspan=2, sticky="w",
                                  padx=(14, 14), pady=(0, 4))

        self.e_name.insert(0,   self.conn.get("name",""))
        self.e_host.insert(0,   self.conn.get("host",""))
        self.e_port.insert(0,   str(self.conn.get("port",22)))
        self.e_user.insert(0,   self.conn.get("user",""))
        self.e_remote.insert(0, self.conn.get("remote_path","/"))

        # Drive
        lbl("Drive Letter", 6)

        # Collect drive letters claimed by *other* saved connections, and
        # detect whether this is an existing saved connection or a new one.
        claimed = set()
        is_existing = False
        try:
            for c in self.master._connections:
                if c.get("id") == self.conn.get("id"):
                    is_existing = True
                else:
                    dl = c.get("drive_letter")
                    if dl: claimed.add(dl)
        except Exception:
            pass

        # Show drive letters D: onward that the OS doesn't currently have.
        # Existing connections also see their own current letter (so they can
        # re-save without disconnecting first); new connections never include
        # in-use letters.
        import string
        all_letters = []
        for c in string.ascii_uppercase[3:]:
            letter = f"{c}:"
            if not os.path.exists(letter + "\\"):
                all_letters.append(letter)
            elif is_existing and letter == self.conn.get("drive_letter"):
                all_letters.append(letter)

        # Pick a sensible default: keep saved value if still in the list,
        # otherwise the first free letter.
        saved_drive = self.conn.get("drive_letter", "")
        current_drive = saved_drive if saved_drive in all_letters else (
            all_letters[0] if all_letters else "Z:")

        drive_row = tk.Frame(form, bg=BG)
        drive_row.grid(row=6, column=1, sticky="ew", padx=(0,14), pady=5)
        self.drive_var = tk.StringVar(value=current_drive)
        ttk.Combobox(drive_row, textvariable=self.drive_var, values=all_letters,
                     width=6, state="readonly").pack(side="left")
        self.lbl_drive_status = tk.Label(drive_row, text="", bg=BG,
                                         font=("Segoe UI", 8))
        self.lbl_drive_status.pack(side="left", padx=(8, 0))

        def update_drive_status(*_):
            d = self.drive_var.get()
            self_owns = (d == self.conn.get("drive_letter") and is_mounted(d))
            if self_owns:
                self.lbl_drive_status.configure(
                    text="✓ Currently mounted by this connection", fg=SUCCESS)
            elif d in claimed:
                self.lbl_drive_status.configure(
                    text="⚠ Used by another mount (currently offline)",
                    fg=WARNING)
            else:
                self.lbl_drive_status.configure(text="✓ Available", fg=SUCCESS)
        self.drive_var.trace_add("write", update_drive_status)
        update_drive_status()
        # Auth
        lbl("Auth Method", 7)
        self.auth_var = tk.StringVar(value=self.conn.get("auth_type","password"))
        af = tk.Frame(form, bg=BG)
        af.grid(row=7, column=1, sticky="w", pady=5)
        for v, t in [("password","Password"),("key","Private Key"),("ask","Ask Each Time")]:
            tk.Radiobutton(af, text=t, variable=self.auth_var, value=v,
                           bg=BG, fg=FG, selectcolor=BG_INPUT,
                           activebackground=BG, activeforeground=FG, font=FONT,
                           command=self._auth_change).pack(side="left", padx=5)

        # Password
        self.lbl_pw = mk_label(form, "Password", fg=FG_MUTED, font=FONT_SMALL)
        self.lbl_pw.grid(row=8, column=0, sticky="e", padx=(14,8), pady=5)
        self.e_pass = mk_entry(form, show="●")
        self.e_pass.grid(row=8, column=1, sticky="ew", padx=(0,14), pady=5)
        self.e_pass.insert(0, self.conn.get("password",""))

        # Key
        self.lbl_key = mk_label(form, "Key File", fg=FG_MUTED, font=FONT_SMALL)
        self.lbl_key.grid(row=9, column=0, sticky="e", padx=(14,8), pady=5)
        kf = tk.Frame(form, bg=BG)
        kf.grid(row=9, column=1, sticky="ew", padx=(0,14), pady=5)
        kf.columnconfigure(0, weight=1)
        self.e_key = mk_entry(kf, width=20)
        self.e_key.grid(row=0, column=0, sticky="ew")
        self.e_key.insert(0, self.conn.get("key_path",""))
        mk_btn(kf, "Browse", self._browse, width=7).grid(row=0, column=1, padx=(6,0))
        self._kf = kf

        # Extra args
        lbl("Extra Args", 10)
        self.e_extra = entry(10)
        self.e_extra.insert(0, self.conn.get("extra_args",""))

        # Auto connect
        self.auto_var = tk.BooleanVar(value=self.conn.get("auto_connect", False))
        tk.Checkbutton(form, text="Connect automatically when the app starts", variable=self.auto_var,
                       bg=BG, fg=FG, selectcolor=BG_INPUT,
                       activebackground=BG, activeforeground=FG,
                       font=FONT).grid(row=11, column=1, sticky="w", pady=(8, 0))

        # Verify host key
        self.verify_var = tk.BooleanVar(value=self.conn.get("verify_host_key", False))
        tk.Checkbutton(form, text="Verify SSH host key (recommended)",
                       variable=self.verify_var,
                       bg=BG, fg=FG, selectcolor=BG_INPUT,
                       activebackground=BG, activeforeground=FG,
                       font=FONT).grid(row=12, column=1, sticky="w", pady=(0, 8))

        self._auth_change()

        # Test result line
        self.lbl_test = tk.Label(self, text="", bg=BG, fg=FG_MUTED, font=FONT_SMALL,
                                 anchor="w", justify="left", wraplength=430)
        self.lbl_test.pack(fill="x", padx=14, pady=(2, 0))

        # Buttons
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=(4,14))
        self.btn_test = mk_btn(bar, "Test Connection", self._test_connection)
        self.btn_test.configure(disabledforeground=FG_MUTED)
        self.btn_test.pack(side="left")
        mk_btn(bar, "Save", self._save, bg=ACCENT, fg="white").pack(side="right", padx=(6,0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right")

        self._probe_password = None  # password typed for "Ask Each Time" tests
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _auth_change(self):
        auth = self.auth_var.get()
        if auth == "password":
            self.lbl_pw.grid(row=8, column=0, sticky="e", padx=(14,8), pady=5)
            self.e_pass.grid(row=8, column=1, sticky="ew", padx=(0,14), pady=5)
            self.lbl_key.grid_remove(); self._kf.grid_remove()
        elif auth == "key":
            self.lbl_key.grid(row=9, column=0, sticky="e", padx=(14,8), pady=5)
            self._kf.grid(row=9, column=1, sticky="ew", padx=(0,14), pady=5)
            self.lbl_pw.grid_remove(); self.e_pass.grid_remove()
        else:
            self.lbl_pw.grid_remove();  self.e_pass.grid_remove()
            self.lbl_key.grid_remove(); self._kf.grid_remove()

    def _browse(self):
        p = filedialog.askopenfilename(title="Select Private Key",
            filetypes=[("All files","*.*"),("PEM files","*.pem")])
        if p:
            self.e_key.delete(0,"end")
            self.e_key.insert(0,p)

    def _form_conn(self):
        """Current (unsaved) form values merged over the stored connection."""
        c = dict(self.conn)
        try: port = int(self.e_port.get().strip() or 22)
        except ValueError: port = 22
        c.update({
            "host": self.e_host.get().strip(), "port": port,
            "user": self.e_user.get().strip(),
            "remote_path": self.e_remote.get().strip() or "/",
            "auth_type": self.auth_var.get(), "password": self.e_pass.get(),
            "key_path": self.e_key.get().strip(),
            "verify_host_key": self.verify_var.get(),
        })
        return c

    def _probe_args(self):
        """(conn, password) for an SSHProbe, or None if the user cancelled."""
        c = self._form_conn()
        if not c["host"] or not c["user"]:
            messagebox.showerror("Test Connection", "Enter a host and username first.",
                                 parent=self)
            return None
        pw = None
        if c["auth_type"] == "ask":
            if self._probe_password is None:
                from tkinter import simpledialog
                pw = simpledialog.askstring("Password",
                        f"Password for {c['user']}@{c['host']}:", show="●", parent=self)
                if pw is None:
                    return None
                self._probe_password = pw
            pw = self._probe_password
        return c, pw

    def _test_connection(self):
        args = self._probe_args()
        if not args:
            return
        c, pw = args
        self.btn_test.configure(text="Testing…", state="disabled")
        self.lbl_test.configure(text=f"Connecting to {c['host']}:{c['port']}…", fg=FG_MUTED)

        def work():
            try:
                probe = SSHProbe(c, password=pw,
                                 prompt=prompt_from_thread(self, _conn_label(c)))
            except Exception as e:
                return self._after_safe(self._test_done, False, str(e), None)
            try:
                home = probe.home
                rp = c["remote_path"]
                try:
                    resolved = probe.resolve(rp)
                    probe.list_dirs(resolved)
                    path_msg = f"Remote path '{rp}' found ({resolved})."
                    path_ok = True
                except Exception:
                    path_msg = (f"Remote path '{rp}' does not exist on the server — "
                                f"click Browse… to pick one.")
                    path_ok = False
                msg = f"✓ Connected as {c['user']} (home: {home}). {path_msg}"
                self._after_safe(self._test_done, path_ok, msg, home)
            finally:
                probe.close()
        threading.Thread(target=work, daemon=True).start()

    def _after_safe(self, fn, *a):
        try: self.after(0, fn, *a)
        except Exception: pass  # dialog closed while the test was running

    def _test_done(self, ok, msg, home):
        if not self.winfo_exists():
            return
        self.btn_test.configure(text="Test Connection", state="normal")
        connected = home is not None
        if not connected:
            self._probe_password = None  # re-ask next time for "Ask Each Time"
            msg = "✗ " + msg
        self.lbl_test.configure(text=msg,
                                fg=SUCCESS if ok else (WARNING if connected else DANGER))
        if connected:
            self._probe_home = home
            self.btn_browse_remote.configure(state="normal", fg=FG)
            self.lbl_remote_note.configure(
                text="Connected — click Browse… to pick a folder, or type a path manually.")

    def _browse_remote(self):
        args = self._probe_args()
        if not args:
            return
        c, pw = args
        def on_pick(path):
            self.e_remote.delete(0, "end")
            self.e_remote.insert(0, path)
            self.lbl_test.configure(text=f"✓ Remote path set to {path} — click Save to keep it.",
                                    fg=SUCCESS)
        start = c["remote_path"] if c["remote_path"] not in ("", "/") else getattr(
            self, "_probe_home", ".")
        RemoteBrowser(self, c, pw, start, on_pick)

    def _save(self):
        self.conn.update({
            "name":            self.e_name.get().strip() or "Unnamed",
            "host":            self.e_host.get().strip(),
            "port":            int(self.e_port.get().strip() or 22),
            "user":            self.e_user.get().strip(),
            "remote_path":     self.e_remote.get().strip() or "/",
            "drive_letter":    self.drive_var.get(),
            "auth_type":       self.auth_var.get(),
            "password":        self.e_pass.get(),
            "key_path":        self.e_key.get().strip(),
            "extra_args":      self.e_extra.get().strip(),
            "auto_connect":    self.auto_var.get(),
            "verify_host_key": self.verify_var.get(),
        })
        if not self.conn["host"]:
            messagebox.showerror("Validation","Host is required.", parent=self); return
        if not self.conn["user"]:
            messagebox.showerror("Validation","Username is required.", parent=self); return
        self.on_save(self.conn)
        self.destroy()


class RemoteBrowser(tk.Toplevel):
    """Navigate the server's folders over SFTP and pick one for Remote Path.
    Double-click (or Enter) opens a folder; 'Use' picks the highlighted
    folder, or the current one if nothing is highlighted."""

    UP = "..   (parent folder)"

    def __init__(self, parent, conn, password, start, on_pick):
        super().__init__(parent)
        self.on_pick = on_pick
        self.withdraw()
        self.title("Select Remote Folder")
        self.configure(bg=BG)
        self.minsize(360, 320)
        self.transient(parent)
        self._probe = None
        self._busy = False
        self.cwd = None
        self._build()
        show_centered(self, parent, size=(440, 460))
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._status(f"Connecting to {conn['host']}…")

        def work():
            try:
                self._probe = SSHProbe(conn, password=password,
                                       prompt=prompt_from_thread(self, _conn_label(conn)))
            except Exception as e:
                return self._ui(self._status, f"✗ {e}", DANGER)
            self._ui(self._load, start, self._probe.home)
        self._busy = True
        threading.Thread(target=work, daemon=True).start()

    def _build(self):
        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=12, pady=(12, 6))
        top.columnconfigure(0, weight=1)
        self.e_path = mk_entry(top, width=30)
        self.e_path.grid(row=0, column=0, sticky="ew")
        self.e_path.bind("<Return>", lambda e: self._load(self.e_path.get()))
        mk_btn(top, "Go", lambda: self._load(self.e_path.get())).grid(
            row=0, column=1, padx=(6, 0))

        box = tk.Frame(self, bg=BG_INPUT)
        box.pack(fill="both", expand=True, padx=12)
        sb = DarkScrollbar(box, command=lambda *a: self.lb.yview(*a), autohide=False)
        sb.pack(side="right", fill="y")
        self.lb = tk.Listbox(box, bg=BG_INPUT, fg=FG, font=FONT, bd=0,
                             highlightthickness=0, activestyle="none",
                             selectbackground=ACCENT, selectforeground="white",
                             yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True, padx=(6, 0), pady=4)
        self.lb.bind("<Double-Button-1>", lambda e: self._open_selected())
        self.lb.bind("<Return>", lambda e: self._open_selected())
        self.lb.bind("<<ListboxSelect>>", lambda e: self._update_selection_status())

        self.lbl_status = tk.Label(self, text="", bg=BG, fg=FG_MUTED, font=FONT_SMALL,
                                   anchor="w", justify="left", wraplength=410)
        self.lbl_status.pack(fill="x", padx=12, pady=(6, 0))

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(6, 12))
        self.btn_use = mk_btn(bar, "Select This Folder", self._use, bg=ACCENT, fg="white")
        self.btn_use.pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", self._close).pack(side="right")

    def _ui(self, fn, *a):
        try: self.after(0, fn, *a)
        except Exception: pass  # window closed

    def _status(self, text, fg=None):
        if fg is None: fg = FG_MUTED
        if self.winfo_exists():
            self.lbl_status.configure(text=text, fg=fg)

    def _load(self, path, fallback=None, note=None):
        """List `path` in a worker thread; on failure try `fallback`."""
        if self._probe is None:
            return
        self._busy = True
        self._status(f"Loading {path}…")

        def work():
            try:
                resolved = self._probe.resolve(path)
                dirs = self._probe.list_dirs(resolved)
            except Exception as e:
                if fallback is not None:
                    return self._ui(self._load_failed_then, path, e, fallback)
                return self._ui(self._load_failed, path, e)
            self._ui(self._show, resolved, dirs, note)
        threading.Thread(target=work, daemon=True).start()

    def _load_failed_then(self, path, err, fallback):
        self._load(fallback, note=f"'{path}' not found on the server — "
                                  f"showing your home folder.")

    def _load_failed(self, path, err):
        self._busy = False
        msg = getattr(err, "strerror", None) or str(err) or type(err).__name__
        self._status(f"✗ Can't open '{path}': {msg}", DANGER)

    def _show(self, path, dirs, note=None):
        if not self.winfo_exists():
            return
        self._busy = False
        self.cwd = path
        self.e_path.delete(0, "end")
        self.e_path.insert(0, path)
        self.lb.delete(0, "end")
        if path != "/":
            self.lb.insert("end", self.UP)
        for d in dirs:
            self.lb.insert("end", "📁  " + d)
        self._list_msg = (note, WARNING) if note else (
            f"{len(dirs)} folder(s)" if dirs else "No sub-folders here.", FG_MUTED)
        self._status(*self._list_msg)

    def _child(self, index):
        item = self.lb.get(index)
        if item == self.UP:
            return self.cwd.rsplit("/", 1)[0] or "/"
        name = item.split("  ", 1)[1]
        return self.cwd.rstrip("/") + "/" + name

    def _open_selected(self):
        sel = self.lb.curselection()
        if sel and not self._busy:
            self._load(self._child(sel[0]))

    def _target(self):
        sel = self.lb.curselection()
        if sel and self.lb.get(sel[0]) != self.UP:
            return self._child(sel[0])
        return self.cwd

    def _update_selection_status(self):
        """A highlighted sub-folder is what 'Select This Folder' picks, so say so;
        otherwise show the listing message (folder count or warning)."""
        sel = self.lb.curselection()
        if sel and self.lb.get(sel[0]) != self.UP:
            self._status(f"Selected: {self._child(sel[0])}", ACCENT)
        elif getattr(self, "_list_msg", None):
            self._status(*self._list_msg)

    def _use(self):
        t = self._target()
        if t:
            self.on_pick(t)
            self._close()

    def _close(self):
        if self._probe:
            threading.Thread(target=self._probe.close, daemon=True).start()
        self.destroy()


class SettingsDialog(tk.Toplevel):
    def __init__(self, parent, settings, on_save):
        super().__init__(parent)
        self.settings, self.on_save = settings, on_save
        self.withdraw()
        self.title("Settings")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        mk_label(self, "Settings", font=FONT_LARGE).pack(pady=(14, 8))
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True, padx=20)

        def section(text):
            mk_label(body, text, font=FONT_BOLD).pack(anchor="w", pady=(8, 2))

        def note(text):
            tk.Label(body, text=text, bg=BG, fg=FG_MUTED, font=("Segoe UI", 8),
                     anchor="w", justify="left", wraplength=320).pack(
                anchor="w", padx=(24, 0), pady=(0, 4))

        radio = dict(bg=BG, fg=FG, selectcolor=BG_INPUT, activebackground=BG,
                     activeforeground=FG, font=FONT)

        section("Appearance")
        self.theme_var = tk.StringVar(value=settings["theme"])
        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", padx=(18, 0))
        for value, text in (("dark", "Dark"), ("light", "Light")):
            tk.Radiobutton(row, text=text, variable=self.theme_var, value=value,
                           **radio).pack(side="left", padx=(0, 14))

        section("Startup")
        self.sww_var = tk.BooleanVar(value=settings.get("start_with_windows", False))
        tk.Checkbutton(body, text="Start with Windows", variable=self.sww_var,
                       **radio).pack(anchor="w", padx=(18, 0))
        self.min_var = tk.BooleanVar(value=settings.get("start_minimized", True))
        tk.Checkbutton(body, text="Start minimized to tray", variable=self.min_var,
                       **radio).pack(anchor="w", padx=(18, 0))
        note("Applies when Windows starts the app. Connections set to connect "
             "automatically are retried for about a minute while the network "
             "comes up.")

        section("Logging")
        self.log_var = tk.BooleanVar(value=settings["log_enabled"])
        tk.Checkbutton(body, text="Enable debug log", variable=self.log_var,
                       **radio).pack(anchor="w", padx=(18, 0))
        note("When off, the Debug Log panel is hidden and nothing is recorded.")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=(14, 14))
        mk_btn(bar, "Save", self._save, bg=ACCENT).pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right")
        self.minsize(360, 0)
        show_centered(self, parent)

    def _save(self):
        self.settings.update(theme=self.theme_var.get(),
                             log_enabled=self.log_var.get(),
                             start_with_windows=self.sww_var.get(),
                             start_minimized=self.min_var.get())
        self.destroy()
        self.on_save(self.settings)


class ShutdownDialog(tk.Toplevel):
    """Shown on Exit while drives unmount in background threads. Calls
    on_done() when all are finished, or after `timeout_ms` regardless."""

    def __init__(self, parent, conns, on_done, timeout_ms=20000):
        super().__init__(parent)
        self.withdraw()
        self.on_done = on_done
        self._closed = False
        self.title("Disconnecting")
        self.configure(bg=BG)
        self.resizable(False, False)
        if parent.winfo_viewable():  # a hidden (tray) parent would hide us too
            self.transient(parent)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # wait for it to finish

        self.lbl_title = mk_label(self, "Disconnecting drives", font=FONT_LARGE)
        self.lbl_title.pack(padx=24, pady=(16, 10))
        rows = tk.Frame(self, bg=BG)
        rows.pack(fill="x", padx=24)
        self._status = []
        for i, conn in enumerate(conns):
            mk_label(rows, conn["drive_letter"], font=FONT_BOLD).grid(
                row=i, column=0, sticky="w", pady=2)
            mk_label(rows, conn.get("name", ""), fg=FG_MUTED).grid(
                row=i, column=1, sticky="w", padx=(10, 24), pady=2)
            st = mk_label(rows, "disconnecting…", fg=WARNING)
            st.grid(row=i, column=2, sticky="e", pady=2)
            self._status.append(st)
        rows.columnconfigure(1, weight=1)
        tk.Label(self, text="The app will close when this finishes.", bg=BG,
                 fg=FG_MUTED, font=FONT_SMALL).pack(padx=24, pady=(10, 16))
        self.minsize(320, 0)
        show_centered(self, parent)

        self._pending = len(conns)
        self._dots = 0
        self._animate()
        log = getattr(parent, "log", None)
        for i, conn in enumerate(conns):
            threading.Thread(target=self._work, args=(i, conn, log), daemon=True).start()
        self.after(timeout_ms, self._finish)

    def _work(self, i, conn, log):
        try:
            ok = disconnect(conn, log=log)
        except Exception:
            ok = False
        try: self.after(0, self._row_done, i, ok)
        except Exception: pass  # already closed (timeout)

    def _row_done(self, i, ok):
        if self._closed:
            return
        self._status[i].configure(text="✓ unmounted" if ok else "⚠ still mounted",
                                  fg=SUCCESS if ok else DANGER)
        self._pending -= 1
        if self._pending == 0:
            self.lbl_title.configure(text="Done — closing")
            self.after(400, self._finish)

    def _animate(self):
        if self._closed or self._pending == 0:
            return
        self._dots = (self._dots + 1) % 4
        self.lbl_title.configure(text="Disconnecting drives" + "." * self._dots)
        self.after(400, self._animate)

    def _finish(self):
        if self._closed:
            return
        self._closed = True
        self.on_done()


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        apply_theme(self._settings["theme"])
        self._at_login = STARTUP_ARG in sys.argv[1:]
        if self._at_login and self._settings.get("start_minimized", True):
            self.withdraw()  # avoid a window flash before going to the tray
        if getattr(sys, "frozen", False):
            # Start with Windows is on by default: enable it once on first run.
            # The flag means a later "off" in Settings/Task Manager is respected.
            if not self._settings.get("startup_default_applied"):
                try: set_start_with_windows(True)
                except OSError: pass
                self._settings["startup_default_applied"] = True
                try: save_settings(self._settings)
                except OSError: pass
            # If the exe was moved/rebuilt elsewhere, point the Run entry at it.
            reg = get_start_with_windows()
            if reg and reg != _startup_command():
                try: set_start_with_windows(True)
                except OSError: pass
        self.title(APP_NAME)
        self.geometry("700x540")
        self.minsize(600,400)
        self.configure(bg=BG)
        # Replace default Tk feather with the K monogram
        self._set_window_icon()
        self._connections = load_connections()
        self._cards = {}
        self._log_visible = False
        self._tray_icon = None
        self._stop = threading.Event()
        self._build()
        self._render()
        missing = self.check_requirements()
        if missing:
            # Let the main window draw first, then explain what's missing
            self.after(300, lambda: self.show_requirements_dialog(missing))
        else:
            self._auto_connect()
        self._poll()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._at_login and self._settings.get("start_minimized", True):
            self.after(100, self._minimize_to_tray)

    def _set_window_icon(self):
        """Use the K monogram for the title bar / taskbar / Alt-Tab icon."""
        try:
            from PIL import ImageTk
        except Exception:
            return
        # Multiple sizes - Tk picks the best for each context (title bar uses
        # ~16px, taskbar ~32px, Alt-Tab ~48px).
        imgs = []
        for sz in (16, 32, 48, 64):
            pil = _make_logo(sz)
            if pil is not None:
                imgs.append(ImageTk.PhotoImage(pil))
        if not imgs:
            return
        self._icon_imgs = imgs  # keep refs alive
        try:
            self.iconphoto(True, *imgs)  # default=True -> applies to Toplevels too
        except Exception:
            pass

    def _build(self):
        # Header
        hdr = tk.Frame(self, bg=BG_HEAD, height=52)
        hdr.pack(fill="x", side="top")
        hdr.pack_propagate(False)
        logo_img = _make_logo(28)
        if logo_img is not None:
            try:
                from PIL import ImageTk
                self._logo_tk = ImageTk.PhotoImage(logo_img)
                tk.Label(hdr, image=self._logo_tk, bg=BG_HEAD).pack(side="left", padx=(14, 8))
                mk_label(hdr, APP_NAME, font=FONT_LARGE).pack(side="left")
            except Exception:
                mk_label(hdr, APP_NAME, font=FONT_LARGE).pack(side="left", padx=18)
        else:
            mk_label(hdr, APP_NAME, font=FONT_LARGE).pack(side="left", padx=18)
        mk_btn(hdr,"⚙ Settings", self._open_settings, bg=ACCENT).pack(side="right",padx=(4,12),pady=10)
        mk_btn(hdr,"🔌 Disconnect All", self._disconnect_all, bg=DANGER).pack(side="right",padx=4,pady=10)
        mk_btn(hdr,"+ Add Connection",  self._add, bg=SUCCESS).pack(side="right",padx=4,pady=10)
        self._hdr = hdr

        # Missing-requirements banner (shown by check_requirements when needed)
        self._req_banner = tk.Frame(self, bg=BANNER_BG,
                                    highlightbackground=WARNING, highlightthickness=1)

        # Scrollable list
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        self.cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = DarkScrollbar(outer, command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.lf = tk.Frame(self.cv, bg=BG)
        self._lwin = self.cv.create_window((0,0), window=self.lf, anchor="nw")
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfig(self._lwin, width=e.width))
        self.lf.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        _bind_mousewheel(self.cv, lambda d: self.cv.yview_scroll(-1 * (d // 120), "units"),
                         container=outer)
        self.empty = mk_label(self.lf,
            "No connections yet.\nClick '+ Add Connection' to get started.",
            fg=FG_MUTED)

        # Single bottom bar: [Debug Log] [Clear Log] ........ [version] [Tray] [Exit]
        bar = tk.Frame(self, bg=BG_HEAD, height=42)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        # Right side: Exit, then Minimize to Tray, then version (packed right->left)
        mk_btn(bar, "Exit", self._on_close, bg=EXIT_BG).pack(side="right", padx=(4, 12), pady=6)
        mk_btn(bar, "Minimize to Tray", self._minimize_to_tray,
               bg=TRAY_BG).pack(side="right", padx=4, pady=6)
        ver = mk_label(bar, f"v{APP_VERSION}", fg=FG_MUTED, font=FONT_SMALL)
        ver.pack(side="right", padx=(12, 8))
        Tooltip(ver, build_info())

        # Left side: Debug Log, Clear Log
        self._log_btn = tk.Button(bar, text="▶  Debug Log", bg=BG_HEAD, fg=FG_MUTED,
                                   relief="flat", bd=0, font=FONT_SMALL, cursor="hand2",
                                   activebackground=BG_HEAD, activeforeground=FG,
                                   command=self._toggle_log)
        if self._settings["log_enabled"]:
            self._log_btn.pack(side="left", padx=10)
        self._log_btn.bind("<Enter>", lambda e: self._log_btn.configure(fg=FG))
        self._log_btn.bind("<Leave>", lambda e: self._log_btn.configure(fg=FG_MUTED))
        self._clear_btn = tk.Button(bar, text="Clear Log", bg=BG_HEAD, fg=FG_MUTED,
                                    relief="flat", bd=0, font=FONT_SMALL, cursor="hand2",
                                    activebackground=BG_HEAD, activeforeground=DANGER,
                                    command=self._clear_log)
        # Hidden until the log panel is expanded
        self._clear_btn.bind("<Enter>", lambda e: self._clear_btn.configure(fg=DANGER))
        self._clear_btn.bind("<Leave>", lambda e: self._clear_btn.configure(fg=FG_MUTED))
        self._copy_btn = tk.Button(bar, text="Copy Log", bg=BG_HEAD, fg=FG_MUTED,
                                   relief="flat", bd=0, font=FONT_SMALL, cursor="hand2",
                                   activebackground=BG_HEAD, activeforeground=ACCENT,
                                   command=self._copy_log)
        # Hidden until the log panel is expanded
        self._copy_btn.bind("<Enter>", lambda e: self._copy_btn.configure(fg=ACCENT))
        self._copy_btn.bind("<Leave>", lambda e: self._copy_btn.configure(fg=FG_MUTED))

        # Log panel
        self.log_frame = tk.Frame(self, bg=BG_CARD, height=150)
        log_sb = DarkScrollbar(self.log_frame, command=lambda *a: self.log_text.yview(*a),
                               autohide=False)
        log_sb.pack(side="right", fill="y", pady=6)
        self.log_text = tk.Text(self.log_frame, bg=LOG_BG, fg=LOG_FG,
                                 font=FONT_MONO, state="disabled", relief="flat", bd=0,
                                 yscrollcommand=log_sb.set)
        self.log_text.pack(fill="both", expand=True, padx=(8,0), pady=6)
        _bind_mousewheel(self.log_text, lambda d: self.log_text.yview_scroll(-1 * (d // 120), "units"))

    def _toggle_log(self):
        self._log_visible = not self._log_visible
        if self._log_visible:
            self.log_frame.pack(fill="x", side="bottom")
            self._log_btn.configure(text="▲  Debug Log")
            # Place Clear Log right after Debug Log
            self._clear_btn.pack(side="left", padx=10, after=self._log_btn)
            self._copy_btn.pack(side="left", padx=10, after=self._clear_btn)
        else:
            self.log_frame.pack_forget()
            self._log_btn.configure(text="▶  Debug Log")
            self._clear_btn.pack_forget()
            self._copy_btn.pack_forget()

    def check_requirements(self, log_missing=True):
        """Detect WinFsp / SSHFS-Win and show or hide the warning banner.
        Returns the list of missing components."""
        missing = missing_requirements()
        for w in self._req_banner.winfo_children():
            w.destroy()
        if not missing:
            self._req_banner.pack_forget()
            return missing

        names = " and ".join(n for n, _ in missing)
        msg = (f"⚠  {names} {'is' if len(missing) == 1 else 'are'} not installed. "
               f"This app needs {'it' if len(missing) == 1 else 'them'} to mount drives"
               f"{' — install in this order' if len(missing) > 1 else ''}, "
               f"then click Re-check.")
        tk.Label(self._req_banner, text=msg, bg=BANNER_BG, fg=BANNER_FG,
                 font=FONT_SMALL, justify="left", anchor="w",
                 wraplength=360).pack(side="left", fill="x", expand=True,
                                      padx=(12, 8), pady=8)
        mk_btn(self._req_banner, "Re-check", self._recheck_requirements).pack(
            side="right", padx=(4, 10), pady=8)
        for name, url in reversed(missing):  # packed right->left
            mk_btn(self._req_banner, f"Get {name}",
                   lambda u=url: self._open_url(u), bg=WARNING, fg=BG).pack(
                side="right", padx=4, pady=8)
        self._req_banner.pack(fill="x", side="top", after=self._hdr)

        for name, url in (missing if log_missing else []):
            self.log(f"[WARN] {name} not installed — download: {url}\n")
        return missing

    def _recheck_requirements(self):
        if not self.check_requirements():
            self.log("[OK] WinFsp and SSHFS-Win found — ready to connect.\n")
            messagebox.showinfo("Requirements", "WinFsp and SSHFS-Win are installed.\n"
                                "You're ready to connect.", parent=self)

    def show_requirements_dialog(self, missing):
        lines = "\n".join(f"  {i}. {name}  —  {url}"
                          for i, (name, url) in enumerate(missing, 1))
        if messagebox.askyesno(
                "Missing Requirements",
                "This app can't mount drives until these are installed"
                f"{' (in this order)' if len(missing) > 1 else ''}:\n\n{lines}\n\n"
                "Open the download page(s) now?", icon="warning", parent=self):
            for _, url in missing:
                self._open_url(url)

    def _open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def _render(self):
        for w in self.lf.winfo_children():
            if w is not self.empty:
                w.destroy()
        self._cards.clear()
        if not self._connections:
            self.empty.pack(pady=60); return
        self.empty.pack_forget()
        for conn in self._connections:
            c = Card(self.lf, conn, self)
            c.pack(fill="x", padx=14, pady=6)
            self._cards[conn["id"]] = c

    def _add(self):
        self.open_edit_dialog(new_connection(), is_new=True)

    def open_edit_dialog(self, conn, is_new=False):
        def on_save(updated):
            if is_new:
                self._connections.append(updated)
            else:
                for i, c in enumerate(self._connections):
                    if c["id"] == updated["id"]:
                        self._connections[i] = updated; break
            save_connections(self._connections)
            self._render()
        EditDialog(self, conn, on_save)

    def delete_connection(self, cid):
        self._connections = [c for c in self._connections if c["id"] != cid]
        save_connections(self._connections)
        self._render()

    def _disconnect_all(self):
        # Run mount checks + disconnects in a worker thread to keep the UI
        # responsive (is_mounted can block on busy SSHFS drives).
        def w():
            for conn in self._connections:
                try:
                    if is_mounted(conn["drive_letter"]):
                        threading.Thread(target=disconnect, args=(conn, self.log),
                                         daemon=True).start()
                except Exception:
                    pass
        threading.Thread(target=w, daemon=True).start()

    def _auto_connect(self):
        for conn in self._connections:
            if conn.get("auto_connect") and not is_mounted(conn["drive_letter"]):
                card = self._cards.get(conn["id"])
                # At Windows login the network may not be up yet: retry.
                if card: card._connect(retries=5 if self._at_login else 0)

    def retry_connect(self, cid, retries):
        card = self._cards.get(cid)
        if card and card.winfo_exists() and card._status != "connected" \
                and not is_mounted(card.conn["drive_letter"]):
            card._connect(retries=retries)

    def _poll(self):
        def loop():
            while not self._stop.wait(5):
                # Do the (potentially slow) is_mounted checks here, off the UI thread.
                # SSHFS drives under heavy I/O can block os.path.exists for seconds.
                snapshot = []
                for cid, card in list(self._cards.items()):
                    try:
                        snapshot.append((cid, is_mounted(card.conn["drive_letter"])))
                    except Exception:
                        pass
                try:
                    self.after(0, self._apply_poll_snapshot, snapshot)
                except RuntimeError:
                    return  # Tk root destroyed
        threading.Thread(target=loop, daemon=True).start()

    def _apply_poll_snapshot(self, snapshot):
        for cid, mounted in snapshot:
            card = self._cards.get(cid)
            if card:
                card._set("connected" if mounted else "disconnected")

    def _clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _copy_log(self):
        text = self.log_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()  # keep clipboard contents after focus changes
        self._copy_btn.configure(text="Copied ✓")
        self.after(1200, lambda: self._copy_btn.configure(text="Copy Log"))

    LOG_MAX_LINES = 100

    def log(self, msg):
        if not self._settings["log_enabled"]:
            return
        def w():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg)
            # Trim to the most recent LOG_MAX_LINES lines
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > self.LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{line_count - self.LOG_MAX_LINES + 1}.0")
            self.log_text.configure(state="disabled")
        self.after(0, w)

    def set_card_status(self, cid, status, delay=0):
        """Thread-safe card status update, looked up by connection id."""
        def w():
            card = self._cards.get(cid)
            if card and card.winfo_exists():
                card._set(status)
        try: self.after(delay, w)
        except RuntimeError: pass  # app closed

    def _open_settings(self):
        current = dict(self._settings, start_with_windows=bool(get_start_with_windows()))
        SettingsDialog(self, current, self._apply_settings)

    def _apply_settings(self, new):
        sww = new.pop("start_with_windows", None)
        if sww is not None and sww != bool(get_start_with_windows()):
            try:
                set_start_with_windows(sww)
                self.log(f"[INFO] Start with Windows {'enabled' if sww else 'disabled'}.\n")
            except OSError as e:
                messagebox.showerror("Settings", f"Couldn't change Windows startup:\n{e}",
                                     parent=self)
        old, self._settings = self._settings, new
        save_settings(new)
        if new["theme"] != old["theme"]:
            apply_theme(new["theme"])
            self._rebuild_ui()
        elif new["log_enabled"] != old["log_enabled"]:
            self._apply_log_setting()

    def _apply_log_setting(self):
        if self._settings["log_enabled"]:
            self._log_btn.pack(side="left", padx=10)
        else:
            if self._log_visible:
                self._toggle_log()
            self._log_btn.pack_forget()
            self._clear_log()

    def _rebuild_ui(self):
        """Recreate every widget so the new theme's colours take effect."""
        log_text = self.log_text.get("1.0", "end-1c")
        log_was_visible = self._log_visible
        statuses = {cid: c._status for cid, c in self._cards.items()}
        for w in self.winfo_children():
            w.destroy()
        self.configure(bg=BG)
        self._log_visible = False
        self._build()
        self._render()
        for cid, st in statuses.items():  # keep e.g. "Connecting..." across the rebuild
            if cid in self._cards:
                self._cards[cid]._set(st)
        self.check_requirements(log_missing=False)
        if log_text and self._settings["log_enabled"]:
            self.log_text.configure(state="normal")
            self.log_text.insert("end", log_text + "\n")
            self.log_text.configure(state="disabled")
        if log_was_visible and self._settings["log_enabled"]:
            self._toggle_log()

    def _minimize_to_tray(self):
        self.withdraw()
        if hasattr(self, "_tray_icon") and self._tray_icon:
            return  # already running
        self._start_tray_icon()

    def _start_tray_icon(self):
        try:
            import pystray
        except Exception as e:
            messagebox.showinfo("Tray Unavailable",
                f"pystray/Pillow not installed — cannot minimize to tray.\n({e})")
            self.deiconify(); return
        img = _make_logo(64)
        if img is None:
            messagebox.showinfo("Tray Unavailable",
                "Pillow (PIL) is required for the tray icon.")
            self.deiconify(); return

        menu = pystray.Menu(
            pystray.MenuItem("Show", lambda: self.after(0, self._restore), default=True),
            pystray.MenuItem("Exit", lambda: self.after(0, self._quit)),
        )
        self._tray_icon = pystray.Icon(APP_NAME, img, APP_NAME, menu)
        self._tray_icon.run_detached = False
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _restore(self):
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.deiconify()
        self.lift()
        self.focus_force()

    def _quit(self):
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self._on_close()

    def _on_close(self):
        mounted = [c for c in self._connections if is_mounted(c["drive_letter"])]
        if mounted:
            ans = messagebox.askyesnocancel(
                "Active Connections",
                f"{len(mounted)} connection(s) still mounted.\n\nYes = Disconnect & Exit\nNo = Minimize to tray\nCancel = Go back")
            if ans is None:
                return
            if ans:
                # Disconnect in the background so the UI stays responsive;
                # the dialog calls _finish_close once every drive is done.
                self._stop.set()
                ShutdownDialog(self, mounted, on_done=self._finish_close)
                return
            else:
                self._minimize_to_tray()
                return
        self._finish_close()

    def _finish_close(self):
        self._stop.set()
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.destroy()

if __name__ == "__main__":
    # Launched by ssh as SSH_ASKPASS (see open_ssh_terminal): answer and exit
    # before any UI is created.
    if os.environ.get(ASKPASS_ENV) and len(sys.argv) == 2:
        sys.exit(_askpass_main(sys.argv[1]))
    App().mainloop()
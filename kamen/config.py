"""Version, paths, settings, saved connections, password encryption,
log file and Windows startup."""

import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time
from pathlib import Path

APP_NAME    = "Kamen SSHFS Manager"
ROOT  = Path(__file__).resolve().parent.parent   # folder with version.json
ENTRY = ROOT / "kamen_sshfs_manager.py"          # the script that starts the app

def _load_version():
    """Read version from version.json next to this script (or in CWD as fallback).
    Falls back to a baked-in default if the file is missing or unreadable."""
    here = Path(getattr(sys, "_MEIPASS", ROOT))
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
    path = sys.executable if frozen else str(ENTRY)
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

# Colour palettes. ui.apply_theme() copies one into the ui module's globals, so
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

DEFAULT_SETTINGS = {"theme": "dark", "log_enabled": True, "start_minimized": True,
                    "auto_restart_hung": True, "check_updates": True}

RELEASES_URL = "https://github.com/DinamoXP/Kamen-SSHFS-Manager/releases/latest"

RELEASES_API = "https://api.github.com/repos/DinamoXP/Kamen-SSHFS-Manager/releases/latest"

def _version_tuple(v):
    import re
    return tuple(int(x) for x in re.findall(r"\d+", v)[:3])

def latest_release():
    """(version, url) of the newest GitHub release, or None if it can't be read."""
    import urllib.request
    req = urllib.request.Request(RELEASES_API, headers={
        "Accept": "application/vnd.github+json", "User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read())
        return data["tag_name"].lstrip("v."), data.get("html_url") or RELEASES_URL
    except Exception:
        return None

HUNG_RESTART_SECS = 60

LOG_PATH = CONFIG_PATH.parent / "kamen.log"

_file_logger = None

def setup_file_log(enabled):
    """Rotating log file next to the config (1 MB x 3). Follows the
    "Enable debug log" setting."""
    global _file_logger
    import logging
    import logging.handlers
    lg = logging.getLogger("kamen")
    if enabled and not lg.handlers:
        h = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000,
                                                 backupCount=3, encoding="utf-8")
        h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%d %H:%M:%S"))
        lg.addHandler(h)
        lg.setLevel(logging.INFO)
        lg.propagate = False
    _file_logger = lg if enabled else None

def file_log(msg):
    if _file_logger:
        try:
            _file_logger.info(msg.rstrip("\n"))
        except Exception:
            pass

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
    return f'"{pyw if pyw.exists() else py}" "{ENTRY}" {STARTUP_ARG}'

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

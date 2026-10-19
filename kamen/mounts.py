"""Mounting and unmounting drives with SSHFS-Win."""

import ctypes
import os
import subprocess
import sys
import threading
import time

from .config import (
    file_log,
)
from .ssh import (
    ASKPASS_ENV,
    _askpass_program,
    bare_host,
)
from .winutil import (
    _process_cmdline,
    _process_parents,
    _wait_letter_gone,
    letter_present,
    probe_drive,
)

_procs = {}

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

def sshfs_host(host):
    """Host for sshfs's user@host:path syntax: IPv6 literals (which contain
    colons, e.g. fe80::1%12) must be bracketed or sshfs splits them wrongly."""
    h = bare_host(host)
    return f"[{h}]" if ":" in h else h

def connect(conn, log=None):
    cid, drive = conn["id"], conn["drive_letter"]
    state = probe_drive(drive)
    if state == "mounted":
        if log: log(f"[INFO] {drive} already mounted.\n")
        return True
    if state == "hung":
        if log: log(f"[HUNG] {drive} is not responding. Use Restart or Kill on its card.\n")
        return False

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
        "-oallow_other",
        "-ofollow_symlinks",
        # Notice a dead link within ~45 s and reconnect instead of hanging
        "-oServerAliveInterval=15",
        "-oServerAliveCountMax=3",
        "-oreconnect",
    ]
    # Debug logging is opt-in per connection (extra_args: "-odebug -ologlevel=debug1")
    # Default-on debug was making file ops synchronous -> 38MB took forever and
    # the log flood froze the UI.

    if conn.get("read_only"):
        # SSHFS-Win ignores "-o ro", so: read-only permissions on the Windows
        # side, and the server's sftp-server started with -R so it refuses
        # every write (it lives in different places on different systems).
        args = [{"-oumask=000": "-oumask=222", "-ocreate_umask=000": "-ocreate_umask=222"}.get(a, a)
                for a in args]
        args.append("-osftp_server=for p in /usr/lib/openssh/sftp-server "
                    "/usr/libexec/openssh/sftp-server /usr/lib/ssh/sftp-server "
                    "/usr/libexec/sftp-server /usr/lib/sftp-server; "
                    "do [ -x $p ] && exec $p -R; done; exit 1")

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
                    txt = line.decode(errors="replace").rstrip()
                    if mounted[0]:
                        file_log(f"{prefix} {drive} {txt}")  # file only, no UI flood
                        continue
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
            if result[0] or letter_present(drive):
                if log: log(f"[OK] {drive} mounted as '{name}'.\n")
                mounted[0] = True  # stop log streaming; threads keep draining
                _notify_explorer_drive_change(drive, added=True)
                return True
            if proc.poll() is not None:
                if log: log(f"[ERROR] sshfs exited (code {proc.returncode}).\n")
                if log and conn.get("read_only"):
                    log("[HINT] Read-only mounts need OpenSSH's sftp-server on the server. "
                        "If it's missing, untick Read-only in Edit.\n")
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
    Reads command lines directly instead of asking PowerShell/WMI, which both
    hang while any mounted drive is frozen."""
    drive_letter = drive.rstrip(":\\").upper()
    pids = []
    for pid, (_parent, name) in _process_parents().items():
        if name != "sshfs.exe":
            continue
        # Match the drive letter as a standalone token in the command line
        # e.g. "... user@host:/path Z: -p22 ..."  ->  match " Z: " or trailing " Z:"
        cl = _process_cmdline(pid).upper()
        if f" {drive_letter}: " in cl + " ":
            pids.append(str(pid))
    return pids

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
    if not _wait_letter_gone(drive, 1.0 if terminated else 0):
        pids = _find_sshfs_pids_for_drive(drive)
        for pid in pids:
            if log: log(f"[INFO] Killing orphaned sshfs.exe PID {pid} for {drive}...\n")
            subprocess.run(["taskkill", "/PID", pid, "/T", "/F"],
                           capture_output=True,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if pids:
            _wait_letter_gone(drive, 5.0)

    mounted = letter_present(drive)
    if log: log(f"[INFO] {drive} {'still mounted' if mounted else 'unmounted successfully'}.\n")
    if not mounted:
        _notify_explorer_drive_change(drive, added=False)
    return not mounted

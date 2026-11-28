"""Mounting and unmounting drives with SSHFS-Win."""

import ctypes
import json
import os
import subprocess
import sys
import threading
import time

from .config import (
    CONFIG_PATH,
    file_log,
)
from .ssh import (
    ASKPASS_ENV,
    JUMP_ENV,
    SUDO_ENV,
    SUDO_SSH_ENV,
    SUDO_PROMPT,
    SUDO_READY,
    sudo_error_path,
    _askpass_program,
    jump_hop_args,
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

WINGET_IDS = {"WinFsp": "WinFsp.WinFsp", "SSHFS-Win": "SSHFS-Win.SSHFS-Win"}

def find_winget():
    """Windows' package manager (part of Windows 10/11 via App Installer)."""
    import shutil
    return shutil.which("winget")

def winget_install(name):
    """Install WinFsp or SSHFS-Win with winget. Windows asks for admin rights
    (UAC) for the driver installer. Returns (ok, output)."""
    winget = find_winget()
    if not winget:
        return False, "winget not found"
    p = subprocess.run([winget, "install", "--id", WINGET_IDS[name], "--exact", "--silent",
                        "--accept-package-agreements", "--accept-source-agreements",
                        "--disable-interactivity"],
                       capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    out = (p.stdout + p.stderr).decode("utf-8", "replace")
    # Keep the readable lines: winget also prints progress bars and spinners
    lines = [l.strip() for l in out.replace("\r", "\n").splitlines()
             if l.strip() and not set(l.strip()) <= set("-\\|/ █▒")]
    return p.returncode == 0, "\n".join(lines)

REQUIREMENT_REPOS = {"WinFsp": "winfsp/winfsp", "SSHFS-Win": "winfsp/sshfs-win"}

def direct_install(name):
    """Download the official installer (.msi) from the project's latest GitHub
    release and run it. Windows asks for permission. Returns (ok, output)."""
    import json, tempfile, urllib.request
    req = urllib.request.Request(
        f"https://api.github.com/repos/{REQUIREMENT_REPOS[name]}/releases/latest",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "Kamen-SSHFS-Manager"})
    with urllib.request.urlopen(req, timeout=30) as r:
        assets = json.load(r).get("assets", [])
    msis = [a for a in assets if a["name"].lower().endswith(".msi")]
    arch = "x64" if os.environ.get("PROCESSOR_ARCHITECTURE", "").endswith("64") else "x86"
    msi = next((a for a in msis if arch in a["name"].lower()), msis[0] if msis else None)
    if not msi:
        return False, f"No installer found in the latest {name} release."
    path = os.path.join(tempfile.gettempdir(), msi["name"])
    urllib.request.urlretrieve(msi["browser_download_url"], path)
    # /passive: progress bar only, no questions. 3010 = installed, restart suggested.
    p = subprocess.run(["msiexec", "/i", path, "/passive", "/norestart"],
                       creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        os.remove(path)
    except OSError:
        pass
    return p.returncode in (0, 3010), f"{msi['name']}: msiexec exit code {p.returncode}"

def install_requirement(name):
    """Install WinFsp or SSHFS-Win: with winget when it's there, otherwise (or
    if that didn't work) by downloading the official installer. Returns output."""
    out = []
    if find_winget():
        try:
            out.append(winget_install(name)[1])
        except Exception as e:
            out.append(f"winget: {e}")
        if name not in [n for n, _ in missing_requirements()]:
            return "\n".join(out)
        out.append("winget didn't install it, downloading the installer instead...")
    try:
        out.append(direct_install(name)[1])
    except Exception as e:
        out.append(f"Download failed: {e}")
    return "\n".join(o for o in out if o)

def sshfs_host(host):
    """Host for sshfs's user@host:path syntax: IPv6 literals (which contain
    colons, e.g. fe80::1%12) must be bracketed or sshfs splits them wrongly."""
    h = bare_host(host)
    return f"[{h}]" if ":" in h else h

# Edit window checkboxes: (key, label, default, sshfs arguments, tooltip). The defaults
# match how drives were mounted before these options existed.
MOUNT_OPTIONS = [
    ("follow_symlinks", "Follow symlinks", True, ["-ofollow_symlinks"],
     "Show what symlinks on the server point to, instead of the links themselves"),
    ("compression", "Compress data", False, ["-C"],
     "Faster on slow connections, a little slower on fast ones"),
    ("hide_dot_files", "Hide dot files", False, ["-odothidden"],
     "Files and folders starting with a dot (.bashrc) get Windows' hidden attribute"),
    ("no_dir_cache", "No folder cache", False, ["-odir_cache=no"],
     "Always show the newest files, e.g. when others change them on the server. "
     "Folders open a little slower"),
    ("rename_fix", "Rename fix", False, ["-oworkaround=rename"],
     "For older servers that refuse saving over an existing file by renaming "
     "(some programs save that way)"),
    ("use_sudo", "Use sudo", False, [],
     "Warning: gives the drive root access to the server, including system files. "
     "Deleting or changing the wrong file can damage the server. "
     "Leave Sudo Password blank if sudo -n works."),
]

# Bump this whenever a change to the sshfs arguments only takes effect after a
# drive is mounted again: on start the app then remounts drives mounted the
# older way by itself (see App._upgrade_mounts). 1 = before 1.9.1,
# 2 = VolumePrefix (network share registration).
MOUNT_FORMAT = 2
_FORMATS_PATH = CONFIG_PATH.parent / "mounts.json"

def _load_formats():
    try:
        with open(_FORMATS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def _save_format(drive, fmt):
    formats = _load_formats()
    if fmt is None:
        formats.pop(drive, None)
    else:
        formats[drive] = fmt
    try:
        with open(_FORMATS_PATH, "w", encoding="utf-8") as f:
            json.dump(formats, f)
    except OSError:
        pass

def mount_format(drive):
    """MOUNT_FORMAT the drive's current mount was made with, or None if this
    app's sshfs isn't serving it. Mounts from versions that didn't record it
    are recognised by their command line."""
    pids = _find_sshfs_pids_for_drive(drive)
    if not pids:
        return None
    fmt = _load_formats().get(drive)
    if fmt is not None:
        return fmt
    if any("-ovolumeprefix=/kamen/" in _process_cmdline(int(p)).lower() for p in pids):
        return 2
    return 1

def _share_name(name, drive):
    """Connection name as a share name: without characters a UNC path can't
    hold; the drive letter if nothing is left."""
    share = "".join(ch for ch in name if ch not in '\\/:*?"<>|%' and ord(ch) >= 32).strip(" .")
    return share or drive[0].upper()

def mount_option(conn, key):
    default = next(o[2] for o in MOUNT_OPTIONS if o[0] == key)
    return bool(conn.get("mount_options", {}).get(key, default))

def _sudo_wrapper_alias():
    """Give SSHFS-Win a no-spaces executable name for its ssh_command option.

    The SSHFS version bundled with SSHFS-Win splits ssh_command on spaces,
    even inside quotes. Link/copy our already-built executable to an alias in
    the user's config directory; the alias is found through PATH. No SSHFS-Win
    files are changed.
    """
    if not getattr(sys, "frozen", False):
        raise RuntimeError("Password-protected sudo mounts require the packaged Kamen EXE.")
    import hashlib
    import shutil
    source = os.path.abspath(sys.executable)
    st = os.stat(source)
    stamp = f"{source}|{st.st_size}|{st.st_mtime_ns}".encode("utf-8")
    alias = "kamen-sudo-" + hashlib.sha256(stamp).hexdigest()[:12] + ".exe"
    dest = CONFIG_PATH.parent / alias
    if not dest.exists():
        try:
            os.link(source, dest)
        except OSError:
            shutil.copy2(source, dest)
    # Keep helpers still referenced by sshfs: a mounted drive can reconnect
    # using its original helper even after the app has been updated.
    procs = _process_parents()
    if procs:
        commands = [_process_cmdline(pid).lower() for pid, (_, name) in procs.items()
                    if name == "sshfs.exe"]
        if all(commands):
            running = {name for _, name in procs.values()}
            for old in CONFIG_PATH.parent.glob("kamen-sudo-*.exe"):
                name = old.name.lower()
                if name == alias.lower() or name in running or any(name in cmd for cmd in commands):
                    continue
                try:
                    old.unlink()
                except OSError:
                    pass  # still in use; try again next time
    return alias

def _sudo_sftp_command(read_only, with_password):
    """Find a usable sftp-server and run it via sudo, keeping SFTP stdio clean."""
    paths = ("/usr/lib/openssh/sftp-server "
             "/usr/libexec/openssh/sftp-server "
             "/usr/lib/ssh/sftp-server "
             "/usr/libexec/sftp-server "
             "/usr/lib/sftp-server")
    flags = " -R" if read_only else ""
    if with_password:
        # The local ssh wrapper injects the password before forwarding any SFTP
        # bytes. The root shell emits READY on stderr only after sudo auth.
        launch = (
            f"exec sudo -S -p {SUDO_PROMPT} /bin/sh -c "
            f"'echo {SUDO_READY} >&2; exec \"$@\"' kamen-sftp \"$p\"{flags}"
        )
    else:
        launch = f'exec sudo -n "$p"{flags}'
    return f'for p in {paths}; do [ -x "$p" ] || continue; {launch}; done; exit 1'

def connect(conn, log=None):
    cid, drive = conn["id"], conn["drive_letter"]
    state = probe_drive(drive)
    if state == "mounted":
        other = foreign_drive(drive)
        if other:
            if log: log(f"[ERROR] {drive} is already used by {other}, so {conn['name']} "
                        f"can't use it. Pick another drive letter in Edit.\n")
            return False
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
        # Register the drive like a network share (\\kamen\<name>). Without it
        # Windows can't give programs the drive's "final path"
        # (GetFinalPathNameByHandle), and tools that check their folder that way
        # (Node's realpath.native, Claude Code) treat the whole drive as
        # unreachable. Explorer shows the share name, so use the connection's.
        f"-oVolumePrefix=/kamen/{_share_name(name, drive)}",
        # Notice a dead link within ~45 s and reconnect instead of hanging
        "-oServerAliveInterval=15",
        "-oServerAliveCountMax=3",
        "-oreconnect",
    ]
    # Debug logging is opt-in per connection (extra_args: "-odebug -ologlevel=debug1")
    # Default-on debug was making file ops synchronous -> 38MB took forever and
    # the log flood froze the UI.

    for key, _, _, opt_args, _ in MOUNT_OPTIONS:
        if mount_option(conn, key):
            args += opt_args

    use_sudo = mount_option(conn, "use_sudo")
    sudo_password = conn.get("sudo_password", "") if use_sudo else ""
    if use_sudo and any(a.startswith("-osftp_server=")
                        for a in conn.get("extra_args", "").split()):
        if log:
            log("[ERROR] Remove the manual -osftp_server=... from Extra Args "
                "before enabling Use sudo.\n")
        return False

    if conn.get("read_only"):
        # SSHFS-Win ignores "-o ro", so: read-only permissions on the Windows
        # side, and the server's sftp-server started with -R so it refuses
        # every write (it lives in different places on different systems).
        args = [{"-oumask=000": "-oumask=222", "-ocreate_umask=000": "-ocreate_umask=222"}.get(a, a)
                for a in args]
        if not use_sudo:
            args.append("-osftp_server=for p in /usr/lib/openssh/sftp-server "
                        "/usr/libexec/openssh/sftp-server /usr/lib/ssh/sftp-server "
                        "/usr/libexec/sftp-server /usr/lib/sftp-server; "
                        "do [ -x $p ] && exec $p -R; done; exit 1")

    if use_sudo:
        args.append("-osftp_server=" + _sudo_sftp_command(
            bool(conn.get("read_only")), bool(sudo_password)))
        if sudo_password:
            try:
                alias = _sudo_wrapper_alias()
            except (OSError, RuntimeError) as e:
                if log: log(f"[ERROR] Cannot prepare the local sudo helper: {e}\n")
                return False
            args.append("-ossh_command=" + alias + " --sudo-ssh-wrapper")

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

    if conn.get("jump_host"):
        # Through a jump host: ssh runs this app as its "shell" for the hop
        import shlex
        args.append("-oProxyCommand=" + shlex.join(jump_hop_args(conn, "ssh")))

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
    env["PATH"] = (sshfs_dir + os.pathsep + str(CONFIG_PATH.parent)
                   + os.pathsep + env.get("PATH", ""))
    env.update({"SSH_ASKPASS": _askpass_program(), "SSH_ASKPASS_REQUIRE": "force",
                ASKPASS_ENV: conn["id"]})
    if sudo_password:
        env.update({SUDO_ENV: conn["id"],
                    SUDO_SSH_ENV: os.path.join(sshfs_dir, "ssh.exe")})
        try:
            sudo_error_path(cid).unlink()
        except OSError:
            pass
    if conn.get("jump_host"):
        env.update({"SHELL": _askpass_program(),
                    JUMP_ENV: os.path.join(sshfs_dir, "ssh.exe")})

    # sshfs writes to a file, not a pipe to this app: a mount then keeps
    # working while the app is closed or restarting (e.g. Update Now), instead
    # of dying on its next message to a pipe nobody reads any more.
    out_path = CONFIG_PATH.parent / f"sshfs-{drive[0].upper()}.log"
    try:
        with open(out_path, "wb") as out:
            proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=subprocess.STDOUT,
                env=env,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )

        _procs[cid] = proc

        result = [None]    # success flag set when mount is detected
        mounted = [False]  # flips True after mount - we stop logging then

        def stream():
            """Show sshfs's messages in the log while the mount is being set up."""
            try:
                with open(out_path, "rb") as f:
                    while not mounted[0]:
                        line = f.readline()
                        if not line:
                            if proc.poll() is not None:
                                return
                            time.sleep(0.1)
                            continue
                        txt = line.decode(errors="replace").rstrip()
                        if "service sshfs has been started" in txt:
                            result[0] = True
                        if log: log(f"[SSHFS] {txt}\n")
                        if log and txt.endswith(f":{remote}: No such file or directory"):
                            log(f"[HINT] Remote path '{remote}' doesn't exist on the server. "
                                f"Use Edit → Test Connection → Browse… to pick one.\n")
            except Exception: pass

        threading.Thread(target=stream, daemon=True).start()

        if log: log(f"[INFO] Waiting for {drive} to mount "
                    f"(answer any login prompt that pops up)...\n")
        # Up to 2 minutes, so there's time to type a 2FA code; an unreachable
        # server makes sshfs exit sooner, which ends the wait early.
        for i in range(240):
            time.sleep(0.5)
            if result[0] or letter_present(drive):
                if log: log(f"[OK] {drive} mounted as '{name}'.\n")
                _save_format(drive, MOUNT_FORMAT)
                mounted[0] = True  # stop log streaming; threads keep draining
                _notify_explorer_drive_change(drive, added=True)
                return True
            if proc.poll() is not None:
                time.sleep(0.3)  # let stream() show sshfs's last words
                if log: log(f"[ERROR] sshfs exited (code {proc.returncode}).\n")
                if log and use_sudo:
                    said = ""
                    if sudo_password:  # only the sudo helper leaves this file
                        try:
                            said = sudo_error_path(cid).read_text(encoding="utf-8").strip()
                        except OSError:
                            pass
                    if "incorrect sudo password" in said.lower():
                        log("[ERROR] Wrong sudo password. Fix it in Edit (Sudo Password).\n")
                    elif said:
                        log(f"[ERROR] sudo on the server said: {said}\n")
                    elif not sudo_password:
                        log("[HINT] Use sudo is on without a sudo password, so the server must "
                            "allow sudo without one. If it doesn't, enter the Sudo Password "
                            "in Edit.\n")
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

_DRIVE_KINDS = {2: "removable drive", 3: "local disk", 4: "network drive",
                5: "CD drive", 6: "RAM disk"}

def _drive_unc(drive):
    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    try:
        if ctypes.windll.mpr.WNetGetConnectionW(drive[0] + ":", buf, ctypes.byref(size)) == 0:
            return buf.value
    except OSError:
        pass
    return ""

def foreign_drive(drive):
    """When the letter is taken by something that isn't one of our mounts (a USB
    stick, a CD drive, another network share), return what it is, e.g.
    "magicJack (CD drive)". None when the letter is free or is ours."""
    if not letter_present(drive):
        return None
    root = drive[0].upper() + ":\\"
    kind = ctypes.windll.kernel32.GetDriveTypeW(root)
    unc = _drive_unc(drive) if kind == 4 else ""
    if unc.lower().startswith("\\\\kamen\\") or _find_sshfs_pids_for_drive(drive):
        return None
    label = ""
    if kind != 4:
        buf = ctypes.create_unicode_buffer(261)
        if ctypes.windll.kernel32.GetVolumeInformationW(root, buf, len(buf), None, None,
                                                        None, None, 0):
            label = buf.value
    name = unc or label
    what = _DRIVE_KINDS.get(kind, "another drive")
    return f"{name} ({what})" if name else what

def disconnect(conn, log=None):
    cid, drive = conn["id"], conn["drive_letter"]
    other = foreign_drive(drive)
    if other:
        # Never unmount someone else's drive.
        if log: log(f"[INFO] {drive} is {other}, not {conn['name']}. Leaving it alone.\n")
        return True
    _save_format(drive, None)
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

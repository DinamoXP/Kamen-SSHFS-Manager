"""Self-update for the exe: download the newest release from GitHub, check it
against the release's SHA256SUMS.txt and swap it in."""

import hashlib
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from .config import APP_NAME, APP_VERSION, RECONNECT_ARG, UPDATED_ARG

EXE_ASSET = "Kamen.SSHFS.Manager.exe"


def can_self_update():
    """Only the exe can replace itself; running from source can't."""
    return getattr(sys, "frozen", False)


def _get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


def download(info, progress=None):
    """Download the new exe next to the running one as <name>.exe.new and check
    its SHA256. Returns the file's path; raises on any problem."""
    with _get(info["sums"]) as r:
        sums = r.read().decode("utf-8", "replace")
    want = None
    for line in sums.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == EXE_ASSET:
            want = parts[0].lower()
    if not want:
        raise RuntimeError("SHA256SUMS.txt has no line for the exe.")
    exe = Path(sys.executable)
    new = exe.with_name(exe.name + ".new")
    h = hashlib.sha256()
    with _get(info["exe"], timeout=60) as r, open(new, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while True:
            chunk = r.read(256 * 1024)
            if not chunk:
                break
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress and total:
                progress(done * 100 // total)
    if h.hexdigest() != want:
        new.unlink(missing_ok=True)
        raise RuntimeError("The download doesn't match SHA256SUMS.txt, so it wasn't used.")
    return new


def swap_in(new):
    """Put the downloaded exe in place of the running one. Windows lets a
    running exe be renamed (not deleted), so the old one becomes <name>.exe.old
    and is deleted by the next start."""
    exe = Path(sys.executable)
    old = exe.with_name(exe.name + ".old")
    try:
        old.unlink(missing_ok=True)
    except OSError:
        pass
    os.replace(exe, old)
    try:
        os.replace(new, exe)
    except OSError:
        os.replace(old, exe)
        raise


def start_new(reconnect_ids):
    """Start the updated exe. It waits for this copy to exit (see
    claim_single_instance) and connects the drives that were connected."""
    args = [sys.executable, UPDATED_ARG]
    if reconnect_ids:
        args.append(RECONNECT_ARG + ",".join(reconnect_ids))
    # Without this the new exe would reuse this copy's unpacked files
    env = dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT="1")
    subprocess.Popen(args, env=env, close_fds=True,
                     creationflags=subprocess.DETACHED_PROCESS)


def cleanup_old():
    """Delete <name>.exe.old left by an update. The old copy may still be
    closing, so keep trying for a while."""
    if not can_self_update():
        return
    old = Path(sys.executable + ".old")
    if not old.exists():
        return
    def w():
        for _ in range(30):
            try:
                old.unlink(missing_ok=True)
                return
            except OSError:
                time.sleep(2)
    threading.Thread(target=w, daemon=True).start()

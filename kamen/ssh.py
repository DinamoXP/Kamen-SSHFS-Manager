"""SSH: Test Connection / folder browser logins, key login setup, the
password helper (SSH_ASKPASS), the SSH terminal and tunnels."""

import ctypes
import ctypes.wintypes as wt
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from .config import (
    ENTRY,
    CONFIG_PATH,
    file_log,
)
from .winutil import (
    _calling_ssh_pid,
    _create_kill_job,
)

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
            raise RuntimeError(f"Can't load the 'paramiko' SSH library ({e}) - "
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
            raise RuntimeError("Authentication failed - check username and password/key.")
        except paramiko.SSHException as e:
            client.close()
            msg = str(e)
            if "not found in known_hosts" in msg:
                msg = ("Server's host key is not in your known_hosts file. Untick "
                       "'Verify SSH host key', or connect once with ssh to trust it.")
            raise RuntimeError(msg)
        except OSError as e:
            client.close()
            raise RuntimeError(f"Can't reach {conn['host']}:{kw['port']} - "
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

KEYS_DIR = CONFIG_PATH.parent / "keys"

def setup_key_login(conn, password, prompt=None):
    """Create an ed25519 key for this connection (once), add its public half to
    ~/.ssh/authorized_keys on the server, and check a key-only login works.
    Returns the private key path. Raises RuntimeError with a readable message."""
    import shlex
    KEYS_DIR.mkdir(parents=True, exist_ok=True)
    key = KEYS_DIR / f"{conn['id']}_ed25519"
    pub = key.with_suffix(".pub")
    if not (key.exists() and pub.exists()):
        keygen = None
        ssh = _find_ssh_client()
        if ssh:
            cand = Path(ssh).with_name("ssh-keygen.exe")
            keygen = str(cand) if cand.exists() else None
        if not keygen:
            raise RuntimeError("ssh-keygen wasn't found. Install Windows' OpenSSH Client "
                               "(Settings -> Apps -> Optional features).")
        import socket
        r = subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key),
                            "-C", f"kamen-sshfs-manager@{socket.gethostname()}"],
                           capture_output=True, text=True, timeout=60,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode != 0 or not pub.exists():
            raise RuntimeError(f"Couldn't create the key: {(r.stderr or r.stdout).strip()}")
    pubkey = pub.read_text(encoding="utf-8").strip()

    probe = SSHProbe(conn, password=password, prompt=prompt)
    try:
        cmd = ("umask 077; mkdir -p ~/.ssh && touch ~/.ssh/authorized_keys && "
               f"(grep -qxF {shlex.quote(pubkey)} ~/.ssh/authorized_keys || "
               f"printf '%s\\n' {shlex.quote(pubkey)} >> ~/.ssh/authorized_keys)")
        _, out, err = probe.client.exec_command(cmd, timeout=30)
        code = out.channel.recv_exit_status()
        if code != 0:
            raise RuntimeError("The server refused to save the key: "
                               + (err.read().decode(errors="replace").strip() or f"exit {code}"))
    finally:
        probe.close()

    test = dict(conn, auth_type="key", key_path=str(key), password="")
    try:
        SSHProbe(test, prompt=prompt).close()
    except RuntimeError as e:
        raise RuntimeError(f"The key was added, but logging in with it failed: {e}")
    return str(key)

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
    script = ENTRY
    wrapper.write_text(f'@"{py}" "{script}" %*\r\n', encoding="utf-8")
    return str(wrapper)

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

def _conn_label(conn):
    if not conn:
        return ""
    return f"{conn.get('name', '')} - {conn.get('user', '')}@{bare_host(conn.get('host', ''))}"

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

TUNNEL_PRESETS = [
    # name, type, local port, remote port
    ("Web panel", "local", 8080, 80),
    ("Portainer", "local", 9000, 9000),
    ("MySQL / MariaDB", "local", 3306, 3306),
    ("PostgreSQL", "local", 5432, 5432),
    ("SOCKS proxy", "socks", 1080, None),
]

DB_PORTS = {3306, 5432, 1433, 1521, 6379, 27017}

def describe_tunnel(t):
    if t["type"] == "socks":
        return f"SOCKS proxy on 127.0.0.1:{t['local_port']}"
    return (f"127.0.0.1:{t['local_port']} -> {t.get('remote_host') or '127.0.0.1'}"
            f":{t['remote_port']} on the server")

def port_free(port):
    import socket
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        sock.close()

def _tunnel_ssh_args(conn, t):
    ssh = _find_ssh_client()
    if not ssh:
        raise RuntimeError("No SSH client found. Install Windows' OpenSSH Client "
                           "(Settings -> Apps -> Optional features).")
    args = [ssh, "-N", "-p", str(conn.get("port", 22)),
            "-o", "ExitOnForwardFailure=yes",
            "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
            "-o", "LogLevel=VERBOSE"]  # login and forwarding details go to the log file
    if not conn.get("verify_host_key", False):
        args += ["-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    if conn.get("auth_type") == "key" and conn.get("key_path"):
        args += ["-i", conn["key_path"], "-o", "IdentitiesOnly=yes"]
    if t["type"] == "socks":
        args += ["-D", f"127.0.0.1:{t['local_port']}"]
    else:
        rh = t.get("remote_host") or "127.0.0.1"
        if ":" in rh:
            rh = f"[{rh}]"
        args += ["-L", f"127.0.0.1:{t['local_port']}:{rh}:{t['remote_port']}"]
    args.append(f"{conn['user']}@{bare_host(conn['host'])}")
    return args

class TunnelManager:
    """Runs one background ssh.exe per tunnel and reports state changes
    through on_change(cid, tid), called from worker threads."""

    ACTIVE = ("starting", "running", "retrying")
    FATAL = ("Permission denied", "Host key verification failed", "already in use",
             "Could not request local forwarding", "Too many authentication failures",
             "No SSH client")
    NOISE = ("Authenticated to", "Local connections to", "Connection to port",
             "channel ", "Warning: Permanently added", "Transferred:",
             "Bytes per second", "debug")

    def __init__(self, on_change):
        self.on_change = on_change
        self._recs = {}
        self._lock = threading.Lock()
        self._job = _create_kill_job()

    def state(self, tid):
        rec = self._recs.get(tid)
        return (rec["state"], rec["msg"]) if rec else ("stopped", "")

    def is_active(self, tid):
        return self.state(tid)[0] in self.ACTIVE

    def running_count(self, cid):
        return sum(1 for r in self._recs.values()
                   if r["cid"] == cid and r["state"] == "running")

    def active_count(self):
        return sum(1 for r in self._recs.values() if r["state"] in self.ACTIVE)

    def start(self, conn, t, log):
        with self._lock:
            if self.is_active(t["id"]):
                return
            rec = {"cid": conn["id"], "tid": t["id"], "state": "starting", "msg": "",
                   "proc": None, "stop": threading.Event()}
            self._recs[t["id"]] = rec
        threading.Thread(target=self._run, args=(rec, dict(conn), dict(t), log),
                         daemon=True).start()

    def stop(self, tid):
        rec = self._recs.get(tid)
        if not rec:
            return
        rec["stop"].set()
        proc = rec["proc"]
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except OSError:
                pass

    def stop_conn(self, cid):
        for tid, rec in list(self._recs.items()):
            if rec["cid"] == cid:
                self.stop(tid)

    def stop_all(self):
        for tid in list(self._recs):
            self.stop(tid)

    def forget(self, tid):
        self.stop(tid)
        self._recs.pop(tid, None)

    def _set(self, rec, state, msg=""):
        rec["state"], rec["msg"] = state, msg
        try:
            self.on_change(rec["cid"], rec["tid"])
        except Exception:
            pass

    def _run(self, rec, conn, t, log):
        label = f"{t['name']} ({describe_tunnel(t)})"
        delay = 10
        while not rec["stop"].is_set():
            self._set(rec, "starting")
            if not port_free(t["local_port"]):
                msg = f"Port {t['local_port']} is already in use on this PC."
                log(f"[TUNNEL] {label}: {msg}\n")
                self._set(rec, "error", msg)
                return
            try:
                args = _tunnel_ssh_args(conn, t)
            except RuntimeError as e:
                self._set(rec, "error", str(e))
                return
            env = os.environ.copy()
            env.update({"SSH_ASKPASS": _askpass_program(), "SSH_ASKPASS_REQUIRE": "force",
                        ASKPASS_ENV: conn["id"]})
            try:
                proc = subprocess.Popen(args, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                        env=env, creationflags=subprocess.CREATE_NO_WINDOW)
            except OSError as e:
                self._set(rec, "error", str(e))
                return
            rec["proc"] = proc
            if self._job:
                ctypes.windll.kernel32.AssignProcessToJobObject(
                    wt.HANDLE(self._job), wt.HANDLE(int(proc._handle)))
            log(f"[TUNNEL] Starting {label}...\n")

            def watch_port(proc=proc):
                # Running = ssh is listening on the local port. Windows' ssh
                # doesn't reliably print a "forwarding ready" line.
                while proc.poll() is None and not rec["stop"].is_set():
                    if not port_free(t["local_port"]):
                        log(f"[TUNNEL] {label} is running.\n")
                        self._set(rec, "running")
                        return
                    time.sleep(0.25)
            threading.Thread(target=watch_port, daemon=True).start()

            last = ""
            for raw in proc.stderr:
                line = raw.decode(errors="replace").strip()
                if not line:
                    continue
                file_log(f"[TUNNEL] {t['name']}: {line}")
                if not line.startswith(self.NOISE):
                    last = line
            if rec["state"] == "running":
                delay = 10  # it worked, so the next drop starts the backoff again
            proc.wait()
            rec["proc"] = None
            if rec["stop"].is_set():
                break
            reason = last or f"ssh exited (code {proc.returncode})"
            log(f"[TUNNEL] {label} stopped: {reason}\n")
            fatal = any(f in reason for f in self.FATAL)
            if fatal or not t.get("auto_restart", True) or conn.get("auth_type") == "ask":
                self._set(rec, "error", reason)
                return
            self._set(rec, "retrying", f"{reason} Retrying in {delay} s.")
            if rec["stop"].wait(delay):
                break
            delay = min(delay * 2, 60)
        self._set(rec, "stopped")

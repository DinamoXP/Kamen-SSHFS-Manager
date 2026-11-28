"""Tasks: to-dos, server commands, programs on this PC and file copies, run by
hand, on a schedule, when a drive connects or when the app starts."""

import datetime
import json
import os
import subprocess
import time

from .config import CONFIG_PATH

TASKS_PATH = CONFIG_PATH.parent / "tasks.json"

TYPES = [("todo", "To-do"), ("command", "Server command"),
         ("program", "Program on this PC"), ("copy", "Copy files")]
TRIGGERS = [("manual", "Only when I click Run"), ("schedule", "On a schedule"),
            ("connect", "When a drive connects"), ("start", "When the app starts")]
COPY_MODES = [("copy", "Copy everything"), ("update", "Only new and changed files"),
              ("mirror", "Mirror (also deletes files in To that aren't in From)")]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
OUTPUT_MAX = 20000  # characters of output kept per task


def new_task():
    return {"id": str(int(time.time() * 1000)), "name": "New Task", "type": "todo",
            "notes": "", "done": False, "conn_id": "", "command": "", "workdir": "",
            "wait": True, "src": "", "dst": "", "mode": "update",
            "trigger": "manual", "every": "daily", "minutes": 60, "time": "03:00",
            "day": 0, "notify": False,
            "last_run": 0, "last_ok": None, "last_output": ""}


def load_tasks():
    try:
        with open(TASKS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return [dict(new_task(), **t) for t in data if isinstance(t, dict)]
    except (OSError, ValueError):
        return []


def save_tasks(tasks):
    TASKS_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = TASKS_PATH.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(tasks, f, indent=2)
    os.replace(tmp, TASKS_PATH)


def _hhmm(text):
    try:
        h, m = (int(x) for x in str(text).strip().split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return None


def valid_time(text):
    return _hhmm(text) is not None


def next_run(task, after):
    """Next scheduled run (epoch seconds) strictly after `after`, or None."""
    every = task.get("every", "daily")
    if every == "minutes":
        mins = max(1, int(task.get("minutes") or 60))
        return after + mins * 60
    hm = _hhmm(task.get("time"))
    if not hm:
        return None
    now = datetime.datetime.fromtimestamp(after)
    t = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0)
    if every == "weekly":
        t += datetime.timedelta(days=(int(task.get("day", 0)) - t.weekday()) % 7)
        if t <= now:
            t += datetime.timedelta(days=7)
    elif t <= now:
        t += datetime.timedelta(days=1)
    return t.timestamp()


def describe_when(task, conn_name=None):
    trig = task.get("trigger", "manual")
    if task.get("type") == "todo":
        return ""
    if trig == "schedule":
        every = task.get("every", "daily")
        if every == "minutes":
            n = int(task.get("minutes") or 60)
            return f"every {n} minute{'s' if n != 1 else ''}"
        if every == "weekly":
            return f"every {DAYS[int(task.get('day', 0))]} at {task.get('time')}"
        return f"daily at {task.get('time')}"
    if trig == "connect":
        return f"when {conn_name or 'its drive'} connects"
    if trig == "start":
        return "when the app starts"
    return "when you click Run"


def describe_task(task, conn_name=None):
    """One line about what the task does."""
    t = task.get("type")
    if t == "todo":
        return (task.get("notes") or "").strip().splitlines()[0][:90] if task.get("notes") else ""
    if t == "command":
        return f"On {conn_name or '?'}: {(task.get('command') or '').strip().splitlines()[0][:70]}" \
            if task.get("command") else f"On {conn_name or '?'}"
    if t == "program":
        return (task.get("command") or "").strip()[:90]
    if t == "copy":
        mode = {"copy": "copy", "update": "copy new/changed", "mirror": "mirror"}.get(
            task.get("mode"), "copy")
        return f"{task.get('src')} -> {task.get('dst')} ({mode})"
    return ""


def robocopy_args(src, dst, mode):
    """Windows' robocopy: copies folders, retries briefly, no progress noise."""
    args = ["robocopy", src, dst, "/E", "/R:2", "/W:5", "/NP", "/NDL"]
    if mode == "update":
        args.append("/XO")
    elif mode == "mirror":
        args[3] = "/MIR"
    return args


def _clip(text):
    text = text or ""
    return text if len(text) <= OUTPUT_MAX else "...\n" + text[-OUTPUT_MAX:]


def run_command(conn, command, prompt=None, timeout=3600):
    """Run a command on the server over SSH. Returns (ok, output)."""
    from .ssh import SSHProbe
    probe = SSHProbe(conn, prompt=prompt)
    try:
        _, out, err = probe.client.exec_command(command, timeout=timeout)
        text = out.read().decode("utf-8", "replace")
        errs = err.read().decode("utf-8", "replace")
        code = out.channel.recv_exit_status()
        if errs:
            text += ("\n" if text and not text.endswith("\n") else "") + errs
        text += f"\n[exit code {code}]"
        return code == 0, text
    finally:
        probe.close()


def run_program(command, workdir="", wait=True):
    cwd = workdir or None
    if cwd and not os.path.isdir(cwd):
        return False, f"Folder not found: {cwd}"
    flags = subprocess.CREATE_NO_WINDOW if wait else 0
    if not wait:
        subprocess.Popen(command, shell=True, cwd=cwd, creationflags=flags)
        return True, "Started."
    p = subprocess.run(command, shell=True, cwd=cwd, capture_output=True, creationflags=flags)
    text = (p.stdout + p.stderr).decode("utf-8", "replace") if p.stdout or p.stderr else ""
    return p.returncode == 0, text + f"\n[exit code {p.returncode}]"


def run_copy(src, dst, mode):
    if not os.path.isdir(src):
        return False, f"Folder not found: {src}"
    p = subprocess.run(robocopy_args(src, dst, mode), capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    text = p.stdout.decode("utf-8", "replace") + p.stderr.decode("utf-8", "replace")
    # robocopy: 0-7 mean success (with or without files copied), 8 and up are errors
    return p.returncode < 8, text.strip() + f"\n[robocopy exit code {p.returncode}]"


def drive_of(path):
    path = (path or "").strip()
    return path[:2].upper() if len(path) >= 2 and path[1] == ":" else ""

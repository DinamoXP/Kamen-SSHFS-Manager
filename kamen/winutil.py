"""Windows helpers: drive checks that can't hang, process lookup,
job objects and the single-instance lock."""

import ctypes
import ctypes.wintypes as wt
import os
import sys
import threading
import time

from .config import (
    STARTUP_ARG,
)

def letter_present(drive):
    """Is the drive letter there at all? GetLogicalDrives doesn't touch the
    file system, so it can't hang on a dead mount and is safe on the UI thread."""
    bit = ord(drive[0].upper()) - ord("A")
    return bool(ctypes.windll.kernel32.GetLogicalDrives() >> bit & 1)

_probes = {}

_probe_lock = threading.Lock()

drive_space = {}  # drive -> (free bytes, total bytes), filled by probe_drive(want_space=True)

def human_size(n):
    for unit in ("bytes", "KB", "MB", "GB", "TB", "PB"):
        if n < 1024 or unit == "PB":
            return f"{n:.0f} {unit}" if n >= 100 or unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024

def probe_drive(drive, timeout=3.0, want_space=False):
    """Return "unmounted", "mounted" or "hung". Can block up to `timeout`, so call it
    from worker threads only. At most one probe thread per drive: while an
    earlier probe is stuck, callers share it instead of starting new ones."""
    if not letter_present(drive):
        return "unmounted"
    with _probe_lock:
        p = _probes.get(drive)
        if not (p and p["thread"].is_alive()):
            p = {"started": time.time(), "result": None}
            def run(p=p):
                try:
                    p["result"] = os.path.exists(drive + "\\")
                    if p["result"] and want_space:
                        import shutil
                        u = shutil.disk_usage(drive + "\\")
                        drive_space[drive] = (u.free, u.total)
                except OSError:
                    p["result"] = False
            p["thread"] = threading.Thread(target=run, daemon=True)
            _probes[drive] = p
            p["thread"].start()
    p["thread"].join(max(0.0, timeout - (time.time() - p["started"])))
    if p["thread"].is_alive():
        return "hung"
    return "mounted" if p["result"] else "unmounted"

def _wait_letter_gone(drive, timeout):
    end = time.time() + timeout
    while letter_present(drive):
        if time.time() >= end:
            return False
        time.sleep(0.1)
    return True

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

def _create_kill_job():
    """A job object that ends every process in it when this app exits or
    crashes, so tunnels can't outlive the app unseen."""
    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class BASIC_LIMIT(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wt.DWORD),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", wt.DWORD),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", wt.DWORD),
                    ("SchedulingClass", wt.DWORD)]
    class EXTENDED_LIMIT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", BASIC_LIMIT),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t)]
    k32 = ctypes.windll.kernel32
    k32.CreateJobObjectW.restype = wt.HANDLE
    job = k32.CreateJobObjectW(None, None)
    if not job:
        return None
    info = EXTENDED_LIMIT()
    info.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not k32.SetInformationJobObject(wt.HANDLE(job), 9, ctypes.byref(info),
                                       ctypes.sizeof(info)):
        return None
    return job

def _process_cmdline(pid):
    """Command line of another process, read through the kernel
    (NtQueryInformationProcess, ProcessCommandLineInformation). Works on
    suspended or hung processes and never touches a drive."""
    k32, ntdll = ctypes.windll.kernel32, ctypes.windll.ntdll
    k32.OpenProcess.restype = wt.HANDLE
    h = k32.OpenProcess(0x1000, False, int(pid))  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        size = wt.ULONG(0)
        ntdll.NtQueryInformationProcess(wt.HANDLE(h), 60, None, 0, ctypes.byref(size))
        if not size.value:
            return ""
        buf = ctypes.create_string_buffer(size.value)
        if ntdll.NtQueryInformationProcess(wt.HANDLE(h), 60, buf, size, ctypes.byref(size)) != 0:
            return ""
        class UNICODE_STRING(ctypes.Structure):
            _fields_ = [("Length", wt.USHORT), ("MaximumLength", wt.USHORT),
                        ("Buffer", ctypes.c_void_p)]
        us = UNICODE_STRING.from_buffer(buf)
        return ctypes.wstring_at(us.Buffer, us.Length // 2) if us.Buffer else ""
    finally:
        k32.CloseHandle(wt.HANDLE(h))

_k32 = ctypes.WinDLL("kernel32", use_last_error=True)

_k32.CreateMutexW.restype = wt.HANDLE

_k32.CreateMutexW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.LPCWSTR]

_k32.CreateEventW.restype = wt.HANDLE

_k32.CreateEventW.argtypes = [ctypes.c_void_p, wt.BOOL, wt.BOOL, wt.LPCWSTR]

_k32.OpenEventW.restype = wt.HANDLE

_k32.OpenEventW.argtypes = [wt.DWORD, wt.BOOL, wt.LPCWSTR]

_k32.SetEvent.argtypes = [wt.HANDLE]

_k32.CloseHandle.argtypes = [wt.HANDLE]

_k32.WaitForSingleObject.restype = wt.DWORD

_k32.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]

_instance_mutex = None

_show_event = None

def claim_single_instance():
    """True if no other copy is running. Otherwise asks the running copy to
    show its window (unless this launch came from Windows startup)."""
    global _instance_mutex, _show_event
    _instance_mutex = _k32.CreateMutexW(None, False, "Local\\KamenSSHFSManager")
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        if STARTUP_ARG not in sys.argv[1:]:
            ev = _k32.OpenEventW(0x0002, False, "Local\\KamenSSHFSManagerShow")
            if ev:
                _k32.SetEvent(ev)
                _k32.CloseHandle(ev)
        return False
    _show_event = _k32.CreateEventW(None, False, False, "Local\\KamenSSHFSManagerShow")
    return True

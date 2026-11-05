#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
==============================================================================
 WINDOWS FIXER PRO  -  All-in-One Windows Repair & Manager  (single file)
==============================================================================
 * Pure Python 3.8+ standard library only (tkinter, ctypes, subprocess ...)
 * Nothing to install.  Save as  WindowsFixerPro.py  (or .pyw for no console)
   and double-click it.  It asks for Administrator rights automatically.

 SAFETY MODEL ("No Harm" design)
 -------------------------------
 1. It NEVER deletes personal files, formats drives, or edits the registry.
    (The registry is only READ, to show proxy info.)
 2. File deletion is limited to a HARD-CODED WHITELIST of cache/temp folders
    (see SafeCleaner).  Any path not on that list is refused by the code.
    Junctions / symlinks are never followed.  Files from the last hour in
    Temp folders are skipped so running installers are not broken.
 3. Every action shows a plain-English explanation and asks permission first.
 4. Medium-risk actions offer to create a System Restore point first.
 5. Everything else is done with Microsoft's own tools (SFC, DISM, CHKDSK
    /scan, netsh, winget ...).
 6. Every system call is wrapped in try/except; the app cannot crash from a
    failing command.  Long jobs can be cancelled with the CANCEL button.
==============================================================================
"""

import os
import sys
import re
import json
import time
import stat
import queue
import base64
import socket
import ctypes
import shutil
import fnmatch
import platform
import threading
import subprocess
import datetime
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional, Tuple

try:
    import tkinter as tk
    from tkinter import ttk, messagebox, filedialog
except Exception:  # pragma: no cover
    print("tkinter is required (it ships with the standard Python installer).")
    sys.exit(1)

APP_NAME = "Windows Fixer Pro"
APP_VERSION = "3.0"
CREATE_NO_WINDOW = 0x08000000
FILE_ATTRIBUTE_REPARSE_POINT = 0x400

SYSROOT = os.environ.get("SystemRoot", r"C:\Windows")
_sysnative = os.path.join(SYSROOT, "Sysnative")
# 32-bit Python on 64-bit Windows would be redirected; Sysnative avoids that.
SYS32 = _sysnative if os.path.isdir(_sysnative) else os.path.join(SYSROOT, "System32")


# =============================================================================
#  COLOR PALETTE  (black / red / green / blue / yellow / aqua)
# =============================================================================
class C:
    BG = "#0a0a0a"
    PANEL = "#111214"
    CARD = "#16181b"
    CARD_HOVER = "#1d2025"
    BORDER = "#2a2f36"
    TEXT = "#eaeaea"
    DIM = "#98a2ad"
    RED = "#ff4d4d"
    GREEN = "#2ee66b"
    BLUE = "#3d8bff"
    BLUE_LIGHT = "#6db0ff"
    YELLOW = "#ffd23f"
    AQUA = "#19e3e3"


FONT = "Segoe UI"
MONO = "Consolas"


def _rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _lerp(a, b, t):
    """Blend two RGB tuples -> '#rrggbb'."""
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(x + (y - x) * t))) for x, y in zip(a, b))


def _grad(stops, t):
    """Sample a multi-stop gradient (list of RGB tuples) at 0..1 -> '#rrggbb'."""
    t = max(0.0, min(1.0, t))
    n = len(stops) - 1
    i = min(n - 1, int(t * n))
    return _lerp(stops[i], stops[i + 1], t * n - i)


# =============================================================================
#  GLOBAL CANCEL SUPPORT + UNIVERSAL SUBPROCESS WRAPPER
# =============================================================================
CANCEL = threading.Event()
_current = {"proc": None}


def kill_current():
    """Kill the running child process tree (used by Cancel / window close)."""
    proc = _current.get("proc")
    if proc is not None and proc.poll() is None:
        try:
            subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                           capture_output=True, timeout=15,
                           creationflags=CREATE_NO_WINDOW)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


def run_cmd(cmd, timeout=120, on_line=None, shell=False):
    """
    Universal safe subprocess wrapper.
    Returns {"stdout": str, "exit_code": int, "success": bool}
    exit_code: -1 = could not start, -2 = timed out, -3 = cancelled.
    * stderr is merged into stdout (no noisy separate error stream)
    * output decoded with errors='ignore'; NUL bytes removed so SFC's
      UTF-16 output becomes readable text
    * never raises
    """
    result = {"stdout": "", "exit_code": -1, "success": False}
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, shell=shell,
                                creationflags=CREATE_NO_WINDOW)
    except FileNotFoundError:
        result["stdout"] = "Command not found: %s" % (cmd[0] if isinstance(cmd, (list, tuple)) else cmd)
        return result
    except Exception as exc:
        result["stdout"] = "Could not start command: %s" % exc
        return result

    _current["proc"] = proc
    chunks = []

    def emit(raw):
        try:
            if on_line:
                on_line(raw.decode("utf-8", errors="ignore"))
        except Exception:
            pass

    def reader():
        buf = b""
        try:
            while True:
                data = proc.stdout.read1(4096)
                if not data:
                    break
                data = data.replace(b"\x00", b"")
                chunks.append(data)
                if on_line:
                    buf += data
                    parts = re.split(rb"[\r\n]+", buf)
                    buf = parts.pop()
                    for part in parts:
                        emit(part)
            if buf:
                emit(buf)
        except Exception:
            pass

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    start = time.time()
    code = None
    try:
        while True:
            code = proc.poll()
            if code is not None:
                break
            if CANCEL.is_set():
                kill_current()
                code = -3
                break
            if time.time() - start > timeout:
                kill_current()
                code = -2
                break
            time.sleep(0.1)
    except Exception:
        code = -1
    thread.join(timeout=5)
    _current["proc"] = None
    text = b"".join(chunks).decode("utf-8", errors="ignore")
    result["stdout"] = text.strip()
    result["exit_code"] = code if isinstance(code, int) else -1
    result["success"] = (code == 0)
    return result


def ps_cmd(script, timeout=120, on_line=None):
    """Run PowerShell hidden. Script is passed Base64-encoded (no quoting bugs)."""
    prefix = ("$ProgressPreference='SilentlyContinue';"
              "[Console]::OutputEncoding=[Text.Encoding]::UTF8;")
    enc = base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")
    return run_cmd(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                    "-OutputFormat", "Text", "-EncodedCommand", enc], timeout, on_line)


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return ("%d %s" % (n, unit)) if unit == "B" else ("%.1f %s" % (n, unit))
        n /= 1024.0
    return "%.1f TB" % n


def fmt_uptime(sec):
    d, rem = divmod(int(sec), 86400)
    h, rem = divmod(rem, 3600)
    m, _ = divmod(rem, 60)
    return "%dd %02dh %02dm" % (d, h, m) if d else "%02dh %02dm" % (h, m)


def fmt_dur(sec):
    sec = int(max(0, sec))
    if sec >= 3600:
        return "%dh %02dm" % (sec // 3600, (sec % 3600) // 60)
    if sec >= 60:
        return "%dm %02ds" % (sec // 60, sec % 60)
    return "%ds" % sec


def parse_selection(text, n):
    """Parse '1, 3, 5', '1-4', 'all' -> (sorted list of 1-based ints, list of bad tokens)."""
    text = (text or "").strip().lower()
    if text in ("all", "a", "*"):
        return list(range(1, n + 1)), []
    text = re.sub(r"\s*-\s*", "-", text)
    chosen, bad = set(), []
    for tok in re.split(r"[,\s;]+", text):
        if not tok:
            continue
        m = re.fullmatch(r"(\d+)-(\d+)", tok)
        if m:
            lo, hi = sorted((int(m.group(1)), int(m.group(2))))
            if lo < 1 or hi > n:
                bad.append(tok)
            else:
                chosen.update(range(lo, hi + 1))
        elif tok.isdigit() and 1 <= int(tok) <= n:
            chosen.add(int(tok))
        else:
            bad.append(tok)
    return sorted(chosen), bad


# =============================================================================
#  SAFE CLEANER  -  the hard "no harm" whitelist for every deletion
# =============================================================================
class SafeCleaner:
    """
    The ONLY code in this program that deletes files.
    It can delete only inside the whitelisted folders below, and only the
    file patterns allowed for that folder.  Everything else is refused.
    These are all caches/temp data that Windows or apps recreate on demand.
    """

    def __init__(self):
        env = os.environ
        lad = env.get("LOCALAPPDATA", "")
        pd = env.get("ProgramData", r"C:\ProgramData")
        self.rules = {}      # normalized path -> rule dict
        self.by_id = {}

        def add(rid, label, path, patterns=None, group="other", min_age=0):
            if not path:
                return
            key = os.path.normcase(os.path.abspath(path))
            if key in self.rules:      # e.g. %TEMP% == %LOCALAPPDATA%\Temp
                return
            rule = {"id": rid, "label": label, "path": os.path.abspath(path),
                    "patterns": patterns, "group": group, "min_age": min_age}
            self.rules[key] = rule
            self.by_id[rid] = rule

        # Temp folders: only accepted when the folder is really named Temp/Tmp
        # (protects against a mis-set %TEMP% pointing at something important).
        for var in ("TEMP", "TMP"):
            p = env.get(var)
            if p and os.path.basename(os.path.normpath(p)).lower() in ("temp", "tmp"):
                add("user_temp", "User Temp folder", p, None, "temp", 3600)
        if lad:
            add("local_temp", "Local AppData Temp", os.path.join(lad, "Temp"), None, "temp", 3600)
        add("win_temp", "Windows Temp folder", os.path.join(SYSROOT, "Temp"), None, "temp", 3600)
        for sub in ("ReportQueue", "ReportArchive", "Temp"):
            add("wer_pd_" + sub, "Error-report dumps (system / %s)" % sub,
                os.path.join(pd, "Microsoft", "Windows", "WER", sub), None, "temp", 0)
            if lad:
                add("wer_user_" + sub, "Error-report dumps (user / %s)" % sub,
                    os.path.join(lad, "Microsoft", "Windows", "WER", sub), None, "temp", 0)
        add("wu_download", "Windows Update download cache",
            os.path.join(SYSROOT, "SoftwareDistribution", "Download"), None, "wu", 0)
        add("spool", "Stuck print jobs (*.SHD / *.SPL only)",
            os.path.join(SYS32, "spool", "PRINTERS"), ["*.shd", "*.spl"], "spool", 0)
        if lad:
            add("explorer_cache", "Icon & thumbnail cache",
                os.path.join(lad, "Microsoft", "Windows", "Explorer"),
                ["iconcache*.db", "thumbcache*.db"], "explorer", 0)
            add("icon_root", "IconCache.db", lad, ["iconcache.db"], "explorer", 0)
        add("font_cache", "Windows font cache",
            os.path.join(SYSROOT, "ServiceProfiles", "LocalService", "AppData", "Local", "FontCache"),
            ["*.dat"], "fonts", 0)
        add("search_index", "Windows Search index database",
            os.path.join(pd, "Microsoft", "Search", "Data", "Applications", "Windows"),
            ["windows.edb", "windows.db"], "search", 0)

    @staticmethod
    def _reparse(st):
        return bool(getattr(st, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)

    def group(self, group):
        return [r for r in self.rules.values() if r["group"] == group]

    def size(self, rid):
        """Read-only size calculation of a whitelisted folder."""
        rule = self.by_id.get(rid)
        total = 0
        if not rule or not os.path.isdir(rule["path"]):
            return 0
        stack = [rule["path"]]
        while stack:
            cur = stack.pop()
            try:
                with os.scandir(cur) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                            if self._reparse(st):
                                continue
                            if e.is_dir(follow_symlinks=False):
                                if rule["patterns"] is None:
                                    stack.append(e.path)
                            elif rule["patterns"] is None or any(
                                    fnmatch.fnmatch(e.name.lower(), p) for p in rule["patterns"]):
                                total += st.st_size
                        except Exception:
                            continue
            except Exception:
                continue
        return total

    def clear(self, rid):
        """Delete contents of ONE whitelisted folder. Returns stats dict."""
        stats = {"files": 0, "bytes": 0, "skipped": 0}
        rule = self.by_id.get(rid)
        if not rule:
            raise PermissionError("Folder is not on the safety whitelist: %s" % rid)
        folder = rule["path"]
        # Final guard: the path must still be an exact whitelist match.
        if os.path.normcase(os.path.abspath(folder)) not in self.rules:
            raise PermissionError("Refused by whitelist: %s" % folder)
        if not os.path.isdir(folder):
            return stats
        self._walk(folder, rule["patterns"], rule["min_age"], stats)
        return stats

    def _walk(self, folder, patterns, min_age, stats):
        try:
            entries = list(os.scandir(folder))
        except Exception:
            stats["skipped"] += 1
            return
        for e in entries:
            if CANCEL.is_set():
                return
            try:
                st = e.stat(follow_symlinks=False)
                if self._reparse(st):               # never follow junctions / symlinks
                    stats["skipped"] += 1
                    continue
                if e.is_dir(follow_symlinks=False):
                    if patterns is None:            # only full-clear folders go recursive
                        self._walk(e.path, None, min_age, stats)
                        try:
                            os.rmdir(e.path)        # succeeds only if empty
                        except Exception:
                            pass
                    continue
                if patterns and not any(fnmatch.fnmatch(e.name.lower(), p) for p in patterns):
                    continue
                if min_age and (time.time() - st.st_mtime) < min_age:
                    stats["skipped"] += 1           # too recent: maybe in use
                    continue
                try:
                    os.chmod(e.path, stat.S_IWRITE)
                except Exception:
                    pass
                os.remove(e.path)
                stats["files"] += 1
                stats["bytes"] += st.st_size
            except Exception:                        # locked / in use / access denied
                stats["skipped"] += 1


# =============================================================================
#  SYSTEM TELEMETRY
# =============================================================================
class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", ctypes.c_uint32), ("hi", ctypes.c_uint32)]


class _MEMSTATUS(ctypes.Structure):
    _fields_ = [("dwLength", ctypes.c_uint32), ("dwMemoryLoad", ctypes.c_uint32),
                ("ullTotalPhys", ctypes.c_uint64), ("ullAvailPhys", ctypes.c_uint64),
                ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64),
                ("ullAvailExtendedVirtual", ctypes.c_uint64)]


class SystemTelemetry:
    """Live system information (read-only; changes nothing)."""

    def __init__(self):
        self._prev = None

    @staticmethod
    def is_admin():
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    @staticmethod
    def os_name():
        try:
            build = int(platform.version().split(".")[-1])
            name = "Windows 11" if build >= 22000 else "Windows 10"
            return "%s (build %d)" % (name, build)
        except Exception:
            return platform.platform()

    @staticmethod
    def uptime_seconds():
        try:
            f = ctypes.windll.kernel32.GetTickCount64
            f.restype = ctypes.c_uint64
            return f() / 1000.0
        except Exception:
            return 0.0

    def cpu_percent(self):
        """CPU load from GetSystemTimes deltas (no psutil needed)."""
        try:
            idle, kern, user = _FILETIME(), _FILETIME(), _FILETIME()
            ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kern), ctypes.byref(user))
            cur = tuple((t.hi << 32) | t.lo for t in (idle, kern, user))
            prev, self._prev = self._prev, cur
            if not prev:
                return 0.0
            di, dk, du = (cur[i] - prev[i] for i in range(3))
            total = dk + du
            return max(0.0, min(100.0, (1.0 - di / total) * 100.0)) if total else 0.0
        except Exception:
            return 0.0

    @staticmethod
    def ram():
        """Returns (percent, used_bytes, total_bytes)."""
        try:
            ms = _MEMSTATUS()
            ms.dwLength = ctypes.sizeof(_MEMSTATUS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return float(ms.dwMemoryLoad), ms.ullTotalPhys - ms.ullAvailPhys, ms.ullTotalPhys
        except Exception:
            return 0.0, 0, 0

    @staticmethod
    def disk_c():
        """Returns (free_bytes, total_bytes)."""
        try:
            drive = os.environ.get("SystemDrive", "C:") + "\\"
            u = shutil.disk_usage(drive)
            return u.free, u.total
        except Exception:
            return 0, 0

    @staticmethod
    def smart_details():
        """WMI Win32_DiskDrive status via PowerShell. Returns (overall, [lines])."""
        try:
            r = ps_cmd("Get-CimInstance Win32_DiskDrive | Select-Object Model,Status | ConvertTo-Json -Compress",
                       timeout=40)
            if not r["success"] or not r["stdout"].strip():
                return "Unknown", []
            data = json.loads(r["stdout"].strip())
            if isinstance(data, dict):
                data = [data]
            lines, bad = [], False
            for d in data:
                status = str(d.get("Status", "Unknown"))
                lines.append("%s : %s" % (d.get("Model", "Disk"), status))
                if status.strip().lower() not in ("ok",):
                    bad = True
            return ("WARNING" if bad else "OK"), lines
        except Exception:
            return "Unknown", []


# =============================================================================
#  TASK CONTEXT  (what a fixer uses to talk to the GUI from a worker thread)
# =============================================================================
class TaskContext:
    PCT = re.compile(r"(\d{1,3}(?:\.\d+)?)\s*%")

    def __init__(self, q):
        self.q = q
        self.lo, self.hi = 0.0, 100.0      # local span of the current command
        self.base, self.scale = 0.0, 1.0   # window of the whole bar (used by pipelines)
        self.prefix = ""                   # e.g. "Step 2/3 · "

    # --- output helpers
    def log(self, text, tag="info"):
        self.q.put(("log", str(text), tag))

    def ok(self, t): self.log("✔ " + t, "ok")
    def warn(self, t): self.log("⚠ " + t, "warn")
    def err(self, t): self.log("✖ " + t, "err")
    def step(self, t):
        self.log("➤ " + t, "step")
        self.phase(t)

    def phase(self, t):
        self.q.put(("phase", self.prefix + t))

    def plan(self, n):
        self.q.put(("plan", n))
    def dim(self, t): self.log("   " + t, "dim")

    def progress(self, pct):
        self.q.put(("progress", None if pct is None else self.base + float(pct) * self.scale))

    def span(self, lo, hi):
        self.lo, self.hi = float(lo), float(hi)

    def status(self, text):
        self.q.put(("status", text))

    def cancelled(self):
        return CANCEL.is_set()

    def offer_reboot(self):
        self.q.put(("offer_reboot",))

    def refresh_telemetry(self):
        self.q.put(("refresh",))

    # --- command runners with live output
    def run(self, cmd, timeout=120, live=True):
        return run_cmd(cmd, timeout, self._line if live else None)

    def ps(self, script, timeout=120, live=True):
        return ps_cmd(script, timeout, self._line if live else None)

    def _line(self, raw):
        s = raw.replace("\x08", "").strip()
        if not s or s.startswith("#< CLIXML") or s.startswith("<Objs"):
            return
        if re.fullmatch(r"[-\\|/ ]+", s):                    # winget spinner
            return
        m = self.PCT.search(s)
        low = s.lower()
        is_bar = s.startswith("[") or any(ch in s for ch in "█▒░")
        if m and (is_bar or "complete" in low or "verification" in low):
            try:
                pct = max(0.0, min(100.0, float(m.group(1))))
                self.progress(self.lo + (self.hi - self.lo) * pct / 100.0)
                self.status(s[:90])
            except Exception:
                pass
            return
        if re.match(r"^\d+(\.\d+)?\s*[KMG]B\s*/", s):         # winget download size line
            self.status(s)
            return
        self.status(s[:80])
        self.log("   " + s, "dim")


# =============================================================================
#  FIX CATALOG  (plain-English descriptions shown in the permission dialog)
# =============================================================================
class Risk(str, Enum):
    READ_ONLY = "read-only"
    LOW = "low"
    MEDIUM = "medium"


class Category(Enum):
    CLEAN = "CLEANUP & SPEED"
    REPAIR = "SYSTEM REPAIR"
    NET = "NETWORK & INTERNET"
    UPD = "WINDOWS UPDATE & APPS"
    DEV = "DEVICES & SERVICES"
    DIAG = "DIAGNOSTICS (READ-ONLY) & SAFETY"


@dataclass(frozen=True)
class Fix:
    """One fixer in the catalog (immutable). Dict-style access is kept for convenience."""
    key: str
    category: Category
    title: str
    short: str
    does: Tuple[str, ...]
    safe: Tuple[str, ...]
    risk: Risk = Risk.LOW
    reboot: bool = False
    eta: str = "1-2 minutes"
    note: Optional[str] = None

    def __getitem__(self, name):
        return getattr(self, "category" if name == "cat" else name)

    def get(self, name, default=None):
        try:
            return self[name]
        except AttributeError:
            return default


@dataclass(frozen=True)
class Pipeline:
    """A named sequence of fixers run behind ONE permission dialog."""
    key: str
    title: str
    short: str
    fix_keys: Tuple[str, ...]
    color: str


@dataclass
class TaskRecord:
    title: str
    seconds: float
    cancelled: bool


def F(key, cat, title, short, does, safe, risk="low", reboot=False, eta="1-2 minutes", note=None):
    return Fix(key, cat, title, short, tuple(does), tuple(safe), Risk(risk), reboot, eta, note)


CAT_CLEAN, CAT_REPAIR, CAT_NET = Category.CLEAN, Category.REPAIR, Category.NET
CAT_UPD, CAT_DEV, CAT_DIAG = Category.UPD, Category.DEV, Category.DIAG

FIXES = [
    F("clean", CAT_CLEAN, "Deep Temp, Cache & Lag Cleaner",
      "Removes temporary files, old error-report dumps and flushes the DNS cache.",
      ["Deletes unused files inside your Temp folders and Windows\\Temp (files from the last hour are skipped).",
       "Deletes old Windows Error Reporting (crash report) dumps.",
       "Flushes the DNS resolver cache."],
      ["Documents, Pictures, Desktop, Downloads and all personal files are NEVER touched.",
       "Files that are in use are skipped automatically. Windows & apps recreate any cache they need."],
      "low", False, "1-3 minutes"),
    F("component", CAT_CLEAN, "Component Store Cleanup (free disk space)",
      "Uses Windows' own tool to remove superseded update files from WinSxS.",
      ["Runs DISM /AnalyzeComponentStore, then DISM /StartComponentCleanup.",
       "Frees space by removing old versions of system components that are no longer needed."],
      ["Does NOT use /ResetBase, so you can still uninstall recent updates.",
       "Pure Microsoft tool; personal files are not involved."],
      "low", False, "3-15 minutes"),
    F("fonts", CAT_CLEAN, "Font Cache Rebuild",
      "Fixes missing, garbled or slow-loading fonts.",
      ["Stops the Windows Font Cache service, deletes its *.dat cache files, starts the service again."],
      ["Your installed fonts are NOT removed - only the cache index is rebuilt automatically by Windows."],
      "low", True, "under 1 minute"),
    F("explorer", CAT_CLEAN, "Explorer, Icon & Thumbnail Cache Fixer",
      "Fixes blank/wrong icons, broken thumbnails and Explorer glitches.",
      ["Closes File Explorer (taskbar & desktop flicker for a few seconds).",
       "Deletes iconcache*.db, thumbcache*.db and IconCache.db.",
       "Windows restarts Explorer; this tool makes sure it is running again."],
      ["Only cache files are removed - they are regenerated automatically.",
       "Any file copy happening in Explorer right now would be interrupted, so finish those first."],
      "low", False, "under 1 minute"),
    F("search", CAT_CLEAN, "Windows Search Index Rebuild",
      "Fixes Start-menu / File Explorer search that finds nothing or is stuck.",
      ["Stops the Windows Search service, deletes its index database, starts the service again.",
       "Windows then rebuilds the index in the background."],
      ["Your files are not touched - only the search index (a lookup table) is rebuilt.",
       "Search results may be incomplete for a while (minutes to hours) while it re-indexes."],
      "medium", False, "1-2 minutes"),

    F("sfcdism", CAT_REPAIR, "Full System File Repair (DISM + SFC) - recommended",
      "The official 2-step repair: DISM first, then SFC. Fixes corrupted Windows files.",
      ["Runs DISM /Online /Cleanup-Image /RestoreHealth (may download clean files from Windows Update).",
       "Then runs sfc /scannow to replace damaged system files with authentic Microsoft copies."],
      ["Only damaged/missing Windows system files are replaced with genuine Microsoft versions.",
       "Your programs, settings and personal files are not changed."],
      "low", False, "15-60 minutes",
      "Needs internet for DISM. It can seem stuck at 20% or 62% - this is normal. Please keep the PC on."),
    F("dism", CAT_REPAIR, "DISM Image Repair only",
      "Repairs the Windows component store (the source SFC uses).",
      ["Runs DISM /Online /Cleanup-Image /RestoreHealth."],
      ["Microsoft's own repair tool. Non-destructive."], "low", False, "10-40 minutes",
      "Needs internet. May appear stuck at 20% / 62% - normal."),
    F("sfc", CAT_REPAIR, "SFC System File Check only",
      "Scans and repairs protected system files.",
      ["Runs sfc /scannow."],
      ["SFC only replaces missing/corrupt system files with authentic Microsoft copies."],
      "low", False, "5-20 minutes"),
    F("disk", CAT_REPAIR, "Disk Health (SMART) + Online Disk Scan",
      "Checks drive health and scans the C: file system without a reboot.",
      ["Reads drive health status from Windows (WMI Win32_DiskDrive).",
       "Runs chkdsk C: /scan (online, read-only scan)."],
      ["/scan never unmounts the drive and does not modify data. Any repairs it recommends are NOT run automatically."],
      "low", False, "5-30 minutes"),
    F("wmi", CAT_REPAIR, "WMI Repository Check",
      "Detects a corrupted WMI database (causes odd errors & missing system info).",
      ["Runs winmgmt /verifyrepository (read-only check)."],
      ["Only verifies. It does not change anything."], "read-only", False, "under 1 minute"),
    F("shellapps", CAT_REPAIR, "Fix Start Menu, Search & Settings App",
      "Re-registers core Windows shell apps when Start/Settings won't open.",
      ["Re-registers Start Menu, Shell Experience, Search and Settings packages using their existing manifests."],
      ["Re-registration only refreshes the app registration. Nothing is removed or reinstalled from the web."],
      "medium", False, "1-3 minutes"),

    F("netquick", CAT_NET, "Quick Internet Fix (DNS refresh + connection test)",
      "Safe first step for 'no internet', slow sites or DNS errors.",
      ["Flushes and re-registers DNS, then tests 1.1.1.1, 8.8.8.8 and a DNS lookup."],
      ["No settings are changed. Your Wi-Fi passwords and IP settings stay as they are."],
      "low", False, "under 1 minute"),
    F("netfull", CAT_NET, "Full Network Reset (Winsock, TCP/IP, ARP, DNS)",
      "Strong fix for Wi-Fi/Ethernet problems, 'limited connectivity' and broken sockets.",
      ["Flushes & re-registers DNS, resets the Winsock catalog, resets the TCP/IP stack, clears the ARP cache.",
       "Finishes with a ping test of 1.1.1.1 and 8.8.8.8."],
      ["Saved Wi-Fi networks and passwords are kept.",
       "CAUTION: a manually typed (static) IP address or custom DNS on adapters may need to be re-entered afterwards. "
       "Some VPN / firewall software that hooks into Winsock may need to be reinstalled (rare).",
       "A restart is needed to finish."],
      "medium", True, "1-2 minutes"),
    F("proxy", CAT_NET, "Proxy Check & Reset (WinHTTP)",
      "Fixes 'cannot connect' errors caused by a leftover proxy.",
      ["Shows your current proxy settings, then runs netsh winhttp reset proxy."],
      ["If your company/school requires a proxy, you may need to set it again. Home users: safe."],
      "medium", False, "under 1 minute"),
    F("time", CAT_NET, "Fix Windows Clock / Time Sync",
      "Fixes wrong date/time (which also breaks HTTPS sites and updates).",
      ["Starts the Windows Time service if stopped and forces a re-sync with the time server."],
      ["Your time-zone and clock settings are not edited - only a normal sync is requested."],
      "low", False, "under 1 minute"),

    F("wu", CAT_UPD, "Windows Update Stuck / Error Fixer",
      "Fixes updates that are stuck, looping or failing.",
      ["Stops Update, Cryptographic, BITS and Installer services (with time-outs).",
       "Clears the SoftwareDistribution\\Download cache.",
       "Starts the services that were running before."],
      ["Installed updates and your update history are kept. Windows re-downloads what it needs."],
      "low", False, "1-3 minutes"),
    F("store", CAT_UPD, "Microsoft Store & UWP App Repair",
      "Fixes Store not opening, downloads stuck or app-install errors.",
      ["Runs wsreset.exe -i in the background (Store reset/reinstall).",
       "Re-registers Store, Store Purchase and App Installer packages for all users."],
      ["Re-registration is non-destructive. Installed apps and their data stay in place."],
      "medium", False, "2-5 minutes"),
    F("winget", CAT_UPD, "Winget Pro - Interactive App Update Manager",
      "Lists outdated apps and lets YOU pick which to update.",
      ["Reads the list of available updates from winget, shows a numbered table, and updates only what you select."],
      ["Nothing is uninstalled. Only the apps you choose are updated, silently."],
      "low", False, "depends on selection"),

    F("spooler", CAT_DEV, "Printer Spooler & Stuck Queue Fixer",
      "Fixes print jobs that will not print or cannot be deleted.",
      ["Stops the Print Spooler, deletes stuck *.SHD / *.SPL job files, starts the spooler again."],
      ["Pending print jobs are cancelled (you would need to print them again). Printers and drivers are untouched."],
      "low", False, "under 1 minute"),
    F("audio", CAT_DEV, "Fix No Sound (restart Windows Audio services)",
      "Fixes missing sound, 'No audio device' and crackling after sleep.",
      ["Restarts Windows Audio and Audio Endpoint Builder services."],
      ["Audio pauses for a few seconds. Drivers and settings are untouched."],
      "low", False, "under 1 minute"),
    F("bluetooth", CAT_DEV, "Fix Bluetooth (restart Bluetooth service)",
      "Fixes Bluetooth devices not showing or not connecting.",
      ["Restarts the Bluetooth Support Service."],
      ["Paired devices stay paired. Connected devices reconnect after a moment."],
      "low", False, "under 1 minute"),
    F("defender", CAT_DEV, "Security: Update Defender + Quick Scan",
      "Updates Microsoft Defender definitions and runs a quick virus scan.",
      ["Runs Update-MpSignature and Start-MpScan -ScanType QuickScan."],
      ["Scanning only; threats are handled by Defender's normal policy. Skipped if another antivirus is in charge."],
      "low", False, "5-20 minutes"),
    F("power", CAT_DEV, "Restore Default Power Plans",
      "Fixes missing power plans, battery/sleep oddities.",
      ["Exports your current power plans to a backup folder on your Desktop first.",
       "Runs powercfg /restoredefaultschemes."],
      ["Custom power plans you created are removed (a backup .pow file of each is saved first)."],
      "medium", False, "under 1 minute"),

    F("events", CAT_DIAG, "Recent Critical Errors Report (last 3 days)",
      "Shows the newest critical/error events to help find the real cause of problems.",
      ["Reads the System and Application event logs (read-only)."],
      ["Nothing is changed or deleted."], "read-only", False, "under 1 minute"),
    F("memdiag", CAT_DIAG, "Windows Memory Diagnostic (RAM test)",
      "Opens Microsoft's RAM test. Useful for random crashes / blue screens.",
      ["Opens the official Windows Memory Diagnostic window. YOU decide whether to restart and test."],
      ["Nothing happens unless you choose an option in that window."], "read-only", False, "opens a window"),
    F("restore", CAT_DIAG, "Create System Restore Point",
      "Makes a rollback point so you can undo changes.",
      ["Creates a restore point named 'WindowsFixerPro'."],
      ["Adds a restore point only. Requires System Restore to be turned on (Windows may limit to one per 24 hours)."],
      "low", False, "under 1 minute"),
]


FIX_BY_KEY: Dict[str, Fix] = {f.key: f for f in FIXES}

PIPELINES = (
    Pipeline("tuneup", "⚡ Quick Tune-Up", "Cleaner + internet refresh + clock sync",
             ("clean", "netquick", "time"), C.GREEN),
    Pipeline("deep", "⚙ Deep Repair", "DISM + SFC, disk scan, component cleanup",
             ("sfcdism", "disk", "component"), C.AQUA),
)


def combine(pl):
    """Build one permission-dialog entry that describes every step of a pipeline."""
    fixes = [FIX_BY_KEY[k] for k in pl.fix_keys]
    does = tuple("Step %d - %s: %s" % (i, f.title, f.short) for i, f in enumerate(fixes, 1))
    safe = tuple(dict.fromkeys(x for f in fixes for x in f.safe))
    order = [Risk.READ_ONLY, Risk.LOW, Risk.MEDIUM]
    risk = max((f.risk for f in fixes), key=order.index)
    note = " ".join(f.note for f in fixes if f.note) or None
    return Fix("pipeline_" + pl.key, Category.DIAG, pl.title, pl.short, does, safe, risk,
               any(f.reboot for f in fixes), "see each step", note)


# =============================================================================
#  WIN REPAIR  -  all fixers
# =============================================================================
class WinRepair:
    def __init__(self, cleaner):
        self.cleaner = cleaner

    def run(self, fx, ctx):
        fn = getattr(self, "fx_" + fx["key"], None)
        if fn is None:
            ctx.err("This fixer is not available.")
            return
        try:
            fn(ctx)
        except Exception as exc:  # last line of defence: never crash
            ctx.err("A problem was handled safely: %s" % exc)

    def run_many(self, fixes, ctx):
        """Run several fixers in order; the progress bar shows one segment per step."""
        n = len(fixes)
        try:
            for i, fx in enumerate(fixes):
                if ctx.cancelled():
                    ctx.warn("Pipeline cancelled - remaining steps skipped.")
                    break
                ctx.base, ctx.scale = i * 100.0 / n, 1.0 / n
                ctx.lo, ctx.hi = 0.0, 100.0
                ctx.prefix = "Step %d/%d · " % (i + 1, n)
                ctx.log("─" * 10 + "  STEP %d/%d: %s  " % (i + 1, n, fx.title) + "─" * 10, "head")
                ctx.phase(fx.title)
                self.run(fx, ctx)
        finally:
            ctx.base, ctx.scale, ctx.prefix = 0.0, 1.0, ""
            ctx.lo, ctx.hi = 0.0, 100.0
        if not ctx.cancelled():
            ctx.progress(100)
            ctx.ok("All %d steps finished." % n)

    # ------------------------------------------------------------ helpers
    def svc_state(self, name):
        r = run_cmd(["sc", "query", name], timeout=15)
        out = r["stdout"].upper()
        if "1060" in out:
            return "MISSING"
        m = re.search(r"STATE\s*:\s*\d+\s+([A-Z_]+)", out)
        if m:
            return {"RUNNING": "RUNNING", "STOPPED": "STOPPED"}.get(m.group(1), "PENDING")
        return "UNKNOWN"

    def stop_service(self, ctx, name, timeout=45):
        st = self.svc_state(name)
        if st == "MISSING":
            ctx.dim("Service %s not present - skipped" % name)
            return True
        if st == "STOPPED":
            ctx.dim("Service %s already stopped" % name)
            return True
        ctx.run(["net", "stop", name, "/y"], timeout=timeout, live=False)
        if self.svc_state(name) == "STOPPED":
            ctx.ok("Stopped %s" % name)
            return True
        run_cmd(["sc", "stop", name], timeout=15)         # fallback; never hangs
        for _ in range(max(1, timeout // 3)):
            if self.svc_state(name) == "STOPPED" or ctx.cancelled():
                break
            time.sleep(3)
        if self.svc_state(name) == "STOPPED":
            ctx.ok("Stopped %s" % name)
            return True
        ctx.warn("%s did not stop in time - continuing safely (locked files will be skipped)" % name)
        return False

    def start_service(self, ctx, name, timeout=45):
        st = self.svc_state(name)
        if st == "MISSING":
            return False
        if st == "RUNNING":
            ctx.dim("Service %s already running" % name)
            return True
        ctx.run(["net", "start", name], timeout=timeout, live=False)
        if self.svc_state(name) == "RUNNING":
            ctx.ok("Started %s" % name)
            return True
        ctx.warn("Could not start %s right now (Windows will start it when needed / after reboot)" % name)
        return False

    def clear_rule(self, ctx, rid, label=None):
        rule = self.cleaner.by_id.get(rid)
        if not rule:
            return {"files": 0, "bytes": 0, "skipped": 0}
        try:
            s = self.cleaner.clear(rid)
        except Exception as exc:
            ctx.warn("Skipped %s: %s" % (label or rid, exc))
            return {"files": 0, "bytes": 0, "skipped": 0}
        ctx.log("   %s: %d items removed, %s freed, %d in-use/recent skipped" %
                (label or rule["label"], s["files"], fmt_bytes(s["bytes"]), s["skipped"]),
                "ok" if s["files"] else "dim")
        return s

    def ping_test(self, ctx):
        good = 0
        for host in ("1.1.1.1", "8.8.8.8"):
            r = ctx.run(["ping", "-n", "3", "-w", "2000", host], timeout=30, live=False)
            if "TTL=" in r["stdout"].upper():
                times = re.findall(r"=\s*(\d+)\s*ms", r["stdout"])
                ctx.ok("Ping %s: reachable%s" % (host, (" (avg %s ms)" % times[-1]) if times else ""))
                good += 1
            else:
                ctx.err("Ping %s: no reply" % host)
        try:
            socket.setdefaulttimeout(6)
            ip = socket.gethostbyname("www.microsoft.com")
            ctx.ok("DNS lookup works (www.microsoft.com -> %s)" % ip)
        except Exception:
            ctx.err("DNS lookup failed (names cannot be resolved)")
        if good == 2:
            ctx.ok("Internet connectivity: GOOD")
        elif good == 1:
            ctx.warn("Internet connectivity: PARTIAL - one test failed")
        else:
            ctx.err("Internet connectivity: NO REPLY. Check cable/Wi-Fi, restart your router, then restart this PC.")
        return good

    def make_restore_point(self, ctx):
        ctx.step("Creating System Restore point (safety net)")
        r = ctx.ps("Checkpoint-Computer -Description 'WindowsFixerPro' -RestorePointType MODIFY_SETTINGS "
                   "-ErrorAction Stop; 'RP_OK'", timeout=180, live=False)
        if "RP_OK" in r["stdout"]:
            ctx.ok("Restore point created")
            return True
        ctx.warn("Restore point not created (System Restore may be off, or one was made in the last 24 h). "
                 "Continuing - this fix is still non-destructive.")
        return False

    # ------------------------------------------------------------ cleanup
    def fx_clean(self, ctx):
        rules = [r for r in self.cleaner.group("temp")]
        total_b = total_f = 0
        n = len(rules) + 1
        for i, rule in enumerate(rules):
            if ctx.cancelled():
                return
            ctx.progress(i * 90.0 / n)
            ctx.step("Cleaning: %s" % rule["label"])
            s = self.clear_rule(ctx, rule["id"])
            total_b += s["bytes"]
            total_f += s["files"]
        ctx.progress(92)
        ctx.step("Flushing DNS resolver cache")
        r = ctx.run(["ipconfig", "/flushdns"], timeout=30, live=False)
        (ctx.ok if r["success"] else ctx.warn)("DNS cache flush " + ("completed" if r["success"] else "was not possible"))
        ctx.progress(100)
        ctx.ok("TOTAL: %d items removed, %s freed." % (total_f, fmt_bytes(total_b)))
        ctx.refresh_telemetry()

    def fx_component(self, ctx):
        ctx.span(0, 30)
        ctx.step("Analyzing component store (read-only)")
        r = ctx.run(["DISM.exe", "/Online", "/Cleanup-Image", "/AnalyzeComponentStore"], timeout=900)
        if ctx.cancelled():
            return
        ctx.span(30, 100)
        ctx.step("Removing superseded components (safe cleanup)")
        r = ctx.run(["DISM.exe", "/Online", "/Cleanup-Image", "/StartComponentCleanup"], timeout=3600)
        (ctx.ok if r["success"] else ctx.warn)("Component store cleanup " + ("finished" if r["success"] else "did not complete"))
        ctx.progress(100)
        ctx.refresh_telemetry()

    def fx_fonts(self, ctx):
        svcs = ["FontCache", "FontCache3.0.0.0"]
        was = {s: self.svc_state(s) == "RUNNING" for s in svcs}
        try:
            for s in svcs:
                self.stop_service(ctx, s, 30)
            ctx.step("Deleting font cache files")
            self.clear_rule(ctx, "font_cache")
        finally:
            for s in svcs:
                if was[s]:
                    self.start_service(ctx, s, 30)
        ctx.ok("Font cache rebuilt. Restart Windows if fonts still look wrong.")

    def fx_explorer(self, ctx):
        ctx.step("Closing File Explorer (screen will flicker)")
        ctx.run(["taskkill", "/F", "/IM", "explorer.exe"], timeout=20, live=False)
        try:
            time.sleep(1.5)
            ctx.step("Deleting icon & thumbnail cache files")
            self.clear_rule(ctx, "explorer_cache")
            self.clear_rule(ctx, "icon_root", "IconCache.db")
        finally:
            ctx.step("Making sure Explorer is running again")
            running = False
            for _ in range(8):          # Windows normally auto-restarts the shell
                time.sleep(1)
                r = run_cmd(["tasklist", "/FI", "IMAGENAME eq explorer.exe"], timeout=15)
                if "explorer.exe" in r["stdout"].lower():
                    running = True
                    break
            if not running:
                try:
                    subprocess.Popen(["explorer.exe"], creationflags=CREATE_NO_WINDOW)
                    ctx.ok("Explorer restarted")
                except Exception as exc:
                    ctx.err("Could not start Explorer automatically (%s). Press Ctrl+Shift+Esc > Run new task > explorer.exe" % exc)
            else:
                ctx.ok("Explorer is running")
            run_cmd(["ie4uinit.exe", "-show"], timeout=20)   # asks Windows to refresh icons
        ctx.ok("Icon/thumbnail caches will rebuild automatically.")

    def fx_search(self, ctx):
        was = self.svc_state("WSearch") == "RUNNING"
        try:
            self.stop_service(ctx, "WSearch", 60)
            ctx.step("Deleting the old search index database")
            self.clear_rule(ctx, "search_index")
        finally:
            if was or True:
                self.start_service(ctx, "WSearch", 45)
        ctx.ok("Windows is now rebuilding the search index in the background.")

    # ------------------------------------------------------------ repair
    def _dism(self, ctx, lo, hi):
        ctx.span(lo, hi)
        ctx.step("DISM: repairing Windows image (needs internet; 20% / 62% pauses are normal)")
        r = ctx.run(["DISM.exe", "/Online", "/Cleanup-Image", "/RestoreHealth"], timeout=7200)
        out = r["stdout"].lower()
        if r["exit_code"] == -3:
            ctx.warn("DISM cancelled by you")
        elif r["exit_code"] == -2:
            ctx.err("DISM timed out")
        elif r["success"]:
            ctx.ok("DISM completed: the Windows image is healthy / was repaired.")
        elif "0x800f081f" in out or "0x800f0906" in out or "0x800f0915" in out:
            ctx.err("DISM could not find repair files. Check your internet connection and run Windows Update, then retry.")
        else:
            ctx.err("DISM finished with code %s. See log above." % r["exit_code"])
        return r

    def _sfc(self, ctx, lo, hi):
        ctx.span(lo, hi)
        ctx.step("SFC: scanning protected system files")
        r = ctx.run(["sfc.exe", "/scannow"], timeout=7200)
        out = r["stdout"].lower()
        if r["exit_code"] == -3:
            ctx.warn("SFC cancelled by you")
        elif "did not find any integrity violations" in out:
            ctx.ok("SFC: no integrity violations - system files are healthy.")
        elif "successfully repaired" in out:
            ctx.ok("SFC found corrupt files and repaired them.")
        elif "unable to fix" in out or "could not perform the requested operation" in out:
            ctx.warn("SFC could not fix everything. Run DISM first, then SFC again (use the combined repair).")
        elif r["success"]:
            ctx.ok("SFC completed.")
        else:
            ctx.warn("SFC finished with code %s." % r["exit_code"])
        return r

    def fx_dism(self, ctx):
        self._dism(ctx, 0, 100)
        ctx.progress(100)

    def fx_sfc(self, ctx):
        self._sfc(ctx, 0, 100)
        ctx.progress(100)

    def fx_sfcdism(self, ctx):
        self._dism(ctx, 0, 55)
        if ctx.cancelled():
            return
        self._sfc(ctx, 55, 100)
        ctx.progress(100)
        ctx.ok("Full system file repair finished. Restart the PC when convenient.")

    def fx_disk(self, ctx):
        ctx.step("Checking physical disk health (WMI Win32_DiskDrive)")
        overall, lines = SystemTelemetry.smart_details()
        for ln in lines:
            ctx.log("   " + ln, "ok" if ln.rstrip().lower().endswith(": ok") else "err")
        if overall == "OK":
            ctx.ok("Drive health: OK")
        elif overall == "WARNING":
            ctx.err("A drive reports a problem! BACK UP YOUR IMPORTANT FILES NOW and consider replacing the drive.")
        else:
            ctx.warn("Drive health could not be read (some drives/RAID do not report it).")
        ctx.progress(10)
        ctx.span(10, 100)
        ctx.step("Running online file-system scan: chkdsk C: /scan (read-only, no reboot)")
        drive = os.environ.get("SystemDrive", "C:")
        r = ctx.run(["chkdsk.exe", drive, "/scan"], timeout=7200)
        out = r["stdout"].lower()
        if "found no problems" in out or r["exit_code"] == 0:
            ctx.ok("CHKDSK: no problems found.")
        elif r["exit_code"] == -3:
            ctx.warn("CHKDSK cancelled by you")
        else:
            ctx.warn("CHKDSK reported issues (code %s). Nothing was changed. "
                     "To repair, open Command Prompt (Admin) and run:  chkdsk %s /spotfix" % (r["exit_code"], drive))
        ctx.progress(100)
        ctx.refresh_telemetry()

    def fx_wmi(self, ctx):
        ctx.step("Verifying WMI repository (read-only)")
        r = ctx.run(["winmgmt", "/verifyrepository"], timeout=120, live=False)
        out = r["stdout"].lower()
        ctx.log("   " + r["stdout"], "dim")
        if "is consistent" in out:
            ctx.ok("WMI repository is consistent (healthy).")
        elif "inconsistent" in out:
            ctx.warn("WMI repository is inconsistent. A manual fix is 'winmgmt /salvagerepository' "
                     "(not run automatically).")
        else:
            ctx.warn("Result unclear (code %s)." % r["exit_code"])

    def _reregister(self, ctx, names, all_users=True):
        arr = ",".join("'%s'" % n for n in names)
        au = "-AllUsers" if all_users else ""
        script = ("foreach($n in @(%s)){ Get-AppxPackage %s -Name $n | ForEach-Object {"
                  "$m=Join-Path $_.InstallLocation 'AppXManifest.xml';"
                  "if($_.InstallLocation -and (Test-Path $m)){ try{ Add-AppxPackage -DisableDevelopmentMode -Register $m "
                  "-ErrorAction Stop; 'OK: '+$_.Name } catch { 'SKIPPED: '+$_.Name } } } }" % (arr, au))
        r = ctx.ps(script, timeout=600, live=False)
        for ln in r["stdout"].splitlines():
            ln = ln.strip()
            if ln.startswith("OK:"):
                ctx.ok("Re-registered " + ln[3:].strip())
            elif ln.startswith("SKIPPED:"):
                ctx.dim("Skipped " + ln[8:].strip() + " (in use or not applicable)")
        if not r["stdout"].strip():
            ctx.dim("No matching packages needed re-registration.")

    def fx_shellapps(self, ctx):
        ctx.step("Re-registering Start Menu / Search / Settings packages")
        self._reregister(ctx, ["Microsoft.Windows.StartMenuExperienceHost",
                               "Microsoft.Windows.ShellExperienceHost",
                               "Microsoft.Windows.Search",
                               "windows.immersivecontrolpanel"])
        ctx.ok("Done. Sign out and back in (or restart) if Start/Settings still misbehave.")

    # ------------------------------------------------------------ network
    def fx_netquick(self, ctx):
        for i, (label, cmd) in enumerate([("Flushing DNS cache", ["ipconfig", "/flushdns"]),
                                          ("Re-registering DNS", ["ipconfig", "/registerdns"])]):
            ctx.step(label)
            r = ctx.run(cmd, timeout=60, live=False)
            (ctx.ok if r["success"] else ctx.warn)("%s %s" % (label, "OK" if r["success"] else "- not possible right now"))
            ctx.progress((i + 1) * 30)
        ctx.step("Testing connectivity")
        self.ping_test(ctx)
        ctx.progress(100)

    def fx_netfull(self, ctx):
        steps = [("Flushing DNS cache", ["ipconfig", "/flushdns"]),
                 ("Re-registering DNS", ["ipconfig", "/registerdns"]),
                 ("Resetting Winsock catalog", ["netsh", "winsock", "reset"]),
                 ("Resetting TCP/IP stack", ["netsh", "int", "ip", "reset"]),
                 ("Clearing ARP cache", ["netsh", "interface", "ip", "delete", "arpcache"])]
        for i, (label, cmd) in enumerate(steps):
            if ctx.cancelled():
                return
            ctx.step(label)
            r = ctx.run(cmd, timeout=90, live=False)
            if r["success"]:
                ctx.ok(label + " - OK")
            else:
                ctx.warn("%s - finished with notes (code %s). This is often normal." % (label, r["exit_code"]))
            ctx.progress((i + 1) * 80.0 / len(steps))
        ctx.step("Testing connectivity (may fail until you restart)")
        self.ping_test(ctx)
        ctx.progress(100)
        ctx.warn("A RESTART is required to finish the network reset.")
        ctx.offer_reboot()

    def fx_proxy(self, ctx):
        ctx.step("Current WinHTTP proxy")
        r = ctx.run(["netsh", "winhttp", "show", "proxy"], timeout=30, live=False)
        ctx.log("   " + r["stdout"].replace("\n", "\n   "), "dim")
        try:                                    # READ-ONLY registry peek
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
                en = winreg.QueryValueEx(k, "ProxyEnable")[0]
                try:
                    srv = winreg.QueryValueEx(k, "ProxyServer")[0]
                except Exception:
                    srv = ""
            ctx.log("   Browser/user proxy: %s %s" % ("ENABLED" if en else "disabled", srv), "dim")
            if en:
                ctx.warn("A user-level proxy is enabled. Turn it off in Settings > Network > Proxy if you don't use one "
                         "(this tool does not edit the registry).")
        except Exception:
            pass
        ctx.step("Resetting WinHTTP proxy")
        r = ctx.run(["netsh", "winhttp", "reset", "proxy"], timeout=30, live=False)
        (ctx.ok if r["success"] else ctx.warn)("WinHTTP proxy reset " + ("done" if r["success"] else "not possible"))

    def fx_time(self, ctx):
        ctx.step("Checking Windows Time service")
        self.start_service(ctx, "w32time", 30)
        ctx.step("Forcing time re-sync")
        r = ctx.run(["w32tm", "/resync", "/force"], timeout=60, live=False)
        ctx.log("   " + r["stdout"], "dim")
        if r["success"]:
            ctx.ok("Clock synchronised.")
        else:
            ctx.warn("Re-sync failed. Check internet, or set time automatically in Settings > Time & language.")

    # ------------------------------------------------------------ update/apps
    def fx_wu(self, ctx):
        svcs = ["wuauserv", "cryptSvc", "bits", "msiserver"]
        was = {s: self.svc_state(s) == "RUNNING" for s in svcs}
        try:
            ctx.step("Stopping update services (with time-outs)")
            for i, s in enumerate(svcs):
                if ctx.cancelled():
                    break
                self.stop_service(ctx, s, 45)
                ctx.progress((i + 1) * 15)
            ctx.step("Clearing SoftwareDistribution\\Download cache")
            self.clear_rule(ctx, "wu_download")
            ctx.progress(75)
        finally:
            ctx.step("Restarting services that were running")
            for s in svcs:
                if was[s]:
                    self.start_service(ctx, s, 45)
        ctx.progress(100)
        ctx.ok("Done. Now open Settings > Windows Update and click 'Check for updates'.")

    def fx_store(self, ctx):
        ctx.step("Starting Store reset in the background (wsreset.exe -i)")
        try:
            subprocess.Popen(["wsreset.exe", "-i"], creationflags=CREATE_NO_WINDOW)
            ctx.ok("Store reset started")
        except Exception as exc:
            ctx.warn("wsreset could not be started: %s" % exc)
        for i in range(10):          # give it a moment; never blocks long
            if ctx.cancelled():
                return
            time.sleep(1)
            ctx.progress(i * 3)
        ctx.step("Re-registering Store packages (non-destructive)")
        self._reregister(ctx, ["Microsoft.WindowsStore", "Microsoft.StorePurchaseApp",
                               "Microsoft.DesktopAppInstaller", "Microsoft.Services.Store.Engagement"])
        ctx.progress(100)
        ctx.ok("Finished. Wait a minute, then open the Microsoft Store.")

    # ------------------------------------------------------------ devices
    def fx_spooler(self, ctx):
        try:
            ctx.step("Stopping Print Spooler")
            self.stop_service(ctx, "Spooler", 45)
            ctx.step("Deleting stuck print-job files (*.SHD, *.SPL)")
            self.clear_rule(ctx, "spool")
        finally:
            ctx.step("Starting Print Spooler")
            self.start_service(ctx, "Spooler", 45)
        ctx.ok("Printing service reset. Try printing again.")

    def _restart_services(self, ctx, stop_order, start_order, label):
        was = {s: self.svc_state(s) == "RUNNING" for s in set(stop_order)}
        try:
            for s in stop_order:
                self.stop_service(ctx, s, 30)
            time.sleep(1)
        finally:
            for s in start_order:
                if was.get(s) or True:
                    self.start_service(ctx, s, 30)
        ctx.ok("%s restarted." % label)

    def fx_audio(self, ctx):
        self._restart_services(ctx, ["Audiosrv", "AudioEndpointBuilder"],
                               ["AudioEndpointBuilder", "Audiosrv"], "Windows Audio services")

    def fx_bluetooth(self, ctx):
        if self.svc_state("bthserv") == "MISSING":
            ctx.warn("No Bluetooth service found on this PC.")
            return
        self._restart_services(ctx, ["bthserv"], ["bthserv"], "Bluetooth service")

    def fx_defender(self, ctx):
        ctx.step("Updating Defender definitions and running a quick scan")
        script = ("try{ Update-MpSignature -ErrorAction Stop; 'Definitions updated' }catch{ 'Definitions: '+$_.Exception.Message };"
                  "try{ Start-MpScan -ScanType QuickScan -ErrorAction Stop; 'Quick scan finished' }catch{ 'Scan: '+$_.Exception.Message };"
                  "try{ $t=Get-MpThreat -ErrorAction Stop; if($t){'Threats detected: '+($t|Measure-Object).Count}else{'No active threats'} }catch{}")
        r = ctx.ps(script, timeout=3600)
        out = r["stdout"].lower()
        if "no active threats" in out or "quick scan finished" in out:
            ctx.ok("Defender scan completed.")
        else:
            ctx.warn("Defender could not complete (another antivirus may be managing protection).")

    def fx_power(self, ctx):
        ctx.step("Backing up your current power plans")
        r = ctx.run(["powercfg", "/list"], timeout=30, live=False)
        guids = re.findall(r"([0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12})", r["stdout"])
        desktop = os.path.join(os.path.expanduser("~"), "Desktop", "WindowsFixerPro_PowerPlan_Backup")
        try:
            os.makedirs(desktop, exist_ok=True)
            for g in guids:
                run_cmd(["powercfg", "/export", os.path.join(desktop, g + ".pow"), g], timeout=30)
            ctx.ok("Backed up %d plan(s) to %s" % (len(guids), desktop))
        except Exception as exc:
            ctx.err("Backup failed (%s). Power plans were NOT reset, to stay safe." % exc)
            return
        ctx.step("Restoring default power plans")
        r = ctx.run(["powercfg", "/restoredefaultschemes"], timeout=30, live=False)
        (ctx.ok if r["success"] else ctx.err)("Default power plans " + ("restored" if r["success"] else "could not be restored"))

    # ------------------------------------------------------------ diagnostics
    def fx_events(self, ctx):
        ctx.step("Reading critical/error events from the last 3 days (read-only)")
        script = ("$e=Get-WinEvent -FilterHashtable @{LogName='System','Application';Level=1,2;"
                  "StartTime=(Get-Date).AddDays(-3)} -MaxEvents 25 -ErrorAction SilentlyContinue;"
                  "if(-not $e){'No critical or error events found.'}else{"
                  "$e|ForEach-Object{ $_.TimeCreated.ToString('MM-dd HH:mm')+' | '+$_.ProviderName+' | ID '+$_.Id+' | '+"
                  "(($_.Message -split \"`n\")[0]) }}")
        r = ctx.ps(script, timeout=180, live=False)
        for ln in r["stdout"].splitlines():
            if ln.strip():
                ctx.log("   " + ln.strip(), "warn" if "|" in ln else "ok")
        ctx.ok("Report complete. Repeating provider names / IDs point to the failing component.")

    def fx_memdiag(self, ctx):
        try:
            subprocess.Popen(["mdsched.exe"], creationflags=CREATE_NO_WINDOW)
            ctx.ok("Windows Memory Diagnostic opened. Choose 'Restart now' or 'Check next time' there.")
        except Exception as exc:
            ctx.err("Could not open Memory Diagnostic: %s" % exc)

    def fx_restore(self, ctx):
        self.make_restore_point(ctx)


# =============================================================================
#  WINGET MANAGER
# =============================================================================
class WingetManager:
    ROW = re.compile(r"^(?P<name>.+?)\s{2,}(?P<id>[\w.\-+]+)\s+(?P<cur>(?:[<>]\s)?\S+)\s+"
                     r"(?P<avail>\S+)(?:\s+(?P<src>\w+))?\s*$")

    # Fallback when columns are separated by a single space: IDs contain a dot.
    ROW2 = re.compile(r"^(?P<name>.+?)\s+(?P<id>[A-Za-z0-9+\-_]+\.[\w.\-+]+)\s+(?P<cur>(?:[<>]\s)?\S+)\s+"
                      r"(?P<avail>\S+)(?:\s+(?P<src>\w+))?\s*$")

    @staticmethod
    def available():
        return shutil.which("winget") is not None

    def list_updates(self):
        """Returns (items, message). items = [{'name','id','cur','avail'}]."""
        if not self.available():
            return [], "winget was not found. Install 'App Installer' from the Microsoft Store."
        base = ["winget", "upgrade", "--accept-source-agreements"]
        r = run_cmd(base + ["--disable-interactivity"], timeout=240)
        if not r["success"] and re.search(r"invalid|unrecogni|unknown", r["stdout"], re.I):
            r = run_cmd(base, timeout=240)
        text = re.sub(r"[\x00-\x07\x0b-\x1f]", "", r["stdout"])
        lines = [ln.rstrip() for ln in re.split(r"[\r\n]+", text) if ln.strip()]
        items, in_table = [], False
        for ln in lines:
            if re.fullmatch(r"-{5,}\s*", ln.strip()):
                in_table = True
                continue
            if not in_table:
                continue
            if re.search(r"^\d+\s+upgrades?\s+available", ln.strip(), re.I) or \
                    ln.lower().startswith("the following packages"):
                break
            m = self.ROW.match(ln.strip()) or self.ROW2.match(ln.strip())
            if m:
                items.append({"name": m.group("name").strip(), "id": m.group("id"),
                              "cur": m.group("cur"), "avail": m.group("avail")})
        if not items:
            return [], "No app updates are available - everything is up to date."
        return items, "%d update(s) available." % len(items)

    def upgrade_many(self, ctx, items):
        ok = fail = same = 0
        n = len(items)
        for i, it in enumerate(items, 1):
            if ctx.cancelled():
                ctx.warn("Cancelled.")
                break
            ctx.step("[%d/%d] Updating %s  (%s -> %s)" % (i, n, it["name"], it["cur"], it["avail"]))
            ctx.span((i - 1) * 100.0 / n, i * 100.0 / n)
            r = ctx.run(["winget", "upgrade", "--id", it["id"], "--exact", "--silent",
                         "--accept-package-agreements", "--accept-source-agreements",
                         "--disable-interactivity"], timeout=3600)
            code = r["exit_code"]
            if r["success"]:
                ctx.ok("%s updated successfully" % it["name"])
                ok += 1
            elif code in (-1978335189, 0x8A15002B):
                ctx.log("= %s is already up to date" % it["name"], "warn")
                same += 1
            else:
                hexcode = "0x%08X" % (code & 0xFFFFFFFF) if code >= -(2 ** 31) else str(code)
                ctx.err("%s failed (code %s). Close the app if it is open and retry." % (it["name"], hexcode))
                fail += 1
            ctx.progress(i * 100.0 / n)
        ctx.log("SUMMARY: %d updated, %d already current, %d failed" % (ok, same, fail),
                "ok" if not fail else "warn")


# =============================================================================
#  GUI WIDGET HELPERS
# =============================================================================
def make_button(parent, text, bg, fg="#000000", command=None, font=None, padx=14, pady=6):
    hover = {C.BLUE: C.AQUA, C.GREEN: "#7dffa5", C.RED: "#ff8080", C.YELLOW: "#ffe27a", C.AQUA: "#7ff5f5"}.get(bg, bg)
    btn = tk.Button(parent, text=text, bg=bg, fg=fg, activebackground=hover, activeforeground=fg,
                    relief="flat", bd=0, cursor="hand2", font=font or (FONT, 10, "bold"),
                    padx=padx, pady=pady, command=command)
    btn.bind("<Enter>", lambda e: btn.configure(bg=hover))
    btn.bind("<Leave>", lambda e: btn.configure(bg=bg))
    return btn


RISK_STYLE = {Risk.READ_ONLY: (C.AQUA, "READ-ONLY"), Risk.LOW: (C.GREEN, "LOW RISK"), Risk.MEDIUM: (C.YELLOW, "ASK FIRST")}


class ConfirmDialog(tk.Toplevel):
    """Plain-English permission dialog shown before ANY change."""

    def __init__(self, parent, fx, admin_ok=True):
        super().__init__(parent)
        self.result = False
        self.restore = False
        self.title("Permission required - " + fx["title"])
        self.configure(bg=C.BG)
        self.resizable(False, False)
        self.transient(parent)
        col, label = RISK_STYLE.get(fx["risk"], (C.GREEN, "LOW RISK"))

        wrap = tk.Frame(self, bg=C.BG, padx=22, pady=18)
        wrap.pack(fill="both", expand=True)
        top = tk.Frame(wrap, bg=C.BG)
        top.pack(fill="x")
        tk.Label(top, text=fx["title"], bg=C.BG, fg=C.AQUA, font=(FONT, 14, "bold"),
                 wraplength=470, justify="left").pack(side="left", anchor="w")
        tk.Label(top, text=" " + label + " ", bg=col, fg="#000000", font=(FONT, 9, "bold")).pack(side="right", padx=(10, 0))
        tk.Label(wrap, text=fx["short"], bg=C.BG, fg=C.DIM, font=(FONT, 10), wraplength=560,
                 justify="left").pack(anchor="w", pady=(4, 10))

        def section(head, items, color, bullet):
            tk.Label(wrap, text=head, bg=C.BG, fg=color, font=(FONT, 10, "bold")).pack(anchor="w", pady=(8, 2))
            for it in items:
                tk.Label(wrap, text="%s %s" % (bullet, it), bg=C.BG, fg=C.TEXT, font=(FONT, 10),
                         wraplength=560, justify="left").pack(anchor="w", padx=(10, 0), pady=1)

        section("WHAT WILL HAPPEN", fx["does"], C.BLUE_LIGHT, "•")
        section("WHY IT IS SAFE / WHAT STAYS UNTOUCHED", fx["safe"], C.GREEN, "✔")
        extra = ["Estimated time: " + fx["eta"]]
        if fx["reboot"]:
            extra.append("A restart will be needed to finish (you will be asked - nothing restarts by itself).")
        if fx.get("note"):
            extra.append(fx["note"])
        section("GOOD TO KNOW", extra, C.YELLOW, "!")

        self.rp = tk.BooleanVar(value=(fx["risk"] == "medium"))
        if fx["risk"] != "read-only" and fx["key"] not in ("restore", "memdiag"):
            tk.Checkbutton(wrap, text="Create a System Restore point first (recommended safety net)",
                           variable=self.rp, bg=C.BG, fg=C.TEXT, selectcolor=C.PANEL, activebackground=C.BG,
                           activeforeground=C.TEXT, font=(FONT, 10)).pack(anchor="w", pady=(12, 0))

        tk.Label(wrap, text="Do you give permission to run this?", bg=C.BG, fg=C.TEXT,
                 font=(FONT, 11, "bold")).pack(anchor="w", pady=(14, 6))
        row = tk.Frame(wrap, bg=C.BG)
        row.pack(anchor="e", fill="x")
        make_button(row, "✖  No, cancel", C.RED, "#000000", self._no).pack(side="right", padx=(8, 0))
        make_button(row, "✔  Yes, run it", C.GREEN, "#000000", self._yes).pack(side="right")

        self.bind("<Escape>", lambda e: self._no())
        self.protocol("WM_DELETE_WINDOW", self._no)
        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width() - self.winfo_width()) // 2
        y = parent.winfo_rooty() + max(20, (parent.winfo_height() - self.winfo_height()) // 3)
        self.geometry("+%d+%d" % (max(0, x), max(0, y)))
        self.grab_set()
        self.focus_set()
        parent.wait_window(self)

    def _yes(self):
        self.result = True
        self.restore = bool(self.rp.get())
        self.destroy()

    def _no(self):
        self.result = False
        self.destroy()


class WingetWindow(tk.Toplevel):
    """Numbered update table + selection by '1,3,5', '1-4', 'all' or mouse."""

    def __init__(self, app):
        super().__init__(app)
        self.app = app
        self.items = []
        self.title("Winget Pro - App Update Manager")
        self.configure(bg=C.BG)
        self.geometry("920x560")
        self.transient(app)
        tk.Label(self, text="WINGET PRO  -  APP UPDATE MANAGER", bg=C.BG, fg=C.AQUA,
                 font=(FONT, 14, "bold")).pack(anchor="w", padx=16, pady=(14, 0))
        tk.Label(self, text="Pick the apps you want to update. Nothing is uninstalled; only the apps you select are "
                            "updated silently.", bg=C.BG, fg=C.DIM, font=(FONT, 10)).pack(anchor="w", padx=16)
        self.msg = tk.Label(self, text="Loading update list from winget... (can take up to a minute)",
                            bg=C.BG, fg=C.YELLOW, font=(FONT, 10, "bold"))
        self.msg.pack(anchor="w", padx=16, pady=(8, 4))

        frame = tk.Frame(self, bg=C.BG)
        frame.pack(fill="both", expand=True, padx=16)
        cols = ("n", "name", "id", "cur", "avail")
        self.tree = ttk.Treeview(frame, columns=cols, show="headings", selectmode="extended", style="Fixer.Treeview")
        for c, t, w in (("n", "#", 40), ("name", "App name", 300), ("id", "Package ID", 250),
                        ("cur", "Current", 130), ("avail", "Available", 130)):
            self.tree.heading(c, text=t)
            self.tree.column(c, width=w, anchor="w" if c != "n" else "center")
        sb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview, style="Fixer.Vertical.TScrollbar")
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        sel = tk.Frame(self, bg=C.BG)
        sel.pack(fill="x", padx=16, pady=10)
        tk.Label(sel, text="Selection:", bg=C.BG, fg=C.TEXT, font=(FONT, 10, "bold")).pack(side="left")
        self.entry = tk.Entry(sel, bg=C.PANEL, fg=C.TEXT, insertbackground=C.AQUA, relief="flat",
                              font=(MONO, 11), width=26)
        self.entry.pack(side="left", padx=8, ipady=4)
        tk.Label(sel, text='e.g.  1, 3, 5   or   1-4   or   all   (or click rows with Ctrl/Shift)',
                 bg=C.BG, fg=C.DIM, font=(FONT, 9)).pack(side="left")

        btns = tk.Frame(self, bg=C.BG)
        btns.pack(fill="x", padx=16, pady=(0, 14))
        make_button(btns, "Select all", C.BLUE, "#ffffff", self.select_all).pack(side="left")
        make_button(btns, "⟳ Refresh", C.AQUA, "#000000", self.load).pack(side="left", padx=8)
        make_button(btns, "Close", C.RED, "#000000", self.destroy).pack(side="right")
        make_button(btns, "▶  Update selected", C.GREEN, "#000000", self.install).pack(side="right", padx=8)
        self.load()

    def load(self):
        self.msg.configure(text="Loading update list from winget... (can take up to a minute)", fg=C.YELLOW)
        self.tree.delete(*self.tree.get_children())
        self._res = {}

        def work():
            try:
                self._res["data"] = WingetManager().list_updates()
            except Exception as exc:
                self._res["data"] = ([], "Could not read winget output: %s" % exc)

        threading.Thread(target=work, daemon=True).start()
        self.after(300, self._poll)

    def _poll(self):
        if not self.winfo_exists():
            return
        if "data" not in self._res:
            self.after(300, self._poll)
            return
        self.items, message = self._res["data"]
        for i, it in enumerate(self.items, 1):
            self.tree.insert("", "end", iid=str(i), values=(i, it["name"], it["id"], it["cur"], it["avail"]))
        self.msg.configure(text=message, fg=C.GREEN if self.items else C.YELLOW)

    def select_all(self):
        self.tree.selection_set(self.tree.get_children())

    def install(self):
        if not self.items:
            messagebox.showinfo(APP_NAME, "There are no updates to install.", parent=self)
            return
        spec = self.entry.get().strip()
        if spec:
            idx, bad = parse_selection(spec, len(self.items))
            if bad:
                messagebox.showwarning(APP_NAME, "Invalid selection: %s\nUse numbers 1-%d, ranges like 1-4, or 'all'."
                                       % (", ".join(bad), len(self.items)), parent=self)
                return
        else:
            idx = sorted(int(i) for i in self.tree.selection())
        if not idx:
            messagebox.showinfo(APP_NAME, "Nothing selected. Click rows or type e.g. 1,3 or all.", parent=self)
            return
        chosen = [self.items[i - 1] for i in idx]
        listing = "\n".join("  %d. %s  (%s -> %s)" % (i, self.items[i - 1]["name"], self.items[i - 1]["cur"],
                                                      self.items[i - 1]["avail"]) for i in idx[:15])
        if len(idx) > 15:
            listing += "\n  ...and %d more" % (len(idx) - 15)
        if not messagebox.askyesno(APP_NAME, "Update these %d app(s)?\n\n%s\n\nThey will be updated silently. "
                                   "Nothing will be uninstalled. Close them first if they are open." % (len(chosen), listing),
                                   parent=self):
            return
        self.destroy()
        self.app.start_task("Winget updates", lambda ctx: WingetManager().upgrade_many(ctx, chosen), restore=False)


# =============================================================================
#  MAIN APPLICATION
# =============================================================================
class FancyProgress(tk.Canvas):
    """
    Animated canvas progress bar:
      * eased (smooth) movement, 3-colour gradient that fills with progress
      * moving diagonal stripes + glossy highlight + glowing leading edge
      * 'scanner' animation when progress is unknown (indeterminate)
      * step ticks for multi-step pipelines, live percent, elapsed time & ETA
      * final state colours: green (done), yellow (cancelled)
    """
    STOPS = {"run": [_rgb(C.BLUE), _rgb(C.AQUA), _rgb(C.GREEN)],
             "idle": [_rgb(C.BLUE), _rgb(C.AQUA), _rgb(C.GREEN)],
             "ok": [_rgb("#14a84a"), _rgb(C.GREEN), _rgb("#a6ffc4")],
             "cancel": [_rgb("#b45309"), _rgb(C.YELLOW), _rgb("#fff0a8")]}
    SPIN = "◐◓◑◒"

    def __init__(self, parent, height=96):
        super().__init__(parent, height=height, bg=C.BG, highlightthickness=0)
        self.target = 0.0
        self.disp = 0.0
        self.indet = False
        self.running = False
        self.state = "idle"
        self.phase = "Ready - choose a fix to begin"
        self.detail = ""
        self.t0 = 0.0
        self.t_end = 0.0
        self.ticks = 0
        self.frame = 0
        self._dirty = True
        self.bind("<Configure>", lambda e: self._draw())
        self.after(50, self._anim)

    # ----- public API
    def start(self, title):
        self.running, self.state, self.indet = True, "run", True
        self.target = self.disp = 0.0
        self.phase, self.detail = title, "Starting..."
        self.t0, self.ticks, self._dirty = time.time(), 0, True

    def set_target(self, pct):
        self.indet = False
        self.target = max(0.0, min(100.0, float(pct)))
        self._dirty = True

    def set_indeterminate(self):
        self.indet = True
        self._dirty = True

    def set_phase(self, text):
        self.phase = text[:90]
        self._dirty = True

    def set_detail(self, text):
        self.detail = text[:90]
        self._dirty = True

    def set_ticks(self, n):
        self.ticks = n if n > 1 else 0
        self._dirty = True

    def finish(self, state="ok"):
        self.running, self.indet, self.state = False, False, state
        self.t_end = time.time()
        if state == "ok":
            self.target = 100.0
            self.phase = "Completed"
        else:
            self.phase = "Cancelled"
        self._dirty = True

    # ----- animation loop
    def _anim(self):
        try:
            self.frame += 1
            diff = self.target - self.disp
            if abs(diff) > 0.05:
                self.disp += diff * 0.14
            else:
                self.disp = self.target
            if self.running or abs(diff) > 0.05 or self._dirty:
                self._dirty = False
                self._draw()
            delay = 33
        except Exception:
            delay = 100
        self.after(delay, self._anim)

    def _draw(self):
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40:
            return
        self.delete("all")
        col = {"run": C.AQUA, "ok": C.GREEN, "cancel": C.YELLOW, "idle": C.DIM}[self.state]
        spin = self.SPIN[(self.frame // 4) % 4] if self.running else {"ok": "✔", "cancel": "■"}.get(self.state, "●")
        pct = self.disp
        # ---- header rows
        self.create_text(4, 16, anchor="w", text="%s  %s" % (spin, self.phase), fill=col, font=(FONT, 11, "bold"))
        self.create_text(w - 4, 20, anchor="e", text="···" if (self.indet and self.running) else "%d%%" % round(pct),
                         fill=C.TEXT, font=(FONT, 22, "bold"))
        self.create_text(4, 46, anchor="w", text=self.detail, fill=C.DIM, font=(MONO, 9))
        right = ""
        if self.t0:
            end = time.time() if self.running else (self.t_end or time.time())
            elapsed = end - self.t0
            right = "elapsed " + fmt_dur(elapsed)
            if self.running and not self.indet and pct >= 3 and elapsed > 2:
                right += "   ·   ETA " + fmt_dur(elapsed * (100 - pct) / pct)
        self.create_text(w - 4, 48, anchor="e", text=right, fill=C.DIM, font=(FONT, 9))
        # ---- bar
        y0, y1 = h - 30, h - 8
        x0, x1 = 6, w - 6
        span = float(x1 - x0)
        self.create_rectangle(x0 - 3, y0 - 3, x1 + 3, y1 + 3, fill="#0d0f12", outline=C.BORDER)
        stops = self.STOPS[self.state]
        if self.indet and self.running:                       # scanner glow
            seg = span * 0.30
            ph = (self.frame * 0.018) % 2.0
            sx = x0 + (span - seg) * (ph if ph <= 1 else 2 - ph)
            x, dark = sx, _rgb("#0d0f12")
            while x < sx + seg:
                t = 1 - abs(2 * (x - sx) / seg - 1)
                self.create_rectangle(x, y0, min(x + 5, sx + seg), y1, outline="",
                                      fill=_lerp(dark, _rgb(C.AQUA), t ** 1.4))
                x += 5
        else:
            fw = span * pct / 100.0
            if fw >= 1:
                x = float(x0)
                while x < x0 + fw:
                    self.create_rectangle(x, y0, min(x + 6, x0 + fw), y1, outline="",
                                          fill=_grad(stops, (x - x0) / span))
                    x += 6
                if self.running:                              # moving stripes
                    sx = x0 - 28 + (self.frame * 1.6) % 28
                    skew = (y1 - y0) * 0.6
                    while sx + 8 + skew <= x0 + fw:
                        if sx >= x0:
                            c = _lerp(_rgb(_grad(stops, (sx - x0) / span)), (255, 255, 255), 0.22)
                            self.create_polygon(sx, y1, sx + 8, y1, sx + 8 + skew, y0, sx + skew, y0, fill=c, outline="")
                        sx += 28
                gloss = _lerp(_rgb(_grad(stops, fw / span)), (255, 255, 255), 0.5)
                self.create_line(x0 + 2, y0 + 2, x0 + fw - 2, y0 + 2, fill=gloss)
                if fw > 6:                                    # glowing leading edge
                    self.create_rectangle(x0 + fw - 7, y0, x0 + fw - 4, y1, outline="",
                                          fill=_lerp(_rgb(_grad(stops, fw / span)), (255, 255, 255), 0.45))
                    self.create_rectangle(x0 + fw - 3, y0, x0 + fw, y1, outline="", fill="#ffffff")
        # ---- step ticks for pipelines
        for i in range(1, self.ticks):
            tx = x0 + span * i / self.ticks
            self.create_line(tx, y0 - 3, tx, y1 + 3, fill=C.BG, width=3)
            self.create_line(tx, y1 + 3, tx, y1 + 7, fill=C.DIM)


class Tile(tk.Frame):
    """Telemetry tile: caption + value, with optional usage bar or live sparkline."""

    def __init__(self, parent, caption, width=170, bar=False, spark=False):
        super().__init__(parent, bg=C.PANEL, highlightbackground=C.BORDER, highlightthickness=1, width=width, height=78)
        self.pack_propagate(False)
        tk.Label(self, text=caption, bg=C.PANEL, fg=C.DIM, font=(FONT, 8, "bold")).pack(anchor="w", padx=10, pady=(7, 0))
        self.value = tk.Label(self, text="...", bg=C.PANEL, fg=C.AQUA, font=(FONT, 11, "bold"), anchor="w",
                              justify="left", wraplength=width - 18)
        self.value.pack(anchor="w", padx=10)
        self.spark = spark
        self.hist = deque(maxlen=48)
        self.canvas = None
        if bar or spark:
            self.canvas = tk.Canvas(self, height=18 if spark else 6, bg=C.PANEL if spark else "#26292e",
                                    highlightthickness=0)
            self.canvas.pack(fill="x", padx=10, pady=(3, 0))

    def set(self, text, color=C.AQUA, pct=None):
        self.value.configure(text=text, fg=color)
        if self.canvas is None or pct is None:
            return
        c = self.canvas
        w, h = max(4, c.winfo_width()), int(c["height"])
        c.delete("all")
        pct = max(0.0, min(100.0, pct))
        if not self.spark:
            c.create_rectangle(0, 0, w * pct / 100.0, h, fill=color, width=0)
            return
        self.hist.append(pct)
        n = len(self.hist)
        if n < 2:
            return
        step = w / float(self.hist.maxlen - 1)
        off = w - (n - 1) * step
        pts = []
        for i, v in enumerate(self.hist):
            pts += [off + i * step, h - 1 - (h - 3) * v / 100.0]
        c.create_polygon([off, h] + pts + [off + (n - 1) * step, h], fill=_lerp(_rgb(C.PANEL), _rgb(color), 0.35), outline="")
        c.create_line(pts, fill=color, width=2, smooth=True)



class App(tk.Tk):
    def __init__(self, admin):
        super().__init__()
        self.admin = admin
        self.title("%s %s  -  All-in-One Windows Repair & Manager" % (APP_NAME, APP_VERSION))
        self.geometry("1360x860")
        self.minsize(1120, 700)
        self.configure(bg=C.BG)
        self.cleaner = SafeCleaner()
        self.tele = SystemTelemetry()
        self.repair = WinRepair(self.cleaner)
        self.q = queue.Queue()
        self.busy = False
        self.history: List[TaskRecord] = []
        self.slow = {"smart": ("Checking...", []), "temp": None}
        self._style()
        self._build_header()
        self._build_body()
        self._build_footer()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.after(100, self._pump)
        self.after(300, self._tick)
        self.refresh_slow()
        self.welcome()

    # ---------------------------------------------------------------- style
    def _style(self):
        st = ttk.Style(self)
        try:
            st.theme_use("clam")
        except Exception:
            pass
        st.configure("Fixer.Horizontal.TProgressbar", troughcolor="#202328", background=C.AQUA,
                     bordercolor=C.BG, lightcolor=C.AQUA, darkcolor=C.AQUA, thickness=14)
        st.configure("Fixer.Treeview", background=C.PANEL, fieldbackground=C.PANEL, foreground=C.TEXT,
                     rowheight=26, bordercolor=C.BORDER, font=(FONT, 10))
        st.configure("Fixer.Treeview.Heading", background="#1d2025", foreground=C.AQUA, font=(FONT, 10, "bold"),
                     relief="flat")
        st.map("Fixer.Treeview", background=[("selected", C.BLUE)], foreground=[("selected", "#ffffff")])
        st.configure("Fixer.Vertical.TScrollbar", background="#2a2f36", troughcolor=C.BG, bordercolor=C.BG,
                     arrowcolor=C.AQUA)

    # ---------------------------------------------------------------- header
    def _build_header(self):
        head = tk.Frame(self, bg=C.BG)
        head.pack(fill="x", padx=16, pady=(12, 4))
        tk.Label(head, text="◉ WINDOWS FIXER PRO", bg=C.BG, fg=C.AQUA, font=(FONT, 20, "bold")).pack(side="left")
        tk.Label(head, text="   non-destructive • asks before every change • no installs needed", bg=C.BG, fg=C.DIM,
                 font=(FONT, 10)).pack(side="left", pady=(8, 0))
        badge_txt, badge_col = ("  ADMINISTRATOR  ", C.GREEN) if self.admin else ("  VIEW-ONLY (not admin)  ", C.RED)
        tk.Label(head, text=badge_txt, bg=badge_col, fg="#000000", font=(FONT, 9, "bold")).pack(side="right", pady=6)
        make_button(head, "⟳ Refresh", C.BLUE, "#ffffff", self.refresh_all, padx=10, pady=3).pack(side="right", padx=10)

        row = tk.Frame(self, bg=C.BG)
        row.pack(fill="x", padx=16, pady=4)
        self.t_os = Tile(row, "OS VERSION", 230)
        self.t_up = Tile(row, "SYSTEM UPTIME", 150)
        self.t_cpu = Tile(row, "CPU USAGE", 150, spark=True)
        self.t_ram = Tile(row, "RAM USAGE", 180, spark=True)
        self.t_disk = Tile(row, "DRIVE C: FREE", 170, bar=True)
        self.t_smart = Tile(row, "DISK SMART", 150)
        self.t_temp = Tile(row, "TEMP JUNK", 120)
        for t in (self.t_os, self.t_up, self.t_cpu, self.t_ram, self.t_disk, self.t_smart, self.t_temp):
            t.pack(side="left", padx=(0, 8), fill="x", expand=True)
        self.t_os.set(self.tele.os_name() + ("\nAdmin: YES" if self.admin else "\nAdmin: NO"),
                      C.GREEN if self.admin else C.RED)
        self.reco = tk.Label(self, text="", bg=C.BG, fg=C.YELLOW, font=(FONT, 10, "bold"), anchor="w", justify="left")
        self.reco.pack(fill="x", padx=18, pady=(2, 4))

    # ---------------------------------------------------------------- body
    def _build_body(self):
        body = tk.Frame(self, bg=C.BG)
        body.pack(fill="both", expand=True, padx=16, pady=4)
        left = tk.Frame(body, bg=C.BG, width=600)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        prow = tk.Frame(left, bg=C.BG)
        prow.pack(side="top", fill="x", pady=(0, 6))
        for pl in PIPELINES:
            make_button(prow, pl.title, pl.color, "#000000", lambda p=pl: self.on_pipeline(p), pady=9).pack(
                side="left", fill="x", expand=True, padx=(0, 6))
        right = tk.Frame(body, bg=C.BG)
        right.pack(side="left", fill="both", expand=True, padx=(12, 0))

        # scrollable card list
        self.canvas = tk.Canvas(left, bg=C.BG, highlightthickness=0)
        sb = ttk.Scrollbar(left, orient="vertical", command=self.canvas.yview, style="Fixer.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=C.BG)
        win = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(win, width=e.width))
        self.canvas.bind("<Enter>", lambda e: self.canvas.bind_all("<MouseWheel>", self._wheel))
        self.canvas.bind("<Leave>", lambda e: self.canvas.unbind_all("<MouseWheel>"))
        cat = None
        for fx in FIXES:
            if fx["cat"] != cat:
                cat = fx["cat"]
                tk.Label(self.inner, text="▌" + cat.value, bg=C.BG, fg=C.BLUE_LIGHT, font=(FONT, 10, "bold")).pack(
                    anchor="w", pady=(12, 4))
            self._card(fx)

        # console
        tk.Label(right, text="◉ LIVE CONSOLE", bg=C.BG, fg=C.AQUA, font=(FONT, 10, "bold")).pack(anchor="w")
        box = tk.Frame(right, bg=C.BORDER)
        box.pack(fill="both", expand=True, pady=(4, 6))
        self.console = tk.Text(box, bg="#050505", fg=C.TEXT, insertbackground=C.AQUA, relief="flat",
                               font=(MONO, 10), wrap="word", padx=10, pady=8, state="disabled")
        csb = ttk.Scrollbar(box, orient="vertical", command=self.console.yview, style="Fixer.Vertical.TScrollbar")
        self.console.configure(yscrollcommand=csb.set)
        csb.pack(side="right", fill="y")
        self.console.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        for tag, color, extra in (("info", C.AQUA, ""), ("ok", C.GREEN, ""), ("warn", C.YELLOW, ""), ("err", C.RED, ""),
                                  ("step", C.BLUE_LIGHT, "bold"), ("dim", "#9aa5b1", ""), ("time", "#5b6570", ""),
                                  ("head", C.AQUA, "bold")):
            self.console.tag_configure(tag, foreground=color, font=(MONO, 10, "bold") if extra else (MONO, 10))

        self.bar = FancyProgress(right)
        self.bar.pack(fill="x", pady=(2, 8))
        brow = tk.Frame(right, bg=C.BG)
        brow.pack(fill="x")
        self.btn_cancel = make_button(brow, "■ CANCEL RUNNING TASK", C.RED, "#000000", self.cancel_task)
        self.btn_cancel.pack(side="left")
        make_button(brow, "Save log", C.BLUE, "#ffffff", self.save_log).pack(side="right")
        make_button(brow, "Clear", C.YELLOW, "#000000", self.clear_log).pack(side="right", padx=8)

    def _card(self, fx):
        col, label = RISK_STYLE.get(fx["risk"], (C.GREEN, "LOW RISK"))
        f = tk.Frame(self.inner, bg=C.CARD, highlightbackground=C.BORDER, highlightthickness=1)
        f.pack(fill="x", pady=3, padx=(0, 6))
        left = tk.Frame(f, bg=C.CARD)
        left.pack(side="left", fill="both", expand=True, padx=10, pady=8)
        tk.Label(left, text=fx["title"], bg=C.CARD, fg=C.AQUA, font=(FONT, 10, "bold"), anchor="w",
                 wraplength=370, justify="left").pack(anchor="w")
        tk.Label(left, text=fx["short"], bg=C.CARD, fg=C.DIM, font=(FONT, 9), anchor="w", wraplength=370,
                 justify="left").pack(anchor="w", pady=(2, 0))
        right = tk.Frame(f, bg=C.CARD)
        right.pack(side="right", padx=10, pady=8)
        tk.Label(right, text=" %s " % label, bg=col, fg="#000000", font=(FONT, 8, "bold")).pack(anchor="e")
        make_button(right, "▶ Run", C.BLUE, "#ffffff", lambda x=fx: self.on_run(x), padx=18, pady=4).pack(pady=(6, 0))

    def _wheel(self, e):
        try:
            self.canvas.yview_scroll(int(-1 * (e.delta / 120)), "units")
        except Exception:
            pass

    def _build_footer(self):
        foot = tk.Frame(self, bg=C.BG)
        foot.pack(fill="x", padx=18, pady=(2, 8))
        tk.Label(foot, text="Safety: never deletes personal files, never formats drives, never edits the registry. "
                            "Deletions are limited to a built-in whitelist of temp/cache folders.",
                 bg=C.BG, fg="#6d7783", font=(FONT, 8)).pack(side="left")
        self.session_lbl = tk.Label(foot, text="Session: 0 task(s) completed", bg=C.BG, fg=C.BLUE_LIGHT,
                                    font=(FONT, 8, "bold"))
        self.session_lbl.pack(side="right")

    # ---------------------------------------------------------------- console
    def log(self, text, tag="info"):
        try:
            self.console.configure(state="normal")
            if tag not in ("head",):
                self.console.insert("end", datetime.datetime.now().strftime("[%H:%M:%S] "), "time")
            self.console.insert("end", text + "\n", tag)
            self.console.see("end")
            self.console.configure(state="disabled")
        except Exception:
            pass

    def clear_log(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")

    def save_log(self):
        try:
            path = filedialog.asksaveasfilename(defaultextension=".txt", initialfile="WindowsFixerPro_log.txt",
                                                filetypes=[("Text file", "*.txt")])
            if path:
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(self.console.get("1.0", "end"))
                self.log("Log saved to %s" % path, "ok")
        except Exception as exc:
            self.log("Could not save log: %s" % exc, "err")

    def welcome(self):
        self.log("Welcome to %s %s" % (APP_NAME, APP_VERSION), "head")
        self.log("Pick any fix on the left. You will always get a plain-English explanation and be asked for "
                 "permission first.", "info")
        if not self.admin:
            self.log("You are NOT running as Administrator - fixes are disabled. Restart the program and accept the "
                     "Windows UAC prompt.", "err")
        self.log("Tip: try the ⚡ Quick Tune-Up first. For deeper problems use ⚙ Deep Repair (DISM + SFC), then restart.", "warn")

    # ---------------------------------------------------------------- telemetry
    def _tick(self):
        try:
            cpu = self.tele.cpu_percent()
            self.t_cpu.set("%.0f %%" % cpu, C.GREEN if cpu < 60 else C.YELLOW if cpu < 85 else C.RED, cpu)
            pct, used, total = self.tele.ram()
            self.t_ram.set("%.0f %%  (%s / %s)" % (pct, fmt_bytes(used), fmt_bytes(total)),
                           C.GREEN if pct < 70 else C.YELLOW if pct < 88 else C.RED, pct)
            up = self.tele.uptime_seconds()
            long_up = up > 3 * 86400
            self.t_up.set(fmt_uptime(up) + ("\n⚠ Restart advised" if long_up else ""), C.YELLOW if long_up else C.GREEN)
            free, tot = self.tele.disk_c()
            if tot:
                used_pct = (1 - free / float(tot)) * 100
                fp = free * 100.0 / tot
                self.t_disk.set("%s  (%.0f %%)" % (fmt_bytes(free), fp),
                                C.GREEN if fp > 20 else C.YELLOW if fp > 10 else C.RED, used_pct)
            self._reco(up, free, tot)
        except Exception:
            pass
        self.after(2000, self._tick)

    def refresh_slow(self):
        def work():
            try:
                smart = SystemTelemetry.smart_details()
            except Exception:
                smart = ("Unknown", [])
            try:
                temp = sum(self.cleaner.size(r["id"]) for r in self.cleaner.group("temp"))
            except Exception:
                temp = None
            self.q.put(("tele", smart, temp))

        threading.Thread(target=work, daemon=True).start()

    def refresh_all(self):
        self.t_smart.set("Checking...", C.DIM)
        self.t_temp.set("Calculating...", C.DIM)
        self.refresh_slow()

    def _apply_slow(self, smart, temp):
        self.slow["smart"], self.slow["temp"] = smart, temp
        overall = smart[0]
        self.t_smart.set({"OK": "OK ✔", "WARNING": "WARNING ✖"}.get(overall, "Unknown"),
                         {"OK": C.GREEN, "WARNING": C.RED}.get(overall, C.YELLOW))
        if temp is None:
            self.t_temp.set("n/a", C.DIM)
        else:
            mb = temp / 1048576.0
            self.t_temp.set("%.0f MB" % mb, C.GREEN if mb < 300 else C.YELLOW if mb < 1500 else C.RED)

    def _reco(self, up, free, tot):
        msgs = []
        if self.slow["smart"][0] == "WARNING":
            msgs.append("✖ A drive reports a health problem - back up your data now!")
        if tot and free * 100.0 / tot < 10:
            msgs.append("⚠ Low free space on C: - run Deep Cleaner / Component Cleanup.")
        if self.slow["temp"] and self.slow["temp"] > 500 * 1048576:
            msgs.append("⚠ %.0f MB of temp junk - run the Deep Temp Cleaner." % (self.slow["temp"] / 1048576.0))
        if up > 3 * 86400:
            msgs.append("⚠ PC has run %.0f days without a restart - a restart fixes many problems." % (up / 86400))
        self.reco.configure(text="  ".join(msgs) if msgs else "✔ No issues detected. Your system looks healthy.",
                            fg=C.YELLOW if msgs else C.GREEN)

    # ---------------------------------------------------------------- tasks
    def on_run(self, fx):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Another task is running. Wait for it to finish or press CANCEL.")
            return
        if fx["key"] == "winget":
            if not self.admin:
                pass  # winget works for the user too; no admin needed to list/update most apps
            WingetWindow(self)
            return
        if not self.admin:
            messagebox.showwarning(APP_NAME, "Administrator rights are required for this fix.\n\n"
                                   "Close the program and start it again, then accept the Windows UAC prompt.")
            return
        dlg = ConfirmDialog(self, fx)
        if not dlg.result:
            self.log("Cancelled by you: %s (nothing was changed)" % fx["title"], "warn")
            return
        self.start_task(fx["title"], lambda ctx: self.repair.run(fx, ctx), restore=dlg.restore)

    def on_pipeline(self, pl):
        if self.busy:
            messagebox.showinfo(APP_NAME, "Another task is running. Wait for it to finish or press CANCEL.")
            return
        if not self.admin:
            messagebox.showwarning(APP_NAME, "Administrator rights are required.\n\nClose the program and start it "
                                   "again, then accept the Windows UAC prompt.")
            return
        dlg = ConfirmDialog(self, combine(pl))
        if not dlg.result:
            self.log("Cancelled by you: %s (nothing was changed)" % pl.title, "warn")
            return
        fixes = [FIX_BY_KEY[k] for k in pl.fix_keys]

        def job(ctx):
            ctx.plan(len(fixes))
            self.repair.run_many(fixes, ctx)

        self.start_task(pl.title, job, restore=dlg.restore)

    def _update_session(self):
        done = sum(1 for r in self.history if not r.cancelled)
        total = sum(r.seconds for r in self.history)
        self.session_lbl.configure(text="Session: %d task(s) completed · %s total" % (done, fmt_dur(total)))

    def start_task(self, title, func, restore=False):
        if self.busy:
            return
        self.busy = True
        CANCEL.clear()
        self.bar.start(title)
        self.log("═" * 14 + "  " + title.upper() + "  " + "═" * 14, "head")
        q = self.q

        def worker():
            ctx = TaskContext(q)
            t0 = time.time()
            try:
                if restore:
                    self.repair.make_restore_point(ctx)
                func(ctx)
            except Exception as exc:
                ctx.err("Unexpected error handled safely: %s" % exc)
            finally:
                q.put(("done", title, time.time() - t0))

        threading.Thread(target=worker, daemon=True).start()

    def cancel_task(self):
        if self.busy:
            CANCEL.set()
            kill_current()
            self.log("Cancel requested - stopping safely...", "warn")

    def _pump(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == "log":
                    self.log(msg[1], msg[2])
                elif kind == "progress":
                    if self.busy:
                        if msg[1] is None:
                            self.bar.set_indeterminate()
                        else:
                            self.bar.set_target(msg[1])
                elif kind == "phase":
                    self.bar.set_phase(msg[1])
                elif kind == "plan":
                    self.bar.set_ticks(msg[1])
                elif kind == "status":
                    self.bar.set_detail(msg[1])
                elif kind == "tele":
                    self._apply_slow(msg[1], msg[2])
                elif kind == "refresh":
                    self.refresh_slow()
                elif kind == "offer_reboot":
                    self._offer_reboot()
                elif kind == "done":
                    self.busy = False
                    cancelled = CANCEL.is_set()
                    self.bar.finish("cancel" if cancelled else "ok")
                    self.bar.set_detail("Finished in %s" % fmt_dur(msg[2]))
                    self.history.append(TaskRecord(msg[1], msg[2], cancelled))
                    self._update_session()
                    self.log("%s: %s  (%s)" % ("Cancelled" if cancelled else "Finished", msg[1], fmt_dur(msg[2])),
                             "warn" if cancelled else "ok")
                    self.refresh_slow()
        except queue.Empty:
            pass
        except Exception:
            pass
        self.after(100, self._pump)

    def _offer_reboot(self):
        if messagebox.askyesno(APP_NAME, "A restart is needed to finish this fix.\n\nSave your work first.\n"
                               "Restart in 60 seconds?\n(You can cancel it by running  shutdown /a )"):
            run_cmd(["shutdown", "/r", "/t", "60", "/c", "Restart requested by Windows Fixer Pro"], timeout=15)
            self.log("Restart scheduled in 60 s. To cancel: open Command Prompt and type  shutdown /a", "warn")
        else:
            self.log("Please restart your PC later to finish the fix.", "warn")

    def on_close(self):
        if self.busy:
            if not messagebox.askyesno(APP_NAME, "A task is still running. Stop it and exit?"):
                return
            CANCEL.set()
            kill_current()
        self.destroy()


# =============================================================================
#  ELEVATION + ENTRY POINT
# =============================================================================
def elevate():
    """Relaunch with UAC prompt. Returns True when the elevated copy was started."""
    try:
        exe = sys.executable
        pyw = os.path.join(os.path.dirname(exe), "pythonw.exe")
        if os.path.exists(pyw):
            exe = pyw                           # no console window
        script = os.path.abspath(sys.argv[0])
        params = " ".join('"%s"' % a for a in [script] + sys.argv[1:])
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        return rc > 32
    except Exception:
        return False


def main():
    if os.name != "nt":
        print("%s runs on Windows only." % APP_NAME)
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    admin = SystemTelemetry.is_admin()
    if not admin:
        if elevate():
            return
        root = tk.Tk()
        root.withdraw()
        cont = messagebox.askyesno(APP_NAME, "Administrator permission was not granted.\n\nOpen in VIEW-ONLY mode "
                                   "(live system information only, fixes disabled)?")
        root.destroy()
        if not cont:
            return
    try:
        app = App(admin)
        app.mainloop()
    except KeyboardInterrupt:
        try:
            CANCEL.set()
            kill_current()
        except Exception:
            pass
    except Exception as exc:
        try:
            messagebox.showerror(APP_NAME, "The program stopped safely:\n%s" % exc)
        except Exception:
            pass


if __name__ == "__main__":
    main()

"""All windows: main window, connection cards, dialogs, theme and tray."""

from . import update, winutil
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from .config import (
    APP_NAME,
    APP_VERSION,
    HUNG_RESTART_SECS,
    LOG_PATH,
    RECONNECT_ARG,
    STARTUP_ARG,
    THEMES,
    _startup_command,
    _version_tuple,
    build_info,
    file_log,
    get_start_with_windows,
    latest_release,
    load_connections,
    load_settings,
    new_connection,
    save_connections,
    save_settings,
    set_start_with_windows,
    setup_file_log,
)
from .mounts import (
    MOUNT_FORMAT,
    MOUNT_OPTIONS,
    _find_sshfs_pids_for_drive,
    connect,
    disconnect,
    foreign_drive,
    install_requirement,
    missing_requirements,
    mount_format,
    mount_option,
)
from .ssh import (
    ASKPASS_ENV,
    DB_PORTS,
    SSHProbe,
    TUNNEL_PRESETS,
    TunnelManager,
    _conn_label,
    _saved_password_already_tried,
    _write_stdout,
    describe_tunnel,
    open_ssh_terminal,
    parse_jump,
    setup_key_login,
)
from .tasks import (
    COPY_MODES,
    DAYS,
    TRIGGERS as TASK_TRIGGERS,
    TYPES as TASK_TYPES,
    describe_task,
    describe_when,
    drive_of,
    load_tasks,
    new_task,
    next_run,
    run_command,
    run_copy,
    run_program,
    save_tasks,
    valid_time,
)
from .winutil import (
    _k32,
    drive_space,
    human_size,
    cpu_ms,
    letter_present,
    ping_ms,
    probe_drive,
)

# Declared here so editors/linters know the names; real values come from apply_theme.
BG = BG_CARD = BG_INPUT = BG_HEAD = FG = FG_MUTED = BORDER = ""

ACCENT = ACCENT_H = SSH_BG = SSH_H = SUCCESS = SUCCESS_H = DANGER = DANGER_H = WARNING = WARNING_H = ""

EXIT_BG = EXIT_H = TRAY_BG = TRAY_H = BANNER_BG = BANNER_FG = LOG_BG = LOG_FG = ""

_THEMED = []  # the other ui_* modules; they get the same colours

def apply_theme(name):
    colours = THEMES.get(name, THEMES["dark"])
    globals().update(colours)
    for m in _THEMED:
        vars(m).update(colours)

apply_theme("dark")

FONT       = ("Segoe UI", 10)

FONT_BOLD  = ("Segoe UI", 10, "bold")

FONT_LARGE = ("Segoe UI", 12, "bold")

FONT_SMALL = ("Segoe UI", 9)

FONT_MONO  = ("Consolas", 9)

def _prompt_names_key(prompt, key_path):
    """Is this "Enter passphrase for key '<path>':" prompt about key_path?
    OpenSSH cuts the path in the prompt after 100 characters, so a long path
    only shows its beginning."""
    shown = prompt[prompt.find("'") + 1:prompt.rfind("'")] if prompt.count("'") >= 2 else ""
    norm = lambda p: p.replace("\\", "/").lower()
    shown, key = norm(shown), norm(key_path)
    if not shown:
        return False
    return (key == shown or shown.endswith("/" + key.rsplit("/", 1)[-1])
            or (len(shown) >= 100 and key.startswith(shown)))

def _askpass_main(prompt):
    """Answer one ssh prompt (ssh runs this app as SSH_ASKPASS):
    - host-key "(yes/no)?" questions -> Trust / Cancel popup
    - password prompts -> the saved password (once per ssh process)
    - key passphrase prompts -> the saved passphrase (once per ssh process)
    - anything else (2FA codes, Ask Each Time) -> input popup
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
    elif ("passphrase for key" in low and conn.get("auth_type") == "key"
          and conn.get("passphrase") and conn.get("key_path")
          and _prompt_names_key(prompt, conn["key_path"])
          and not _saved_password_already_tried()):
        # Only respond to OpenSSH prompts naming the configured private key.
        # Never send a stored passphrase to an arbitrary server/2FA prompt.
        return _write_stdout(conn["passphrase"])
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

# The logo comes from icon.ico (sizes 16 to 256), which the exe carries next
# to version.json.
_ICON_PATH = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent)) / "icon.ico"

def _make_logo(size: int = 64):
    """The app logo as a size x size PIL Image (None if PIL is unavailable).
    Uses the nearest icon.ico size at or above `size`; without icon.ico a
    simple K is drawn instead."""
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        ico = Image.open(_ICON_PATH)
        sizes = sorted(s[0] for s in ico.info["sizes"])
        best = next((s for s in sizes if s >= size), sizes[-1])
        img = ico.ico.getimage((best, best)).convert("RGBA")
    except Exception:
        ss = 8  # ImageDraw doesn't anti-alias: draw 8x larger, then shrink
        img = _draw_logo(size, ss)
    return img if img.size == (size, size) else img.resize((size, size), Image.LANCZOS)

LOGO_BLUE = "#4C9EEB"  # brand colour, same in both themes

LOGO_BG   = "#22262F"

_tiles = {}

def _tile_image(size, fill, outline, radius=12):
    """Rounded drive tile for a card. Drawn 8x larger and scaled down so the
    corners are smooth (Tk's canvas draws them jagged)."""
    key = (size, fill, outline, radius)
    if key not in _tiles:
        from PIL import Image, ImageDraw, ImageTk
        ss = 8
        S = size * ss
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle(
            (ss // 2, ss // 2, S - 1 - ss // 2, S - 1 - ss // 2), radius=radius * ss,
            fill=fill, outline=outline, width=round(1.5 * ss))
        _tiles[key] = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
    return _tiles[key]

def _usage_color(used):
    """Green while there's plenty of room, through yellow, to red when nearly full."""
    f = min(1.0, max(0.0, (used - 0.5) / 0.45))  # 50% used or less is green, 95% is red
    return _blend(SUCCESS, WARNING, f * 2) if f < 0.5 else _blend(WARNING, DANGER, f * 2 - 1)

def _bar_image(width, height, used, color, track):
    """Rounded usage bar, drawn large and scaled down for smooth ends."""
    from PIL import Image, ImageDraw, ImageTk
    ss = 8
    W, H = width * ss, height * ss
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, W - 1, H - 1), radius=H // 2, fill=track)
    fill = round(W * min(1.0, max(0.0, used)))
    if fill:
        d.rounded_rectangle((0, 0, max(fill, H) - 1, H - 1), radius=H // 2, fill=color)
    return ImageTk.PhotoImage(img.resize((width, height), Image.LANCZOS))

def _tank_image(size, fill, outline, used, color, radius=12):
    """Drive tile that fills up from the bottom like a tank: the level is how
    full the drive is, in the usage colour."""
    level = round(min(1.0, max(0.0, used)) * 100)
    key = ("tank", size, fill, outline, level, color, radius)
    if key not in _tiles:
        from PIL import Image, ImageChops, ImageDraw, ImageTk
        ss = 8
        S = size * ss
        box = (ss // 2, ss // 2, S - 1 - ss // 2, S - 1 - ss // 2)
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        ImageDraw.Draw(img).rounded_rectangle(box, radius=radius * ss, fill=fill)
        if level:
            mask = Image.new("L", (S, S), 0)
            ImageDraw.Draw(mask).rounded_rectangle(box, radius=radius * ss, fill=255)
            top = round(box[3] - (box[3] - box[1]) * level / 100)
            band = Image.new("L", (S, S), 0)
            ImageDraw.Draw(band).rectangle((0, top, S, S), fill=255)
            liquid = Image.new("RGBA", (S, S), _blend(fill, color, 0.45))
            ImageDraw.Draw(liquid).rectangle((0, top, S, top + 2 * ss), fill=_blend(fill, color, 0.8))
            img.paste(liquid, (0, 0), ImageChops.multiply(mask, band))
        ImageDraw.Draw(img).rounded_rectangle(box, radius=radius * ss, outline=outline,
                                              width=round(1.5 * ss))
        _tiles[key] = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
    return _tiles[key]

def _draw_logo(size: int, ss: int = 1):
    """Fallback logo drawn with shapes only, for when icon.ico is missing."""
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
    # Blue and green buttons: the darker shade normally, the bright one on hover
    # (bright all the time was too loud).
    calm = {ACCENT: ACCENT_H, SUCCESS: SUCCESS_H}
    for bright, dark in calm.items():
        hov_map[dark] = bright
    bg = calm.get(bg, bg)
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
            kwargs["bg"] = calm.get(kwargs["bg"], kwargs["bg"])
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

_eyes = {}

def _eye_image(color, crossed, size=16):
    """Eye icon (crossed out = hidden), drawn 8x larger and scaled down."""
    key = (color, crossed, size)
    if key not in _eyes:
        from PIL import Image, ImageDraw, ImageTk
        ss = 8
        S = size * ss
        img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        w = round(1.6 * ss)
        box = (S * 0.06, S * 0.22, S * 0.94, S * 0.78)
        d.arc((box[0], S * 0.22, box[2], S * 1.10), 200, 340, fill=color, width=w)
        d.arc((box[0], S * -0.10, box[2], S * 0.78), 20, 160, fill=color, width=w)
        r = S * 0.15
        d.ellipse((S / 2 - r, S / 2 - r, S / 2 + r, S / 2 + r), fill=color)
        if crossed:
            d.line((S * 0.16, S * 0.86, S * 0.84, S * 0.14), fill=color, width=w)
        _eyes[key] = ImageTk.PhotoImage(img.resize((size, size), Image.LANCZOS))
    return _eyes[key]

def add_reveal(entry, mask="●"):
    """Eye button inside the right end of a password field: shows or hides
    what was typed."""
    btn = tk.Label(entry, bg=BG_INPUT, cursor="hand2", bd=0)
    def draw(hover=False):
        btn.configure(image=_eye_image(FG if hover else FG_MUTED, entry.cget("show") != ""))
    def toggle(_=None):
        entry.configure(show="" if entry.cget("show") else mask)
        draw(True)
    btn.bind("<Button-1>", toggle)
    btn.bind("<Enter>", lambda e: draw(True))
    btn.bind("<Leave>", lambda e: draw(False))
    draw()
    btn.place(relx=1.0, rely=0.5, anchor="e", x=-4)
    Tooltip(btn, "Show or hide what you type")
    return btn

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
                text += ("\n\n(The key fingerprint wasn't passed through - this "
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
            if kind == "secret":
                add_reveal(self.entry)
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
        build = getattr(self, "build", None)  # optional: fills the tip with widgets
        if self._tip or not (self.text or build) or not self.widget.winfo_exists():
            return
        tip = self._tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)  # no title bar
        tip.attributes("-topmost", True)
        if build:
            box = tk.Frame(tip, bg=BG_CARD, padx=10, pady=8,
                           highlightthickness=1, highlightbackground=BORDER)
            box.pack()
            build(box)
        else:
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

def _blend(c1, c2, t):
    """Mix two #rrggbb colours: t=0 gives c1, t=1 gives c2."""
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * t):02x}" for x, y in zip(a, b))

class Card(tk.Frame):
    def __init__(self, parent, conn, mgr):
        super().__init__(parent, bg=BG_CARD,
                         highlightbackground=BORDER, highlightthickness=1)
        self.conn, self.mgr = conn, mgr
        self._status = None
        self._build()
        self._refresh()

    TILE = 44

    def _build(self):
        # Drive letter tile; its colour shows the status
        t = self.TILE
        self.cv = tk.Canvas(self, width=t, height=t, bg=BG_CARD, highlightthickness=0)
        self.cv.pack(side="left", padx=(12, 12), pady=10)
        self._tile = self.cv.create_image(t / 2, t / 2, image=_tile_image(t, BG_INPUT, BORDER))
        self._tile_text = self.cv.create_text(t / 2, t / 2, text=self.conn["drive_letter"] + "\\",
                                              fill=FG_MUTED, font=("Consolas", 13, "bold"))
        self.cv.bind("<Button-1>", lambda e: self._open())  # click the tile to open the drive

        # Buttons are packed before the info so a narrow window cuts the text, not them
        bf = tk.Frame(self, bg=BG_CARD)
        bf.pack(side="right", padx=10)

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
        # While connected: "73% used" in the usage colour; hover it (or the tile)
        # for used / free / total space and ping
        self.lbl_sep = mk_label(host_row, "", fg=FG_MUTED, font=FONT_SMALL)
        self.lbl_sep.pack(side="left")
        self.lbl_pct = mk_label(host_row, "", font=FONT_SMALL)
        self.lbl_pct.pack(side="left")
        self._tile_tip = Tooltip(self.cv, "", delay=300)
        self._pct_tip = Tooltip(self.lbl_pct, "", delay=150)
        self.lbl_space = mk_label(host_row, "", fg=FG_MUTED, font=FONT_SMALL, anchor="w")
        self.lbl_space.pack(side="left")
        self.lbl_drive = mk_label(host_row, "", fg=WARNING, font=FONT_SMALL, anchor="w")
        self.lbl_drive.pack(side="left", padx=(8, 0))  # "not responding" when hung

        mk_btn(bf, "SSH", self._ssh, bg=SSH_BG).pack(side="left", padx=3)
        self.btn_tun = mk_btn(bf, "Tunnels", lambda: self.mgr.open_tunnels(self.conn["id"]))
        self.btn_tun.pack(side="left", padx=3)
        self.btn_tun.bind("<Button-3>", lambda e: self.mgr.tunnel_menu(self.conn["id"], self.btn_tun))
        self.update_tunnel_count()
        self.btn_con = mk_btn(bf, "Connect", self._toggle, bg=ACCENT, fg="white")
        # Keep text white even when disabled (e.g. during "Connecting...")
        self.btn_con.configure(disabledforeground="white")
        self.btn_con.pack(side="left", padx=3)
        self.btn_kill = mk_btn(bf, "Kill", self._kill, bg=DANGER)  # shown only when hung
        mk_btn(bf, "Edit", self._edit).pack(side="left", padx=3)
        mk_btn(bf, "✕", self._delete).pack(side="left", padx=3)
        self._bind_menu(self)

    def _bind_menu(self, widget):
        """Right-click anywhere on the card (except its buttons) for more actions."""
        if not isinstance(widget, tk.Button):
            widget.bind("<Button-3>", self._show_menu, add="+")
        for child in widget.winfo_children():
            self._bind_menu(child)

    def _show_menu(self, event):
        menu = tk.Menu(self, tearoff=0, bg=BG_CARD, fg=FG, activebackground=ACCENT,
                       activeforeground="white", disabledforeground=FG_MUTED,
                       bd=0, font=FONT)
        st = self._status
        if st == "hung":
            menu.add_command(label="Restart", command=self._restart)
            menu.add_command(label="Kill", command=self._kill)
        elif st == "connected":
            menu.add_command(label="Disconnect", command=self._disconnect)
        elif st == "foreign":
            menu.add_command(label="Change Letter...", command=self._toggle)
        else:
            menu.add_command(label="Connect", command=self._connect,
                             state="normal" if st in ("disconnected", "error") else "disabled")
        menu.add_command(label="Open in Explorer", command=self._open,
                         state="normal" if st == "connected" else "disabled")
        menu.add_command(label="SSH terminal", command=self._ssh)
        menu.add_command(label="Tunnels...", command=lambda: self.mgr.open_tunnels(self.conn["id"]))
        menu.add_separator()
        menu.add_command(label="Edit...", command=self._edit)
        menu.add_command(label="Duplicate", command=lambda: self.mgr.duplicate_connection(self.conn["id"]))
        menu.add_command(label="Delete", command=self._delete)
        menu.add_separator()
        ids = [c["id"] for c in self.mgr._connections]
        i, last, cid = ids.index(self.conn["id"]), len(ids) - 1, self.conn["id"]
        for label, to, ok in (("Move to Top", 0, i > 0), ("Move Up", i - 1, i > 0),
                              ("Move Down", i + 1, i < last), ("Move to Bottom", last, i < last)):
            menu.add_command(label=label, state="normal" if ok else "disabled",
                             command=lambda to=to: self.mgr.move_connection(cid, to))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def set_space(self, space, ping=None):
        text = ""
        connected = self._status == "connected"
        if connected and space and space[1]:
            free, total = space
            used = 1 - free / total
            color = _usage_color(used)
            self.cv.itemconfig(self._tile, image=_tank_image(
                self.TILE, _blend(BG_CARD, SUCCESS, 0.08), _blend(BG_CARD, SUCCESS, 0.6), used, color))
            self.cv.itemconfig(self._tile_text, fill=FG)
            self.lbl_sep.configure(text="  ·  ")
            self.lbl_pct.configure(text=f"{used:.0%} used", fg=color)
            rows = [("Used", f"{human_size(total - free)}  ({used:.0%})", color),
                    ("Free", human_size(free), FG), ("Total", human_size(total), FG)]
            if ping is not None:
                rows.append(("Ping", f"{ping} ms" if ping else "<1 ms", FG))
            def build(box, used=used, color=color, rows=rows):
                mk_label(box, f"{self.conn['drive_letter']}\\  {self.conn['name']}",
                         font=FONT_BOLD).grid(row=0, column=0, columnspan=2, sticky="w")
                box._bar = _bar_image(180, 6, used, color, _blend(BG_CARD, FG_MUTED, 0.25))
                tk.Label(box, image=box._bar, bg=BG_CARD, bd=0).grid(
                    row=1, column=0, columnspan=2, sticky="w", pady=(6, 6))
                for i, (k, v, c) in enumerate(rows, 2):
                    mk_label(box, k, fg=FG_MUTED, font=FONT_SMALL).grid(
                        row=i, column=0, sticky="w", padx=(0, 16))
                    mk_label(box, v, fg=c, font=("Segoe UI", 9, "bold")).grid(
                        row=i, column=1, sticky="e")
            self._tile_tip.build = self._pct_tip.build = build
        else:
            self.lbl_sep.configure(text="")
            self.lbl_pct.configure(text="")
            self._tile_tip.build = self._pct_tip.build = None
            # No space reading: still show the ping on the line
            if connected and ping is not None:
                text = f"  ·  {ping} ms" if ping else "  ·  <1 ms"
        self.lbl_space.configure(text=text)
        self.mgr.fit_width()

    def update_tunnel_count(self):
        n = self.mgr.tunnels.running_count(self.conn["id"])
        self.btn_tun.configure(text=f"Tunnels ({n})" if n else "Tunnels")

    def _host_part(self):
        c = self.conn
        ro = "  ·  read-only" if c.get("read_only") else ""
        via = ""
        try:
            jump = parse_jump(c.get("jump_host"), c["user"])
            if jump:
                via = f"  ·  via {jump[1]}"
        except ValueError:
            pass
        return f"{c['user']}@{c['host']}  ·  port {c.get('port', 22)}{via}{ro}"

    def _refresh(self):
        """Uses the poll worker's cached state; never touches the drive."""
        drive = self.conn["drive_letter"]
        state = self.mgr._drive_state.get(drive)
        if state is None:
            state = "checking" if letter_present(drive) else "unmounted"
        self._set({"mounted": "connected", "unmounted": "disconnected",
                   "hung": "hung"}.get(state, "checking"))

    def _set(self, status):
        if status == self._status:
            return  # no-op when unchanged - avoids flicker from poll loop
        self._status = status
        dot_colors = {"connected": SUCCESS, "connecting": WARNING, "stopping": WARNING,
                      "hung": WARNING, "disconnected": FG_MUTED, "error": DANGER,
                      "foreign": DANGER}
        color = dot_colors.get(status, FG_MUTED)
        self.cv.itemconfig(self._tile, image=_tile_image(
            self.TILE, _blend(BG_CARD, color, 0.16), _blend(BG_CARD, color, 0.6)))
        self.cv.itemconfig(self._tile_text, fill=color, text=self.conn["drive_letter"] + "\\")
        self.cv.configure(cursor="hand2" if status == "connected" else "")
        if status == "hung":
            self.lbl_drive.configure(text="not responding", fg=WARNING)
            self.btn_kill.pack(side="left", padx=3, after=self.btn_con)
        elif status == "foreign":
            drive = self.conn["drive_letter"]
            other = self.mgr._foreign.get(drive, "another drive")
            text = (f"waiting for {drive} to be free" if self.conn["id"] in self.mgr._wait_free
                    else f"{drive} is used by {other}")
            self.lbl_drive.configure(text=text, fg=DANGER)
            self.btn_kill.pack_forget()
        else:
            self.lbl_drive.configure(text="", fg=WARNING)
            self.btn_kill.pack_forget()
        self.mgr.fit_width()
        if status == "foreign":
            self.btn_con.configure(text="Change Letter", bg=WARNING, fg="white", state="normal")
        elif status == "hung":
            self.btn_con.configure(text="Restart", bg=WARNING, fg="white", state="normal")
        elif status in ("checking", "stopping"):
            text = "Checking…" if status == "checking" else "Stopping…"
            self.btn_con.configure(text=text, bg=BG_INPUT, fg=FG_MUTED, state="disabled")
        elif status == "connected":
            self.btn_con.configure(text="Disconnect", bg=DANGER, fg="white", state="normal")
        elif status == "connecting":
            self.btn_con.configure(text="Connecting…", bg=WARNING, fg="white", state="disabled")
        else:
            self.btn_con.configure(text="Connect", bg=ACCENT, fg="white", state="normal")

    def _toggle(self):
        if self._status == "connected": self._disconnect()
        elif self._status == "hung": self._restart()
        elif self._status == "foreign":
            self.mgr._wait_free.discard(self.conn["id"])
            self.mgr._letter_taken(self.conn["id"])
        else: self._connect()

    def _restart(self):
        if messagebox.askyesno("Restart mount",
                f"Restart {self.conn['drive_letter']} ({self.conn['name']})?\n\n"
                "Any copy to or from this drive that is still running will fail.",
                parent=self.mgr):
            self.mgr.kill_mount(self.conn["id"], then_connect=True)

    def _kill(self):
        if messagebox.askyesno("Kill mount",
                f"Kill {self.conn['drive_letter']} ({self.conn['name']})?\n\n"
                "The drive is removed. Any copy still running will fail.",
                parent=self.mgr):
            self.mgr.kill_mount(self.conn["id"])

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
        if self._status == "hung":
            messagebox.showwarning("Not responding",
                f"Drive {drive} is not responding. Use Restart or Kill first.",
                parent=self.mgr)
            return
        if self._status != "connected":
            messagebox.showinfo("Not Mounted", f"Drive {drive} is not mounted.",
                                parent=self.mgr)
            return
        def w():  # in a thread: if the drive hangs right now, only this thread waits
            try:
                os.startfile(drive + "\\")
            except OSError as e:
                self.mgr.after(0, lambda: messagebox.showerror("Open Failed", str(e)))
        threading.Thread(target=w, daemon=True).start()

    def refresh(self, conn):
        self.conn = conn
        self.lbl_name.configure(text=conn["name"])
        self.lbl_host.configure(text=self._host_part())
        self.cv.itemconfig(self._tile_text, text=conn["drive_letter"] + "\\")
        self._refresh()

class EditDialog(tk.Toplevel):
    def __init__(self, parent, conn, on_save, is_new=False):
        super().__init__(parent)
        self.conn, self.on_save = dict(conn), on_save
        self.withdraw()  # build hidden, then show centred over the parent
        self.title("New Connection" if is_new else "Edit Connection")
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
            if not letter_present(letter):
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
            self_owns = (d == self.conn.get("drive_letter") and
                         getattr(self.master, "_drive_state", {}).get(d) == "mounted")
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
        add_reveal(self.e_pass)
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

        # A private key may be encrypted. Remembering the passphrase is optional.
        self.lbl_pp = mk_label(form, "Passphrase", fg=FG_MUTED, font=FONT_SMALL)
        self.lbl_pp.grid(row=10, column=0, sticky="e", padx=(14, 8), pady=5)
        pp = tk.Frame(form, bg=BG)
        pp.grid(row=10, column=1, sticky="ew", padx=(0, 14), pady=5)
        pp.columnconfigure(0, weight=1)
        self._pp = pp
        self.e_passphrase = mk_entry(pp, width=20, show="●")
        add_reveal(self.e_passphrase)
        self.e_passphrase.grid(row=0, column=0, sticky="ew")
        self.e_passphrase.insert(0, self.conn.get("passphrase", ""))
        self.remember_passphrase_var = tk.BooleanVar(
            value=bool(self.conn.get("passphrase")))
        remember_cb = tk.Checkbutton(
            pp, text="Remember", variable=self.remember_passphrase_var,
            bg=BG, fg=FG, selectcolor=BG_INPUT,
            activebackground=BG, activeforeground=FG, font=FONT_SMALL)
        remember_cb.grid(row=0, column=1, padx=(8, 0))
        Tooltip(remember_cb, "Stored encrypted with Windows DPAPI for this Windows user.\n"
                             "Leave unchecked to use the passphrase only for this test.")

        # Extra args
        lbl("Extra Args", 11)
        self.e_extra = entry(11)
        self.e_extra.insert(0, self.conn.get("extra_args",""))

        # Optional jump host (ProxyJump)
        lbl("Jump Host", 12)
        self.e_jump = entry(12)
        self.e_jump.insert(0, self.conn.get("jump_host", ""))
        mk_label(form, "Optional: a server to connect through (user@host:port), with the "
                 "same login.",
                 fg=FG_MUTED, font=FONT_SMALL, wraplength=430, justify="left").grid(
            row=13, column=0, columnspan=2, sticky="w", padx=(14, 14), pady=(0, 4))

        # Read/write (default) or read-only
        lbl("Access", 14)
        self.ro_var = tk.BooleanVar(value=self.conn.get("read_only", False))
        rf = tk.Frame(form, bg=BG)
        rf.grid(row=14, column=1, sticky="w", pady=5)
        for v, t in [(False, "Read/Write"), (True, "Read-only")]:
            tk.Radiobutton(rf, text=t, variable=self.ro_var, value=v,
                           bg=BG, fg=FG, selectcolor=BG_INPUT,
                           activebackground=BG, activeforeground=FG,
                           font=FONT).pack(side="left", padx=5)

        # Mount options (sshfs settings that used to need Extra Args)
        mk_label(form, "Options", fg=FG_MUTED, font=FONT_SMALL).grid(
            row=15, column=0, sticky="ne", padx=(14, 8), pady=(7, 5))
        of = tk.Frame(form, bg=BG)
        of.grid(row=15, column=1, sticky="w", pady=5)
        self.opt_vars = {}
        for i, (key, text, _, _, tip) in enumerate(MOUNT_OPTIONS):
            var = self.opt_vars[key] = tk.BooleanVar(value=mount_option(self.conn, key))
            cb = tk.Checkbutton(of, text=text, variable=var, bg=BG, fg=FG, selectcolor=BG_INPUT,
                                activebackground=BG, activeforeground=FG, font=FONT)
            cb.grid(row=i // 3, column=i % 3, sticky="w", padx=(0, 10))
            Tooltip(cb, tip)
            if key == "use_sudo":
                cb.configure(command=self._sudo_change)

        # Separate sudo password (not the SSH password or key passphrase).
        self.lbl_sudo_password = mk_label(
            form, "Sudo Password", fg=FG_MUTED, font=FONT_SMALL)
        self.lbl_sudo_password.grid(row=16, column=0, sticky="e",
                                    padx=(14, 8), pady=5)
        self.e_sudo_password = mk_entry(form, show="●")
        add_reveal(self.e_sudo_password)
        self.e_sudo_password.grid(row=16, column=1, sticky="ew",
                                  padx=(0, 14), pady=5)
        self.e_sudo_password.insert(0, self.conn.get("sudo_password", ""))
        Tooltip(self.e_sudo_password,
                "Required only when sudo asks for a password. Saved with Windows DPAPI. "
                "Leave blank if sudo -n already works.")
        self._sudo_change()

        # Auto connect
        self.auto_var = tk.BooleanVar(value=self.conn.get("auto_connect", False))
        tk.Checkbutton(form, text="Connect automatically when the app starts", variable=self.auto_var,
                       bg=BG, fg=FG, selectcolor=BG_INPUT,
                       activebackground=BG, activeforeground=FG,
                       font=FONT).grid(row=17, column=1, sticky="w", pady=(8, 0))

        # Verify host key
        self.verify_var = tk.BooleanVar(value=self.conn.get("verify_host_key", False))
        tk.Checkbutton(form, text="Verify SSH host key (recommended)",
                       variable=self.verify_var,
                       bg=BG, fg=FG, selectcolor=BG_INPUT,
                       activebackground=BG, activeforeground=FG,
                       font=FONT).grid(row=18, column=1, sticky="w", pady=(0, 8))

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
        self.btn_key = mk_btn(bar, "Key login...", self._setup_key)
        self.btn_key.configure(disabledforeground=FG_MUTED)
        self.btn_key.pack(side="left", padx=(6, 0))
        Tooltip(self.btn_key, "Create an SSH key, install it on the server and\n"
                              "switch this connection to it (no stored password)")
        apply = mk_btn(bar, "Apply & Save", lambda: self._save(apply=True), bg=SUCCESS, fg="white")
        apply.pack(side="right", padx=(6, 0))
        Tooltip(apply, "Save, then connect the drive with the new settings\n"
                       "(a connected drive is disconnected and connected again)")
        mk_btn(bar, "Save", self._save, bg=ACCENT, fg="white").pack(side="right", padx=(6,0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right", padx=(24, 0))

        self._probe_password = None  # password typed for "Ask Each Time" tests
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    def _sudo_change(self):
        if self.opt_vars["use_sudo"].get():
            self.lbl_sudo_password.grid()
            self.e_sudo_password.grid()
        else:
            self.lbl_sudo_password.grid_remove()
            self.e_sudo_password.grid_remove()

    def _auth_change(self):
        auth = self.auth_var.get()
        if auth == "password":
            self.lbl_pw.grid(row=8, column=0, sticky="e", padx=(14,8), pady=5)
            self.e_pass.grid(row=8, column=1, sticky="ew", padx=(0,14), pady=5)
            self.lbl_key.grid_remove(); self._kf.grid_remove()
            self.lbl_pp.grid_remove(); self._pp.grid_remove()
        elif auth == "key":
            self.lbl_key.grid(row=9, column=0, sticky="e", padx=(14,8), pady=5)
            self._kf.grid(row=9, column=1, sticky="ew", padx=(0,14), pady=5)
            self.lbl_pp.grid(row=10, column=0, sticky="e", padx=(14,8), pady=5)
            self._pp.grid(row=10, column=1, sticky="ew", padx=(0,14), pady=5)
            self.lbl_pw.grid_remove(); self.e_pass.grid_remove()
        else:
            self.lbl_pw.grid_remove();  self.e_pass.grid_remove()
            self.lbl_key.grid_remove(); self._kf.grid_remove()
            self.lbl_pp.grid_remove(); self._pp.grid_remove()

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
            "passphrase": self.e_passphrase.get() if self.auth_var.get() == "key" else "",
            "verify_host_key": self.verify_var.get(),
            "jump_host": self.e_jump.get().strip(),
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
                    path_msg = (f"Remote path '{rp}' does not exist on the server - "
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
                text="Connected - click Browse… to pick a folder, or type a path manually.")

    def _setup_key(self):
        c = self._form_conn()
        if c["auth_type"] == "key":
            messagebox.showinfo("Key login", "This connection already logs in with a key.",
                                parent=self)
            return
        args = self._probe_args()
        if not args:
            return
        c, pw = args
        self.btn_key.configure(text="Setting up...", state="disabled")
        self.lbl_test.configure(text=f"Setting up key login on {c['host']}...", fg=FG_MUTED)

        def work():
            try:
                path = setup_key_login(c, pw, prompt_from_thread(self, _conn_label(c)))
            except Exception as e:
                return self._after_safe(self._key_done, None, str(e))
            self._after_safe(self._key_done, path, "")
        threading.Thread(target=work, daemon=True).start()

    def _key_done(self, path, error):
        if not self.winfo_exists():
            return
        self.btn_key.configure(text="Key login...", state="normal")
        if not path:
            self.lbl_test.configure(text="✗ " + error, fg=DANGER)
            return
        self.auth_var.set("key")
        self.e_key.delete(0, "end")
        self.e_key.insert(0, path)
        self._auth_change()
        msg = "✓ Key login works. Click Save to use it."
        if self.e_pass.get() and messagebox.askyesno(
                "Key login", "Key login is set up and tested.\n\n"
                "Forget the saved password for this connection?", parent=self):
            self.e_pass.delete(0, "end")
            msg = "✓ Key login works and the saved password is removed. Click Save to use it."
        self.lbl_test.configure(text=msg, fg=SUCCESS)

    def _browse_remote(self):
        args = self._probe_args()
        if not args:
            return
        c, pw = args
        def on_pick(path):
            self.e_remote.delete(0, "end")
            self.e_remote.insert(0, path)
            self.lbl_test.configure(text=f"✓ Remote path set to {path} - click Save to keep it.",
                                    fg=SUCCESS)
        start = c["remote_path"] if c["remote_path"] not in ("", "/") else getattr(
            self, "_probe_home", ".")
        RemoteBrowser(self, c, pw, start, on_pick)

    def _save(self, apply=False):
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
            "passphrase":      (self.e_passphrase.get() if self.auth_var.get() == "key"
                                and self.remember_passphrase_var.get() else ""),
            "sudo_password":   (self.e_sudo_password.get()
                                if self.opt_vars["use_sudo"].get() else ""),
            "extra_args":      self.e_extra.get().strip(),
            "auto_connect":    self.auto_var.get(),
            "verify_host_key": self.verify_var.get(),
            "read_only":       self.ro_var.get(),
            "jump_host":       self.e_jump.get().strip(),
            "mount_options":   {k: v.get() for k, v in self.opt_vars.items()},
        })
        if not self.conn["host"]:
            messagebox.showerror("Validation","Host is required.", parent=self); return
        if not self.conn["user"]:
            messagebox.showerror("Validation","Username is required.", parent=self); return
        try:
            parse_jump(self.conn["jump_host"], self.conn["user"])
        except ValueError:
            messagebox.showerror("Validation", "Jump Host can't be read. Use host, user@host "
                                 "or user@host:port (IPv6 with a port: user@[address]:port).",
                                 parent=self)
            return
        self.on_save(self.conn, apply)
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
        self._load(fallback, note=f"'{path}' not found on the server - "
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

def mk_pill(parent, text, color, bg=None):
    """Small coloured badge."""
    base = bg or parent.cget("bg")
    return tk.Label(parent, text=text, bg=_blend(base, color, 0.16), fg=color,
                    font=FONT_SMALL, padx=8, pady=1)

class RequirementsDialog(tk.Toplevel):
    """WinFsp / SSHFS-Win missing: install them with winget (Windows asks for
    permission for each), or open the download pages."""

    def __init__(self, app, missing):
        super().__init__(app)
        self.withdraw()
        self.app, self.missing = app, missing
        self.title("Missing Requirements")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(app)
        names = " and ".join(n for n, _ in missing)
        mk_label(self, f"{names} {'is' if len(missing) == 1 else 'are'} needed",
                 font=FONT_LARGE).pack(anchor="w", padx=24, pady=(16, 6))
        mk_label(self, "This app mounts drives with WinFsp and SSHFS-Win. Install for me "
                 "downloads and installs both for you. Windows asks for permission for "
                 "each, because WinFsp is a driver.",
                 fg=FG_MUTED, font=FONT_SMALL, wraplength=440, justify="left").pack(
            anchor="w", padx=24)
        box = tk.Frame(self, bg=BG)
        box.pack(fill="x", padx=24, pady=(12, 0))
        self._status = {}
        for i, (name, url) in enumerate(missing, 1):
            mk_label(box, f"{i}.  {name}", font=FONT_BOLD).grid(row=i, column=0, sticky="w", pady=3)
            st = mk_label(box, "not installed", fg=FG_MUTED, font=FONT_SMALL)
            st.grid(row=i, column=1, sticky="w", padx=(16, 0))
            self._status[name] = st
        self.lbl_msg = mk_label(self, "", fg=FG_MUTED, font=FONT_SMALL, wraplength=440,
                                justify="left")
        self.lbl_msg.pack(anchor="w", padx=24, pady=(10, 0))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=24, pady=(14, 16))
        self.btn_close = mk_btn(bar, "Later", self.destroy)
        self.btn_close.pack(side="right")
        self.btn_pages = mk_btn(bar, "Open download pages", self._pages)
        self.btn_pages.pack(side="right", padx=6)
        self.btn_install = mk_btn(bar, "Install for me", self._install, bg=SUCCESS, fg="white")
        self.btn_install.pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        show_centered(self, app)

    def _pages(self):
        for _, url in self.missing:
            self.app._open_url(url)

    def _set(self, name, text, color):
        if self.winfo_exists():
            self._status[name].configure(text=text, fg=color)

    def _install(self):
        for b in (self.btn_install, self.btn_pages):
            b.configure(state="disabled", bg=BG_INPUT, fg=FG_MUTED)
        self.btn_close.configure(text="Close")
        self.lbl_msg.configure(text="Installing... Click Yes when Windows asks for permission.",
                               fg=FG_MUTED)
        names = [n for n, _ in missing_requirements()]

        def work():
            failed = []
            for name in names:  # WinFsp first: SSHFS-Win needs it
                self.app.after(0, self._set, name, "installing...", WARNING)
                self.app.log(f"[INFO] Installing {name}...\n")
                try:
                    out = install_requirement(name)
                except Exception as e:
                    out = str(e)
                if out:
                    self.app.log("[INSTALL] " + out.replace("\n", "\n[INSTALL] ") + "\n")
                ok = name not in [n for n, _ in missing_requirements()]
                if not ok:
                    failed.append(name)
                self.app.after(0, self._set, name, "installed" if ok else "not installed",
                               SUCCESS if ok else DANGER)
                if not ok:
                    break
            self.app.after(0, self._finished, failed)
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, failed):
        left = self.app.check_requirements(log_missing=False)
        if not self.winfo_exists():
            return
        if not left:
            self.app.log("[OK] WinFsp and SSHFS-Win installed - ready to connect.\n")
            self.lbl_msg.configure(text="All set. You can connect your drives now.", fg=SUCCESS)
            self.btn_close.configure(text="Done", bg=ACCENT, fg="white")
            self.app._auto_connect()
        else:
            self.lbl_msg.configure(
                text=f"{' and '.join(failed or [n for n, _ in left])} couldn't be installed "
                     "automatically (maybe permission was refused, or there's no internet). "
                     "Try again, or use Open download pages.", fg=DANGER)
            self.btn_install.configure(state="normal", bg=SUCCESS, fg="white", text="Try again")
            self.btn_pages.configure(state="normal", bg=BG_INPUT, fg=FG)

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

        section("Connections")
        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", padx=(18, 0), pady=(2, 0))
        mk_btn(row, "Export...", lambda: parent.export_connections(self)).pack(side="left")
        mk_btn(row, "Import...", lambda: parent.import_connections(self)).pack(side="left", padx=(6, 0))
        note("Back up connections and tunnels or move them to another PC. Passwords "
             "are not included; add them again after importing.")

        section("Drives")
        self.restart_var = tk.BooleanVar(value=settings.get("auto_restart_hung", False))
        tk.Checkbutton(body, text="Restart a drive that stops responding",
                       variable=self.restart_var, **radio).pack(anchor="w", padx=(18, 0))
        note(f"After {HUNG_RESTART_SECS} seconds of no answer while no data is moving. "
             "A drive that is just busy with a big copy is never restarted. On by default.")

        section("Updates")
        self.update_var = tk.BooleanVar(value=settings.get("check_updates", False))
        tk.Checkbutton(body, text="Check for updates when the app starts",
                       variable=self.update_var, **radio).pack(anchor="w", padx=(18, 0))
        note("Asks GitHub at start and once a day whether a newer version exists. "
             "On by default.")
        row = tk.Frame(body, bg=BG)
        row.pack(anchor="w", padx=(18, 0), pady=(6, 0))
        self.btn_check = mk_btn(row, "Check for Updates", lambda: self._check_now(parent))
        self.btn_check.configure(disabledforeground=FG_MUTED)
        self.btn_check.pack(side="left")
        self.lbl_check = mk_label(row, f"You have version {APP_VERSION}.", fg=FG_MUTED,
                                  font=FONT_SMALL)
        self.lbl_check.pack(side="left", padx=(10, 0))

        section("Logging")
        self.log_var = tk.BooleanVar(value=settings["log_enabled"])
        tk.Checkbutton(body, text="Enable debug log", variable=self.log_var,
                       **radio).pack(anchor="w", padx=(18, 0))
        note("Also saved to kamen.log in the app's settings folder. When off, the "
             "Debug Log panel is hidden and nothing is recorded.")

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
                             start_minimized=self.min_var.get(),
                             auto_restart_hung=self.restart_var.get(),
                             check_updates=self.update_var.get())
        self.destroy()
        self.on_save(self.settings)

    def _check_now(self, app):
        self.btn_check.configure(state="disabled")
        self.lbl_check.configure(text="Checking...", fg=FG_MUTED)
        def done(info):
            if not self.winfo_exists():
                return
            self.btn_check.configure(state="normal")
            if not info:
                self.lbl_check.configure(text="Couldn't reach GitHub.", fg=DANGER)
            elif _version_tuple(info["version"]) > _version_tuple(APP_VERSION):
                self.lbl_check.configure(text=f"Version {info['version']} is available.",
                                         fg=SUCCESS)
            else:
                self.lbl_check.configure(text="You're up to date.", fg=SUCCESS)
        app.check_updates_now(done)

class UpdateDialog(tk.Toplevel):
    """Asks before Update Now: what happens, and whether to keep the old exe.
    Calls on_ok(keep_old)."""

    def __init__(self, parent, version, old_name, on_ok):
        super().__init__(parent)
        self.withdraw()
        self.on_ok = on_ok
        self.title("Update")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        mk_label(self, f"Update to version {version}?", font=FONT_LARGE).pack(
            padx=24, pady=(16, 8), anchor="w")
        mk_label(self, "The new version is downloaded from GitHub and checked, then the app "
                 "restarts. Connected drives stay connected, so open files and copies "
                 "carry on. Running tunnels are stopped and the ones set to start with "
                 "the app start again.", fg=FG_MUTED, font=FONT_SMALL, wraplength=420,
                 justify="left").pack(padx=24, anchor="w")
        mk_label(self, "Old version", font=FONT_BOLD).pack(padx=24, pady=(14, 2), anchor="w")
        self.keep_var = tk.BooleanVar(value=False)
        radio = dict(variable=self.keep_var, bg=BG, fg=FG, selectcolor=BG_INPUT,
                     activebackground=BG, activeforeground=FG, font=FONT)
        tk.Radiobutton(self, text="Delete it", value=False, **radio).pack(padx=(30, 24), anchor="w")
        tk.Radiobutton(self, text="Keep it as a backup", value=True, **radio).pack(
            padx=(30, 24), anchor="w")
        mk_label(self, f"It is renamed to \"{old_name}\" in the app's folder. "
                 "To use it again, rename it back to .exe. Older backups are not touched.",
                 fg=FG_MUTED, font=FONT_SMALL, wraplength=400,
                 justify="left").pack(padx=(48, 24), anchor="w")
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=24, pady=(16, 16))
        mk_btn(bar, "Update", self._ok, bg=SUCCESS, fg="white").pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        show_centered(self, parent)
        self.grab_set()

    def _ok(self):
        keep = self.keep_var.get()
        self.destroy()
        self.on_ok(keep)

class LetterTakenDialog(tk.Toplevel):
    """A connection's drive letter is used by another drive (USB stick, CD
    drive, other share). Offers the next free letter, waiting for the letter
    to be free again, or picking one in Edit."""

    def __init__(self, parent, conn, other, free, on_use, on_wait, on_choose):
        super().__init__(parent)
        self.withdraw()
        drive = conn["drive_letter"]
        self.title("Drive letter in use")
        self.configure(bg=BG)
        self.resizable(False, False)
        if parent.winfo_viewable():
            self.transient(parent)
        self.attributes("-topmost", True)
        mk_label(self, f"{drive} is used by {other}", font=FONT_LARGE).pack(
            padx=24, pady=(16, 8), anchor="w")
        mk_label(self, f"{conn['name']} needs another drive letter, or it can wait "
                 f"until {drive} is free.",
                 fg=FG_MUTED, font=FONT_SMALL, wraplength=420,
                 justify="left").pack(padx=24, anchor="w")
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=24, pady=(16, 16))
        def pick(fn):
            def go():
                self.destroy()
                fn()
            return go
        if free:
            mk_btn(bar, f"Use {free} instead", pick(on_use), bg=SUCCESS, fg="white").pack(
                side="right", padx=(6, 0))
        mk_btn(bar, f"Wait for {drive}", pick(on_wait)).pack(side="right", padx=(6, 0))
        mk_btn(bar, "Choose a letter…", pick(on_choose)).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        show_centered(self, parent)

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
            self.lbl_title.configure(text="Done - closing")
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

class DropMenu(tk.Toplevel):
    """Dropdown under a button, drawn in the app's theme (a native tk.Menu
    ignores most of it). items: (label, count, command); a count of 0 shows
    the item greyed out, None shows no count. The count can also be a short
    text (e.g. "running"). Closes on a click elsewhere, Escape or focus loss."""

    def __init__(self, button, items):
        super().__init__(button)
        self.overrideredirect(True)
        self.attributes("-topmost", True)  # never behind the window it belongs to
        self.configure(bg=BORDER)
        box = tk.Frame(self, bg=BG_CARD)
        box.pack(padx=1, pady=1, fill="both", expand=True)
        for label, count, command in items:
            on = count is None or bool(count)
            if label == "-":
                tk.Frame(box, bg=BORDER, height=1).pack(fill="x", pady=2)
                continue
            row = tk.Frame(box, bg=BG_CARD, cursor="hand2" if on else "")
            row.pack(fill="x")
            name = tk.Label(row, text=label, bg=BG_CARD, fg=FG if on else FG_MUTED,
                            font=FONT, anchor="w", padx=12, pady=7)
            name.pack(side="left", fill="x", expand=True)
            num = tk.Label(row, text="" if count is None else str(count), bg=BG_CARD,
                           fg=FG_MUTED, font=FONT_SMALL, padx=12)
            num.pack(side="right")
            if not on:
                continue
            parts = (row, name, num)
            def enter(e, parts=parts):
                for w in parts:
                    w.configure(bg=ACCENT)
                parts[1].configure(fg="white"); parts[2].configure(fg="white")
            def leave(e, parts=parts):
                for w in parts:
                    w.configure(bg=BG_CARD)
                parts[1].configure(fg=FG); parts[2].configure(fg=FG_MUTED)
            for w in parts:
                w.bind("<Enter>", enter)
                w.bind("<Leave>", leave)
                w.bind("<Button-1>", lambda e, c=command: self._pick(c))
        self.update_idletasks()
        width = max(button.winfo_width(), self.winfo_reqwidth())
        x = button.winfo_rootx() + button.winfo_width() - width  # right-aligned
        self.geometry(f"{width}x{self.winfo_reqheight()}+{x}+{button.winfo_rooty() + button.winfo_height() + 2}")
        self.bind("<Escape>", lambda e: self.destroy())
        # Clicks anywhere in the app come here while it's open; one outside the
        # dropdown closes it. Switching to another program closes it too.
        self.bind("<Button-1>", self._click)
        self.bind("<FocusOut>", lambda e: self.after(50, self._lost_focus))
        self.focus_force()
        self.grab_set()

    def _click(self, e):
        if not self.winfo_exists():
            return
        x, y = self.winfo_rootx(), self.winfo_rooty()
        if not (x <= e.x_root < x + self.winfo_width() and y <= e.y_root < y + self.winfo_height()):
            self.destroy()

    def _lost_focus(self):
        if self.winfo_exists() and self.focus_get() is None:
            self.destroy()

    def _pick(self, command):
        self.destroy()
        command()

class DriveActionDialog(tk.Toplevel):
    """Progress for All Drives > Connect All / Disconnect All: one row per
    drive. `work(conn)` runs in a thread per drive and returns True on
    success. Closes by itself when everything worked; stays open with a
    Close button if something failed."""

    def __init__(self, parent, conns, title, busy, ok_text, fail_text, work, on_row=None):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=BG)
        self.resizable(False, False)
        if parent.winfo_viewable():
            self.transient(parent)
        self._title, self._ok_text, self._fail_text = title, ok_text, fail_text
        self._closed, self._failed = False, 0
        self.lbl_title = mk_label(self, title, font=FONT_LARGE)
        self.lbl_title.pack(padx=24, pady=(16, 10))
        rows = tk.Frame(self, bg=BG)
        rows.pack(fill="x", padx=24)
        self._status = []
        for i, conn in enumerate(conns):
            mk_label(rows, conn["drive_letter"], font=FONT_BOLD).grid(row=i, column=0, sticky="w", pady=2)
            mk_label(rows, conn.get("name", ""), fg=FG_MUTED).grid(
                row=i, column=1, sticky="w", padx=(10, 24), pady=2)
            st = mk_label(rows, busy, fg=WARNING)
            st.grid(row=i, column=2, sticky="e", pady=2)
            self._status.append(st)
        rows.columnconfigure(1, weight=1)
        self._bar = tk.Frame(self, bg=BG)
        self._bar.pack(fill="x", padx=24, pady=(12, 16))
        self.minsize(340, 0)
        self.protocol("WM_DELETE_WINDOW", self._close)
        show_centered(self, parent)
        self._pending = len(conns)
        self._dots = 0
        self._animate()
        for i, conn in enumerate(conns):
            threading.Thread(target=self._work, args=(i, conn, work, on_row), daemon=True).start()

    def _work(self, i, conn, work, on_row):
        try:
            ok = bool(work(conn))
        except Exception:
            ok = False
        try: self.after(0, self._row_done, i, ok)
        except Exception: pass
        if on_row:
            on_row(conn, ok)

    def _row_done(self, i, ok):
        if self._closed:
            return
        self._status[i].configure(text=self._ok_text if ok else self._fail_text,
                                  fg=SUCCESS if ok else DANGER)
        self._failed += not ok
        self._pending -= 1
        if self._pending:
            return
        if self._failed:
            self.lbl_title.configure(text=f"{self._title}: {self._failed} failed")
            mk_label(self._bar, "See the Debug Log for details.", fg=FG_MUTED,
                     font=FONT_SMALL).pack(side="left")
            mk_btn(self._bar, "Close", self._close).pack(side="right")
        else:
            self.lbl_title.configure(text="Done")
            self.after(800, self._close)

    def _animate(self):
        if self._closed or self._pending == 0:
            return
        self._dots = (self._dots + 1) % 4
        self.lbl_title.configure(text=self._title + "." * self._dots)
        self.after(400, self._animate)

    def _close(self):
        if self._pending and not self._closed:
            return  # still working: wait (the drives keep going either way)
        self._closed = True
        self.destroy()

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self._settings = load_settings()
        apply_theme(self._settings["theme"])
        self._at_login = STARTUP_ARG in sys.argv[1:]
        # After an update: connect again the drives that were connected before it
        self._reconnect_ids = set()
        for a in sys.argv[1:]:
            if a.startswith(RECONNECT_ARG):
                self._reconnect_ids.update(x for x in a[len(RECONNECT_ARG):].split(",") if x)
        self._update_wake = threading.Event()
        self._update_info = None       # newest release found, if newer than this one
        self._notified_version = None  # tray notification shown once per version
        if self._at_login and self._settings.get("start_minimized", True):
            self.withdraw()  # avoid a window flash before going to the tray
        if getattr(sys, "frozen", False):
            # Start with Windows is on by default: enable it once on first run.
            # The flag means a later "off" in Settings/Task Manager is respected.
            if not self._settings.get("startup_default_applied"):
                try:
                    set_start_with_windows(True)
                    self.after(800, self._startup_notice)  # tell the user once
                except OSError:
                    pass
                self._settings["startup_default_applied"] = True
                try: save_settings(self._settings)
                except OSError: pass
            # If the exe was moved/rebuilt elsewhere, point the Run entry at it.
            reg = get_start_with_windows()
            if reg and reg != _startup_command():
                try: set_start_with_windows(True)
                except OSError: pass
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("700x540")
        self.minsize(600,400)
        self.configure(bg=BG)
        # Replace default Tk feather with the K monogram
        self._set_window_icon()
        self._connections = load_connections()
        self._cards = {}
        self._fit_pending = False
        self._ping = {}  # connection id -> last ping in ms (None: no answer)
        self._drive_state = {}  # drive -> "mounted" / "unmounted" / "hung" / "foreign", set by the poll worker
        self._foreign = {}  # drive -> what else uses that letter, e.g. "magicJack (CD drive)"
        self._wait_free = set()  # connection ids to connect once their letter is free again
        self._tasks = load_tasks()
        self._task_running = set()  # task ids running right now
        self._task_due = {}  # task id -> next scheduled run (epoch seconds)
        self._tasks_window = None
        self._seen_state = {}  # connection id -> last poll state, for "when a drive connects"
        self._taken_dialogs = {}  # connection id -> open LetterTakenDialog
        self.tunnels = TunnelManager(self.on_tunnel_change)
        self._tunnel_windows = {}
        setup_file_log(self._settings["log_enabled"])
        file_log(f"[INFO] {APP_NAME} {APP_VERSION} started")
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
        self._auto_start_tunnels()
        update.cleanup_old()
        self._update_loop()
        self._upgrade_mounts()
        self._task_loop()
        self._listen_for_show()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        if self._at_login and self._settings.get("start_minimized", True):
            self.after(100, self._minimize_to_tray)

    def _startup_notice(self):
        messagebox.showinfo(
            "Start with Windows",
            f"{APP_NAME} will now start with Windows, minimized to the tray, so your "
            "drives and tunnels connect when you log in.\n\n"
            "You can turn this off any time in Settings > Startup.", parent=self)

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
        hver = mk_label(hdr, f"v{APP_VERSION}", fg=FG_MUTED, font=FONT_SMALL)
        hver.pack(side="left", padx=(8, 0), pady=(6, 0))
        Tooltip(hver, build_info())
        mk_btn(hdr, "⚙ Settings", self._open_settings,
               bg=ACCENT).pack(side="right", padx=(4, 12), pady=10)
        mk_btn(hdr, "Tasks", self.open_tasks, bg=BG_INPUT, fg=FG).pack(side="right", padx=4, pady=10)
        self._all_btn = mk_btn(hdr, "All Drives ▾", self._all_drives_menu, bg=BG_INPUT, fg=FG)
        self._all_btn.pack(side="right", padx=4, pady=10)
        mk_btn(hdr, "+ Add Connection", self._add,
               bg=SUCCESS).pack(side="right", padx=4, pady=10)
        self._hdr = hdr

        # Missing-requirements banner (shown by check_requirements when needed)
        self._req_banner = tk.Frame(self, bg=BANNER_BG,
                                    highlightbackground=WARNING, highlightthickness=1)
        # New-version banner (shown by _show_update when needed)
        self._update_banner = tk.Frame(self, bg=_blend(BG, ACCENT, 0.18),
                                       highlightbackground=ACCENT, highlightthickness=1)

        # Scrollable list
        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True)
        self.cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = self._list_sb = DarkScrollbar(outer, command=self.cv.yview)
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

        # Single bottom bar: [Debug Log] [Clear Log] ........ [Tray] [Exit]
        bar = tk.Frame(self, bg=BG_HEAD, height=42)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        # Right side: Exit, then Minimize to Tray (packed right->left)
        mk_btn(bar, "Exit", self._on_close, bg=EXIT_BG).pack(side="right", padx=(4, 12), pady=6)
        mk_btn(bar, "Minimize to Tray", self._minimize_to_tray,
               bg=TRAY_BG).pack(side="right", padx=4, pady=6)

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
        self._logfile_btn = tk.Button(bar, text="Log File", bg=BG_HEAD, fg=FG_MUTED,
                                      relief="flat", bd=0, font=FONT_SMALL, cursor="hand2",
                                      activebackground=BG_HEAD, activeforeground=ACCENT,
                                      command=self._open_log_file)
        self._logfile_btn.bind("<Enter>", lambda e: self._logfile_btn.configure(fg=ACCENT))
        self._logfile_btn.bind("<Leave>", lambda e: self._logfile_btn.configure(fg=FG_MUTED))

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
            self._logfile_btn.pack(side="left", padx=10, after=self._copy_btn)
        else:
            self.log_frame.pack_forget()
            self._log_btn.configure(text="▶  Debug Log")
            self._clear_btn.pack_forget()
            self._copy_btn.pack_forget()
            self._logfile_btn.pack_forget()

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
               f"{' - install in this order' if len(missing) > 1 else ''}, "
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
        mk_btn(self._req_banner, "Install for me",
               lambda: self.show_requirements_dialog(missing_requirements()),
               bg=SUCCESS).pack(side="right", padx=4, pady=8)
        self._req_banner.pack(fill="x", side="top", after=self._hdr)

        for name, url in (missing if log_missing else []):
            self.log(f"[WARN] {name} not installed - download: {url}\n")
        return missing

    def _recheck_requirements(self):
        if not self.check_requirements():
            self.log("[OK] WinFsp and SSHFS-Win found - ready to connect.\n")
            messagebox.showinfo("Requirements", "WinFsp and SSHFS-Win are installed.\n"
                                "You're ready to connect.", parent=self)

    def show_requirements_dialog(self, missing):
        if not missing:
            return
        w = getattr(self, "_req_dialog", None)
        if w and w.winfo_exists():
            w.lift()
            return
        self._req_dialog = RequirementsDialog(self, missing)

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
        self.fit_width()

    def fit_width(self):
        """Don't let the window get narrower than the widest card (buttons plus
        name and host line), so nothing on a card is cut off."""
        if self._fit_pending:
            return
        self._fit_pending = True
        def fit():
            self._fit_pending = False
            cards = [c for c in self._cards.values() if c.winfo_exists()]
            if not cards:
                return
            need = max(c.winfo_reqwidth() for c in cards) + 28 + self._list_sb.winfo_reqwidth()
            self.minsize(max(600, need), 400)
        self.after(100, fit)  # after Tk has worked out the new sizes

    def _add(self):
        self.open_edit_dialog(new_connection(), is_new=True)

    def open_edit_dialog(self, conn, is_new=False):
        def on_save(updated, apply=False):
            old = None if is_new else dict(self.find_conn(updated["id"]) or {}) or None
            if is_new:
                self._connections.append(updated)
            else:
                for i, c in enumerate(self._connections):
                    if c["id"] == updated["id"]:
                        self._connections[i] = updated; break
            save_connections(self._connections)
            self._render()
            if apply:
                self.apply_connection(old, updated)
        EditDialog(self, conn, on_save, is_new=is_new)

    def apply_connection(self, old, new):
        """Apply & Save: unmount the drive as it was saved before (if it is
        mounted), then mount it with the new settings."""
        cid = new["id"]
        card = self._cards.get(cid)
        if not card:
            return
        if not (old and self._ours_mounted(old)):
            card._connect()
            return
        card._set("stopping")
        self.log(f"[INFO] Applying changes to {new['name']}: reconnecting...\n")
        def w():
            disconnect(old, log=self.log)
            self._drive_state[old["drive_letter"]] = "unmounted"
            def reconnect():
                c = self._cards.get(cid)
                if c and c.winfo_exists():
                    c._set("disconnected")
                    c._connect()
            try: self.after(0, reconnect)
            except RuntimeError: pass
        threading.Thread(target=w, daemon=True).start()

    def find_conn(self, cid):
        return next((c for c in self._connections if c["id"] == cid), None)

    def tunnel_menu(self, cid, button):
        """Right-click on a card's Tunnels button: open a tunnel (starting it if
        needed) without opening the Tunnels window. Start/stop is in the window."""
        conn = self.find_conn(cid)
        if not conn:
            return
        tunnels = conn.get("tunnels", [])
        items = []
        for t in tunnels:
            if t["type"] == "local":
                db = t.get("remote_port") in DB_PORTS
                items.append((f"{'Copy address of' if db else 'Open'} {t['name']}", None,
                              lambda t=t, db=db: self.open_tunnel(cid, t, db)))
        if items:
            items.append(("-", None, None))
        items.append(("Manage Tunnels..." if tunnels else "Add a Tunnel...", None,
                      lambda: self.open_tunnels(cid)))
        DropMenu(button, items)

    def open_tunnel(self, cid, t, db=False, tries=0):
        """Open a local tunnel in the browser (or copy a database tunnel's
        address), starting it first if it isn't running."""
        state = self.tunnels.state(t["id"])[0]
        if state != "running":
            if tries == 0:
                self.tunnels.start(self.find_conn(cid), t, self.log)
            if tries < 50 and (tries == 0 or state in TunnelManager.ACTIVE):
                self.after(300, self.open_tunnel, cid, t, db, tries + 1)
            return
        addr = f"127.0.0.1:{t['local_port']}"
        if db:
            self.clipboard_clear()
            self.clipboard_append(addr)
            self.log(f"[TUNNEL] Copied {addr} for your database client.\n")
        else:
            import webbrowser
            webbrowser.open(f"http://{addr}")

    def open_tunnels(self, cid):
        w = self._tunnel_windows.get(cid)
        if w and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        self._tunnel_windows[cid] = TunnelsWindow(self, cid)

    def on_tunnel_change(self, cid, tid):
        """Called from tunnel worker threads."""
        def w():
            card = self._cards.get(cid)
            if card and card.winfo_exists():
                card.update_tunnel_count()
            win = self._tunnel_windows.get(cid)
            if win and win.winfo_exists():
                win.refresh()
            self._refresh_tray()
        try:
            self.after(0, w)
        except RuntimeError:
            pass

    def _auto_start_tunnels(self):
        for conn in self._connections:
            for t in conn.get("tunnels", []):
                if t.get("auto_start"):
                    self.tunnels.start(conn, t, self.log)

    def _update_loop(self):
        """Ask GitHub for a newer release shortly after start, then once a day.
        If GitHub can't be reached, try again after 10 minutes."""
        def check():
            info = latest_release()
            if not info:
                self.log("[INFO] Update check: couldn't reach GitHub, trying again in 10 minutes.\n")
                return 600
            if _version_tuple(info["version"]) > _version_tuple(APP_VERSION):
                self.log(f"[INFO] Version {info['version']} is available: {info['url']}\n")
                for _ in range(20):  # the main loop may not be running yet
                    try:
                        self.after(0, self._show_update, info)
                        break
                    except RuntimeError:
                        time.sleep(0.25)
            else:
                self.log(f"[INFO] Update check: {APP_VERSION} is the latest version.\n")
            return 24 * 3600

        def loop():
            wait = 5
            while True:
                self._update_wake.wait(wait)
                self._update_wake.clear()
                if self._stop.is_set():
                    return
                # Turned off: sleep until Settings turns it back on (wakes us)
                wait = check() if self._settings.get("check_updates") else None
        threading.Thread(target=loop, daemon=True).start()

    def check_updates_now(self, done):
        """Settings > Check for Updates: ask GitHub now, even if the daily check
        is off. done(info or None) runs on the UI thread; a newer version also
        brings the banner back, even if it was dismissed."""
        def w():
            info = latest_release()
            def ui():
                if info and _version_tuple(info["version"]) > _version_tuple(APP_VERSION):
                    self.log(f"[INFO] Version {info['version']} is available: {info['url']}\n")
                    self._update_info = None
                    self._show_update(info)
                elif info:
                    self.log(f"[INFO] Update check: {APP_VERSION} is the latest version.\n")
                else:
                    self.log("[INFO] Update check: couldn't reach GitHub.\n")
                done(info)
            try: self.after(0, ui)
            except RuntimeError: pass
        threading.Thread(target=w, daemon=True).start()

    def _show_update(self, info):
        import webbrowser
        new = self._update_info is None or info["version"] != self._update_info["version"]
        self._update_info = info
        if self._notified_version != info["version"]:
            self._notified_version = info["version"]
            self._notify(f"Version {info['version']} is available. "
                         f"Open {APP_NAME} to update.")
        b = self._update_banner
        if not new and not b.winfo_ismapped():
            return  # dismissed: don't bring it back for the same version
        for w in b.winfo_children():
            w.destroy()
        bg = b.cget("bg")
        self._update_lbl = tk.Label(
            b, text=f"Version {info['version']} is available (you have {APP_VERSION}).",
            bg=bg, fg=FG, font=FONT_SMALL)
        self._update_lbl.pack(side="left", padx=(12, 8), pady=8)
        self._update_btns = []
        btn = mk_btn(b, "Dismiss", b.pack_forget)
        btn.pack(side="right", padx=(4, 10), pady=8)
        self._update_btns.append(btn)
        if update.can_self_update() and info.get("exe") and info.get("sums"):
            btn = mk_btn(b, "Release Notes", lambda: webbrowser.open(info["url"]))
            btn.pack(side="right", padx=4, pady=8)
            self._update_btns.append(btn)
            btn = mk_btn(b, "Update Now", lambda: self._update_now(info), bg=SUCCESS, fg="white")
        else:
            # Can't update in place (running from source, or the release has no
            # checksum to verify against): send them to the release page
            btn = mk_btn(b, "Download", lambda: webbrowser.open(info["url"]), bg=ACCENT, fg="white")
        btn.pack(side="right", padx=4, pady=8)
        self._update_btns.append(btn)
        b.pack(fill="x", side="top", after=self._hdr)

    def _update_now(self, info):
        old = update.old_path()
        UpdateDialog(self, info["version"], old.name,
                     lambda keep: self._start_update(info, old, keep))

    def _start_update(self, info, old, keep_old):
        for btn in self._update_btns:
            btn.configure(state="disabled")
        self._update_lbl.configure(text=f"Downloading version {info['version']}...")
        def progress(pct):
            try: self.after(0, lambda: self._update_lbl.configure(
                text=f"Downloading version {info['version']}... {pct}%"))
            except RuntimeError: pass
        def w():
            try:
                new = update.download(info, progress)
            except Exception as e:
                self.log(f"[ERROR] Update download failed: {e}\n")
                try: self.after(0, self._update_failed, info, str(e))
                except RuntimeError: pass
                return
            try: self.after(0, self._install_update, info, new, old, keep_old)
            except RuntimeError: pass
        threading.Thread(target=w, daemon=True).start()

    def _update_failed(self, info, err):
        messagebox.showerror("Update",
            f"The update couldn't be installed:\n{err}\n\n"
            "You can download it from the release page instead.", parent=self)
        self._update_info = None
        self._show_update(info)

    def _install_update(self, info, new, old, keep_old):
        try:
            update.swap_in(new, old)
        except OSError as e:
            self.log(f"[ERROR] Couldn't replace the exe: {e}\n")
            self._update_failed(info, f"Couldn't replace the exe ({e}).")
            return
        self.log(f"[INFO] Updated to {info['version']}, restarting...\n")
        if keep_old:
            self.log(f"[INFO] The old version is kept as {old}\n")
        # Drives stay mounted: sshfs doesn't need this app, and the new copy
        # picks them up on its first status check. Tunnels are stopped (the new
        # copy starts the ones set to start with the app).
        self.tunnels.stop_all()
        self._stop.set()
        self._update_wake.set()
        update.start_new([], old, keep_old)
        self._finish_close()

    def export_connections(self, parent):
        path = filedialog.asksaveasfilename(
            parent=parent, title="Export connections", defaultextension=".json",
            initialfile="kamen-connections.json", filetypes=[("JSON files", "*.json")])
        if not path:
            return
        conns = [{k: v for k, v in c.items() if k not in ("password", "passphrase", "sudo_password")}
                 for c in self._connections]
        data = {"app": APP_NAME, "version": APP_VERSION, "connections": conns}
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError as e:
            messagebox.showerror("Export", f"Couldn't save the file:\n{e}", parent=parent)
            return
        self.log(f"[INFO] Exported {len(conns)} connection(s) to {path}\n")
        messagebox.showinfo("Export", f"Exported {len(conns)} connection(s).\n\n"
                            "Passwords, sudo passwords and key passphrases are not included.", parent=parent)

    def import_connections(self, parent):
        import copy, string
        path = filedialog.askopenfilename(parent=parent, title="Import connections",
                                          filetypes=[("JSON files", "*.json"), ("All files", "*.*")])
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            items = data["connections"] if isinstance(data, dict) else data
            items = [c for c in items if isinstance(c, dict) and c.get("host") and c.get("user")]
        except Exception as e:
            messagebox.showerror("Import", f"That isn't a connections file:\n{e}", parent=parent)
            return
        if not items:
            messagebox.showinfo("Import", "No connections found in that file.", parent=parent)
            return
        ids = {c["id"] for c in self._connections}
        letters = {c["drive_letter"] for c in self._connections}
        need_pw, base = [], int(time.time() * 1000)
        for i, raw in enumerate(items):
            c = dict(new_connection(), **copy.deepcopy(raw))
            c["password"] = ""
            c["passphrase"] = ""
            c["sudo_password"] = ""
            if c["id"] in ids:
                c["id"] = str(base + i)
            for j, t in enumerate(c.get("tunnels", [])):
                t["id"] = f"{c['id']}-{j}"
            if c["drive_letter"] in letters:
                free = [f"{l}:" for l in reversed(string.ascii_uppercase[3:])
                        if f"{l}:" not in letters and not letter_present(f"{l}:")]
                if free:
                    c["drive_letter"] = free[0]
            ids.add(c["id"])
            letters.add(c["drive_letter"])
            if c.get("auth_type") == "password":
                need_pw.append(c["name"])
            self._connections.append(c)
        save_connections(self._connections)
        self._render()
        self.log(f"[INFO] Imported {len(items)} connection(s) from {path}\n")
        msg = f"Imported {len(items)} connection(s)."
        if need_pw:
            msg += ("\n\nAdd the password with Edit (or use Key login) for:\n- "
                    + "\n- ".join(need_pw))
        messagebox.showinfo("Import", msg, parent=parent)

    def move_connection(self, cid, to):
        """Card right-click > Move: put the connection at position `to`."""
        i = next(n for n, c in enumerate(self._connections) if c["id"] == cid)
        to = max(0, min(to, len(self._connections) - 1))
        if to == i:
            return
        statuses = {k: c._status for k, c in self._cards.items()}
        self._connections.insert(to, self._connections.pop(i))
        save_connections(self._connections)
        self._render()
        for k, st in statuses.items():  # keep e.g. "Connecting..." on the moved cards
            if k in self._cards:
                self._cards[k]._set(st)
        self._refresh_tray()

    def duplicate_connection(self, cid):
        """Copy a connection (without its tunnels, whose ports would clash) onto
        the next free drive letter, then open the copy for editing."""
        import copy, string
        src = self.find_conn(cid)
        if not src:
            return
        dup = copy.deepcopy(src)
        dup.update(id=str(int(time.time() * 1000)), name=f"{src['name']} (copy)",
                   tunnels=[], auto_connect=False)
        taken = {c["drive_letter"] for c in self._connections}
        free = [f"{l}:" for l in reversed(string.ascii_uppercase[3:])
                if f"{l}:" not in taken and not letter_present(f"{l}:")]
        if free:
            dup["drive_letter"] = free[0]
        self._connections.append(dup)
        save_connections(self._connections)
        self._render()
        self.log(f"[INFO] Duplicated '{src['name']}' as '{dup['name']}' on {dup['drive_letter']}\n")
        self.open_edit_dialog(dup)

    def delete_connection(self, cid):
        self.tunnels.stop_conn(cid)
        w = self._tunnel_windows.pop(cid, None)
        if w and w.winfo_exists():
            w.destroy()
        self._connections = [c for c in self._connections if c["id"] != cid]
        save_connections(self._connections)
        self._render()

    def _all_drives_menu(self):
        """Header button: Connect All / Disconnect All."""
        up = [c for c in self._connections if self._ours_mounted(c)]
        down = [c for c in self._connections if not letter_present(c["drive_letter"])]
        DropMenu(self._all_btn, [
            ("Connect All", len(down), self._connect_all),
            ("Disconnect All", len(up), self._disconnect_all),
        ])

    def _connect_all(self):
        conns = [c for c in self._connections if not letter_present(c["drive_letter"])]
        if not conns:
            return
        missing = missing_requirements()
        if missing:
            self.check_requirements()
            self.show_requirements_dialog(missing)
            return
        for c in conns:
            self.set_card_status(c["id"], "connecting")
        def done(conn, ok):
            self.set_card_status(conn["id"], "connected" if ok else "error")
            if not ok:
                self.set_card_status(conn["id"], "disconnected", delay=2500)
        DriveActionDialog(self, conns, "Connecting drives", "connecting…", "✓ connected",
                          "⚠ failed", lambda c: connect(c, log=self.log), on_row=done)

    def _ours_mounted(self, conn):
        """Is this connection's drive mounted? Its letter being there isn't
        enough: another device may be using it (see foreign_drive)."""
        return letter_present(conn["drive_letter"]) and conn["drive_letter"] not in self._foreign

    def _disconnect_all(self):
        conns = [c for c in self._connections if self._ours_mounted(c)]
        if not conns:
            return
        for c in conns:
            self.set_card_status(c["id"], "stopping")
        def done(conn, ok):
            self.set_card_status(conn["id"], "disconnected" if ok else "hung")
        DriveActionDialog(self, conns, "Disconnecting drives", "disconnecting…",
                          "✓ unmounted", "⚠ still mounted",
                          lambda c: disconnect(c, log=self.log), on_row=done)

    def _auto_connect(self):
        conns = [c for c in self._connections
                 if c.get("auto_connect") or c["id"] in self._reconnect_ids]
        if not conns:
            return
        def w():  # probing can wait on a slow drive, so not on the UI thread
            for conn in conns:
                state = probe_drive(conn["drive_letter"])
                if state == "unmounted":
                    for _ in range(20):  # the main loop may not be running yet
                        try:
                            self.after(0, self._auto_connect_one, conn["id"])
                            break
                        except RuntimeError:
                            time.sleep(0.25)
                elif state == "hung":
                    self.log(f"[HUNG] {conn['drive_letter']} is not responding; "
                             f"not auto-connecting {conn['name']}.\n")
        threading.Thread(target=w, daemon=True).start()

    def _auto_connect_one(self, cid):
        card = self._cards.get(cid)
        if card and card._status in ("checking", "disconnected", "error"):
            # At Windows login the network may not be up yet: retry.
            card._connect(retries=5 if self._at_login else 0)

    def retry_connect(self, cid, retries):
        card = self._cards.get(cid)
        if card and card.winfo_exists() and card._status not in ("connected", "hung") \
                and not letter_present(card.conn["drive_letter"]):
            card._connect(retries=retries)

    def kill_mount(self, cid, then_connect=False):
        """Kill a mount by its sshfs process (works on a hung drive), then
        optionally mount it again."""
        card = self._cards.get(cid)
        if not card:
            return
        conn = card.conn
        card._set("stopping")
        self.log(f"[INFO] {'Restarting' if then_connect else 'Killing'} {conn['drive_letter']} "
                 f"({conn['name']})...\n")
        def w():
            gone = disconnect(conn, log=self.log)
            if not gone:
                self.log(f"[ERROR] {conn['drive_letter']} is still present after the kill.\n")
                self.set_card_status(cid, "hung")
                return
            self._drive_state[conn["drive_letter"]] = "unmounted"
            if not then_connect:
                self.set_card_status(cid, "disconnected")
                return
            self.set_card_status(cid, "connecting")
            ok = connect(conn, log=self.log)
            self.set_card_status(cid, "connected" if ok else "error")
            if not ok:
                self.set_card_status(cid, "disconnected", delay=2500)
        threading.Thread(target=w, daemon=True).start()

    def _poll(self):
        def loop():
            hits = {}        # drive -> consecutive "hung" probes
            space_at = {}    # drive -> last free-space check
            since = {}       # drive -> time it was first reported hung
            io_seen = {}     # drive -> sshfs CPU time (ms) at the last slow probe
            restarted = set()
            first = True
            while first or not self._stop.wait(5):
                first = False
                snapshot = []
                for cid, card in list(self._cards.items()):
                    conn = card.conn
                    drive = conn["drive_letter"]
                    try:
                        want = time.time() - space_at.get(drive, 0) >= 30
                        state = probe_drive(drive, want_space=want)
                        other = foreign_drive(drive) if state == "mounted" else None
                        if other:
                            state = "foreign"
                            if self._foreign.get(drive) != other:
                                self._foreign[drive] = other
                                self.log(f"[WARN] {drive} is used by {other}, not by "
                                         f"{conn['name']}. Pick another drive letter "
                                         f"for {conn['name']} in Edit.\n")
                                self._notify(f"{drive} is not {conn['name']}: it's used by "
                                             f"{other}. Pick another drive letter.")
                                self.after(0, self._letter_taken, cid)
                        elif state != "hung":
                            self._foreign.pop(drive, None)
                            if state == "unmounted" and cid in self._wait_free:
                                self._wait_free.discard(cid)
                                self.log(f"[INFO] {drive} is free again, connecting "
                                         f"{conn['name']}.\n")
                                self.after(0, card._connect)
                        if want and state == "mounted":
                            space_at[drive] = time.time()
                            threading.Thread(target=self._ping_server, args=(conn,),
                                             daemon=True).start()
                    except Exception:
                        continue
                    if state == "hung":
                        # Slow to answer while a big copy is running isn't
                        # frozen: if sshfs did work (used CPU) since the last
                        # slow probe, it's busy, so don't count it (or restart it).
                        io = cpu_ms(_find_sshfs_pids_for_drive(drive))
                        busy = io is not None and io_seen.get(drive) not in (None, io)
                        io_seen[drive] = io
                        if busy:
                            state = "mounted"
                    else:
                        io_seen.pop(drive, None)
                    if state == "hung":
                        hits[drive] = hits.get(drive, 0) + 1
                        if hits[drive] < 2:
                            continue  # one slow answer under heavy copying isn't a hang
                        if drive not in since:
                            since[drive] = time.time()
                            self._report_hung(conn)
                        elif (self._settings.get("auto_restart_hung") and drive not in restarted
                              and time.time() - since[drive] >= HUNG_RESTART_SECS):
                            restarted.add(drive)
                            self.log(f"[HUNG] Auto-restarting {drive} ({conn['name']}).\n")
                            self._notify(f"Restarting {conn['name']} ({drive}), "
                                         f"it stopped responding.")
                            self.after(0, self.kill_mount, cid, True)
                    else:
                        if drive in since:
                            self.log(f"[INFO] {drive} is responding again.\n")
                        hits.pop(drive, None)
                        since.pop(drive, None)
                        restarted.discard(drive)
                    self._drive_state[drive] = state
                    snapshot.append((cid, state))
                try:
                    self.after(0, self._apply_poll_snapshot, snapshot)
                except RuntimeError:
                    if self._stop.is_set():
                        return  # app closed
                    # main loop not started yet: try again on the next round
        threading.Thread(target=loop, daemon=True).start()

    def _upgrade_mounts(self):
        """Drives mounted by an older version the way that version mounted them
        (MOUNT_FORMAT) are remounted, so changes to how drives are mounted take
        effect right away. Each one waits until no data is moving, so a copy in
        progress isn't cut off."""
        def work():
            if self._stop.wait(8):  # let the first status checks settle
                return
            while not self._stop.is_set():
                pending = 0
                for conn in list(self._connections):
                    drive = conn["drive_letter"]
                    fmt = mount_format(drive)
                    if fmt is None or fmt >= MOUNT_FORMAT:
                        continue
                    if self._drive_state.get(drive) != "mounted":
                        pending += 1  # hung or still being checked: later
                        continue
                    pids = _find_sshfs_pids_for_drive(drive)
                    before = cpu_ms(pids)
                    if self._stop.wait(3):
                        return
                    if cpu_ms(pids) != before:
                        pending += 1  # busy: try again soon
                        continue
                    self.log(f"[INFO] {drive} ({conn['name']}) was mounted by an older "
                             f"version; remounting it so this version's drive settings "
                             f"take effect.\n")
                    try:
                        self.after(0, lambda c=conn["id"]: self.kill_mount(c, then_connect=True))
                    except RuntimeError:
                        return
                    for _ in range(60):  # wait for this one before the next drive
                        if self._stop.wait(1):
                            return
                        if mount_format(drive) == MOUNT_FORMAT:
                            break
                if not pending:
                    return
                if self._stop.wait(30):
                    return
        threading.Thread(target=work, daemon=True).start()

    def _ping_server(self, conn):
        """Poll worker, every 30 s per connected drive: ping time for its card."""
        self._ping[conn["id"]] = ping_ms(conn["host"])

    def _report_hung(self, conn):
        """Runs on the poll worker, once per hang."""
        drive = conn["drive_letter"]
        pids = _find_sshfs_pids_for_drive(drive)
        self.log(f"[HUNG] {drive} not responding since {time.strftime('%H:%M:%S')} "
                 f"(sshfs PID {', '.join(pids) or 'unknown'})\n")
        self._notify(f"{conn['name']} ({drive}) is not responding. "
                     f"Open {APP_NAME} to restart it.")

    def _free_letter(self):
        import string
        taken = {c["drive_letter"] for c in self._connections}
        free = [f"{l}:" for l in reversed(string.ascii_uppercase[3:])
                if f"{l}:" not in taken and not letter_present(f"{l}:")]
        return free[0] if free else None

    def _letter_taken(self, cid):
        """Poll worker found this connection's letter used by another drive."""
        conn = self.find_conn(cid)
        old = self._taken_dialogs.get(cid)
        if not conn or cid in self._wait_free or (old and old.winfo_exists()):
            return
        drive = conn["drive_letter"]
        other = self._foreign.get(drive)
        if not other:
            return
        free = self._free_letter()

        def use():
            c = self.find_conn(cid)
            new = self._free_letter()
            if not c or not new:
                return
            self.log(f"[INFO] {c['name']} moved from {c['drive_letter']} to {new}.\n")
            c["drive_letter"] = new
            save_connections(self._connections)
            self._render()
            card = self._cards.get(cid)
            if card:
                card._connect()

        def wait():
            self._wait_free.add(cid)
            self.log(f"[INFO] {conn['name']} connects by itself when {drive} is free again.\n")
            card = self._cards.get(cid)
            if card:
                card._status = None
                card._set("foreign")

        self._taken_dialogs[cid] = LetterTakenDialog(
            self, conn, other, free, use, wait, lambda: self.open_edit_dialog(self.find_conn(cid)))

    def _notify(self, msg):
        icon = self._tray_icon
        if icon:
            try:
                icon.notify(msg, APP_NAME)
            except Exception:
                pass

    def _apply_poll_snapshot(self, snapshot):
        status = {"mounted": "connected", "unmounted": "disconnected", "hung": "hung",
                  "foreign": "foreign"}
        for cid, state in snapshot:
            card = self._cards.get(cid)
            if card and card._status not in ("connecting", "stopping"):
                card._set(status[state])
            if card:
                card.set_space(drive_space.get(card.conn["drive_letter"]) if state == "mounted" else None,
                               self._ping.get(cid))
            # "When a drive connects" tasks: only on a real change from
            # unmounted to mounted, not for drives already up at app start.
            prev = self._seen_state.get(cid)
            self._seen_state[cid] = state
            if state == "mounted" and prev == "unmounted":
                for t in self._tasks:
                    if t["trigger"] == "connect" and t["conn_id"] == cid and t["type"] != "todo":
                        self.run_task(t["id"], auto=True)
        self._refresh_tray()

    # Tasks

    def open_tasks(self):
        w = self._tasks_window
        if w and w.winfo_exists():
            w.deiconify()
            w.lift()
            return
        self._tasks_window = TasksWindow(self)

    def _refresh_tasks(self):
        w = self._tasks_window
        if w and w.winfo_exists():
            w.refresh()

    def find_task(self, tid):
        return next((t for t in self._tasks if t["id"] == tid), None)

    def save_task(self, task):
        for i, t in enumerate(self._tasks):
            if t["id"] == task["id"]:
                self._tasks[i] = task
                break
        else:
            self._tasks.append(task)
        self._task_due.pop(task["id"], None)  # schedule may have changed
        save_tasks(self._tasks)
        self._refresh_tasks()

    def delete_task(self, tid):
        self._tasks = [t for t in self._tasks if t["id"] != tid]
        self._task_due.pop(tid, None)
        save_tasks(self._tasks)
        self._refresh_tasks()

    def set_task_done(self, tid, done):
        t = self.find_task(tid)
        if t:
            t["done"] = done
            save_tasks(self._tasks)
            self._refresh_tasks()

    def run_task(self, tid, auto=False):
        """Run a task in a worker thread. auto: started by its trigger."""
        t = self.find_task(tid)
        if not t or t["type"] == "todo" or tid in self._task_running:
            return
        t = dict(t)
        self._task_running.add(tid)
        self._refresh_tasks()
        self.log(f"[TASK] Running '{t['name']}'...\n")

        def work():
            try:
                ok, out = self._do_task(t)
            except Exception as e:
                ok, out = False, f"{type(e).__name__}: {e}"
            self.after(0, self._task_done, tid, ok, out, auto)
        threading.Thread(target=work, daemon=True).start()

    def _do_task(self, t):
        if t["type"] == "command":
            conn = self.find_conn(t["conn_id"])
            if not conn:
                return False, "The server for this task was deleted. Edit the task."
            return run_command(conn, t["command"],
                               prompt=prompt_from_thread(self, _conn_label(conn)))
        if t["type"] == "program":
            return run_program(t["command"], t["workdir"], t["wait"])
        if t["type"] == "copy":
            # A From or To folder on one of our drives: connect that drive first.
            for path in (t["src"], t["dst"]):
                drive = drive_of(path)
                conn = next((c for c in self._connections if c["drive_letter"] == drive), None)
                if conn and not self._ours_mounted(conn):
                    if drive in self._foreign:
                        return False, f"{drive} is used by {self._foreign[drive]}, not {conn['name']}."
                    self.log(f"[TASK] Connecting {conn['name']} ({drive}) first...\n")
                    self.set_card_status(conn["id"], "connecting")
                    ok = connect(conn, log=self.log)
                    self.set_card_status(conn["id"], "connected" if ok else "disconnected")
                    if not ok:
                        return False, f"Couldn't connect {conn['name']} ({drive})."
            return run_copy(t["src"], t["dst"], t["mode"])
        return False, "Nothing to run."

    def _task_done(self, tid, ok, out, auto):
        self._task_running.discard(tid)
        t = self.find_task(tid)
        if t:
            t.update(last_run=time.time(), last_ok=ok, last_output=out[-20000:])
            save_tasks(self._tasks)
            self.log(f"[TASK] '{t['name']}' {'finished' if ok else 'failed'}.\n")
            if (auto and not ok) or t["notify"]:
                self._notify(f"Task '{t['name']}' {'finished' if ok else 'failed'}.")
        self._refresh_tasks()

    def _task_loop(self):
        """Runs "when the app starts" tasks once, then scheduled tasks when due."""
        def due():
            now = time.time()
            for t in list(self._tasks):
                if t["trigger"] != "schedule" or t["type"] == "todo":
                    continue
                when = self._task_due.get(t["id"])
                if when is None:
                    self._task_due[t["id"]] = next_run(t, now)
                elif now >= when:
                    self._task_due[t["id"]] = next_run(t, now)
                    self.run_task(t["id"], auto=True)
        def loop():
            if self._stop.wait(15):  # let drives connect first
                return
            try:
                self.after(0, lambda: [self.run_task(t["id"], auto=True) for t in self._tasks
                                       if t["trigger"] == "start" and t["type"] != "todo"])
            except RuntimeError:
                return
            while not self._stop.wait(20):
                try:
                    self.after(0, due)
                except RuntimeError:
                    return
        threading.Thread(target=loop, daemon=True).start()

    def _listen_for_show(self):
        """A second launch signals this event; bring this window to the front."""
        if not winutil._show_event:
            return
        def w():
            while True:
                if _k32.WaitForSingleObject(winutil._show_event, 0xFFFFFFFF) != 0:
                    return
                try:
                    self.after(0, self._restore)
                except RuntimeError:
                    return
        threading.Thread(target=w, daemon=True).start()

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

    def _open_log_file(self):
        """Explorer with kamen.log selected (or just the folder if there's no log yet)."""
        if LOG_PATH.exists():
            subprocess.Popen(["explorer", "/select,", str(LOG_PATH)])
        else:
            os.startfile(str(LOG_PATH.parent))

    LOG_MAX_LINES = 100

    def log(self, msg):
        if not self._settings["log_enabled"]:
            return
        file_log(msg)
        def w():
            self.log_text.configure(state="normal")
            self.log_text.insert("end", msg)
            # Trim to the most recent LOG_MAX_LINES lines
            line_count = int(self.log_text.index("end-1c").split(".")[0])
            if line_count > self.LOG_MAX_LINES:
                self.log_text.delete("1.0", f"{line_count - self.LOG_MAX_LINES + 1}.0")
            self.log_text.configure(state="disabled")
        try:
            self.after(0, w)
        except RuntimeError:
            pass  # window not running yet or already closed; the file log has it

    def set_card_status(self, cid, status, delay=0):
        """Thread-safe card status update, looked up by connection id."""
        def w():
            card = self._cards.get(cid)
            if card and card.winfo_exists():
                card._set(status)
            self._refresh_tray()
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
        if new.get("check_updates") and not old.get("check_updates"):
            self._update_wake.set()  # check now instead of waiting
        setup_file_log(new["log_enabled"])
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
                f"pystray/Pillow not installed - cannot minimize to tray.\n({e})")
            self.deiconify(); return
        img = _make_logo(64)
        if img is None:
            messagebox.showinfo("Tray Unavailable",
                "Pillow (PIL) is required for the tray icon.")
            self.deiconify(); return

        self._tray_img = img
        self._tray_hung = False
        menu = pystray.Menu(lambda: self._tray_items(pystray))
        self._tray_icon = pystray.Icon(APP_NAME, img, APP_NAME, menu)
        self._tray_icon.run_detached = False
        threading.Thread(target=self._tray_icon.run, daemon=True).start()
        self._refresh_tray()

    def _tray_items(self, pystray):
        """Built by pystray each time the menu opens (on its own thread), so it
        only reads state and hands every action to the UI thread."""
        Item, Menu = pystray.MenuItem, pystray.Menu
        ui = lambda fn, *a: (lambda: self.after(0, fn, *a))
        items = [Item("Show", ui(self._restore), default=True), Menu.SEPARATOR]
        for conn in list(self._connections):
            cid = conn["id"]
            card = self._cards.get(cid)
            status = card._status if card else None
            label = f"{conn['drive_letter']}\\  {conn['name']}"
            if status == "hung":
                label += "  (not responding)"
            sub = []
            if status == "hung":
                sub.append(Item("Restart", ui(self.kill_mount, cid, True)))
                sub.append(Item("Kill", ui(self.kill_mount, cid)))
            elif status == "connected":
                sub.append(Item("Disconnect", ui(self._tray_card, cid, "_disconnect")))
            else:
                sub.append(Item("Connect", ui(self._tray_card, cid, "_connect"),
                                enabled=status in ("disconnected", "error", None)))
            sub.append(Item("Open in Explorer", ui(self._tray_card, cid, "_open"),
                            enabled=status == "connected"))
            n = self.tunnels.running_count(cid)
            sub.append(Item(f"Tunnels ({n})..." if n else "Tunnels...",
                            ui(self._tray_tunnels, cid)))
            items.append(Item(label, Menu(*sub), checked=lambda item, s=status: s == "connected"))
        items += [Menu.SEPARATOR, Item("Exit", ui(self._quit))]
        return items

    def _tray_card(self, cid, action):
        card = self._cards.get(cid)
        if card:
            getattr(card, action)()

    def _tray_tunnels(self, cid):
        self._restore()
        self.open_tunnels(cid)

    def _refresh_tray(self):
        """Rebuild the tray menu and show an orange dot while a drive is stuck."""
        icon = self._tray_icon
        if not icon:
            return
        hung = any(c._status == "hung" for c in self._cards.values())
        if hung != self._tray_hung:
            self._tray_hung = hung
            img = self._tray_img
            if hung:
                from PIL import ImageDraw
                img = img.copy()
                d = ImageDraw.Draw(img)
                w = img.width
                d.ellipse((w * 0.58, w * 0.58, w - 1, w - 1), fill=THEMES["dark"]["WARNING"],
                          outline=LOGO_BG, width=max(2, w // 24))
            icon.icon = img
            icon.title = f"{APP_NAME} - a drive is not responding" if hung else APP_NAME
        try:
            icon.update_menu()
        except Exception:
            pass

    def _restore(self):
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.deiconify()
        self.lift()
        self.attributes("-topmost", True)  # win the foreground lock, then let go
        self.after(200, lambda: self.attributes("-topmost", False))
        self.focus_force()

    def _quit(self):
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self._on_close()

    def _on_close(self):
        mounted = [c for c in self._connections if self._ours_mounted(c)]
        tunnels = self.tunnels.active_count()
        if mounted or tunnels:
            what = []
            if mounted:
                what.append(f"{len(mounted)} connection(s) still mounted")
            if tunnels:
                what.append(f"{tunnels} tunnel(s) running")
            ans = messagebox.askyesnocancel(
                "Active Connections",
                f"{' and '.join(what).capitalize()}.\n\nYes = Disconnect & Exit\n"
                f"No = Minimize to tray\nCancel = Go back")
            if ans is None:
                return
            if ans:
                self.tunnels.stop_all()
                if not mounted:
                    self._finish_close()
                    return
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
        self.tunnels.stop_all()
        if hasattr(self, "_tray_icon") and self._tray_icon:
            self._tray_icon.stop()
            self._tray_icon = None
        self.destroy()

# Last, so these modules can copy every helper defined above
from .ui_tasks import OutputDialog, TaskDialog, TasksWindow  # noqa: E402
from .ui_tunnels import TunnelDialog, TunnelsWindow  # noqa: E402

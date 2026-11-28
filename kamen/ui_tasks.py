"""Tasks window, the Add / Edit Task dialog and the output viewer."""

from . import ui as _ui

# These windows share the main module's helpers, widgets and theme colours.
# apply_theme() in ui.py updates this module's colours too (see _THEMED).
globals().update({k: v for k, v in vars(_ui).items() if not k.startswith("__")})
_ui._THEMED.append(__import__(__name__, fromlist=["_"]))

def mk_text(parent, height=3, mono=False, width=40):
    return tk.Text(parent, height=height, width=width, bg=BG_INPUT, fg=FG, insertbackground=FG,
                   relief="flat", bd=4, font=FONT_MONO if mono else FONT, wrap="word")

def mk_scroll_text(parent, height=3, mono=False, width=40):
    """Text box with a slim scrollbar that shows when the text is longer than
    the box. Returns (frame, text)."""
    f = tk.Frame(parent, bg=BG_INPUT)
    txt = mk_text(f, height, mono, width)
    sb = DarkScrollbar(f, command=txt.yview)
    sb.configure(bg=BG_INPUT)
    txt.configure(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    txt.pack(side="left", fill="both", expand=True)
    return f, txt

class TaskDialog(tk.Toplevel):
    """Add or edit one task. Calls on_save(task)."""

    def __init__(self, app, task, on_save, parent=None):
        parent = parent or app
        super().__init__(parent)
        self.withdraw()
        self.app, self.on_save = app, on_save
        self.t = dict(new_task(), **task) if task else new_task()
        self.title("Edit Task" if task else "New Task")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)
        form = tk.Frame(self, bg=BG)
        form.pack(fill="both", expand=True, padx=8, pady=(12, 0))
        form.columnconfigure(1, weight=1)
        self.form = form
        radio = dict(bg=BG, fg=FG, selectcolor=BG_INPUT, activebackground=BG,
                     activeforeground=FG, font=FONT)
        self._rows = {}

        def row(key, r, label, widget):
            lab = mk_label(form, label, fg=FG_MUTED, font=FONT_SMALL)
            lab.grid(row=r, column=0, sticky="ne", padx=(14, 8), pady=(8, 5))
            widget.grid(row=r, column=1, sticky="ew", padx=(0, 14), pady=5)
            self._rows[key] = (lab, widget)

        def with_browse(entry_value, folder):
            f = tk.Frame(form, bg=BG)
            f.columnconfigure(0, weight=1)
            e = mk_entry(f, width=50)
            e.grid(row=0, column=0, sticky="ew")
            e.insert(0, entry_value)
            def pick():
                p = (filedialog.askdirectory(parent=self) if folder
                     else filedialog.askopenfilename(parent=self))
                if p:
                    e.delete(0, "end")
                    e.insert(0, os.path.normpath(p))
            mk_btn(f, "Browse", pick, width=7).grid(row=0, column=1, padx=(6, 0))
            return f, e

        self.e_name = mk_entry(form, width=56)
        self.e_name.insert(0, self.t["name"])
        row("name", 0, "Name", self.e_name)

        self.type_var = tk.StringVar(value=self.t["type"])
        tf = tk.Frame(form, bg=BG)
        for i, (v, text) in enumerate(TASK_TYPES):
            tk.Radiobutton(tf, text=text, value=v, variable=self.type_var,
                           command=self._refresh, **radio).grid(
                row=i // 2, column=i % 2, sticky="w", padx=(0, 14))
        row("type", 1, "Type", tf)

        self.conns = list(app._connections)
        self.cb_conn = ttk.Combobox(form, state="readonly", width=54, values=["(none)"] + [
            f"{c['name']} ({c['drive_letter']})" for c in self.conns])
        self.cb_conn.current(next((i + 1 for i, c in enumerate(self.conns)
                                   if c["id"] == self.t["conn_id"]), 0))
        row("conn", 2, "Server", self.cb_conn)

        nf, self.txt_notes = mk_scroll_text(form, 7, width=56)
        self.txt_notes.insert("1.0", self.t["notes"])
        row("notes", 3, "Notes", nf)

        cf, self.txt_cmd = mk_scroll_text(form, 7, mono=True, width=60)
        self.txt_cmd.insert("1.0", self.t["command"] if self.t["type"] == "command" else "")
        row("command", 4, "Command", cf)

        pf, self.e_prog = with_browse(self.t["command"] if self.t["type"] == "program" else "", False)
        row("program", 5, "Program", pf)
        wf, self.e_workdir = with_browse(self.t["workdir"], True)
        row("workdir", 6, "Start in", wf)
        self.wait_var = tk.BooleanVar(value=self.t["wait"])
        wcb = tk.Checkbutton(form, text="Wait for it to finish and keep its output",
                             variable=self.wait_var, **radio)
        row("wait", 7, "", wcb)
        Tooltip(wcb, "Untick for programs that stay open, e.g. an editor.")

        sf, self.e_src = with_browse(self.t["src"], True)
        row("src", 8, "From", sf)
        df, self.e_dst = with_browse(self.t["dst"], True)
        row("dst", 9, "To", df)
        self.mode_var = tk.StringVar(value=self.t["mode"])
        mf = tk.Frame(form, bg=BG)
        for v, text in COPY_MODES:
            tk.Radiobutton(mf, text=text, value=v, variable=self.mode_var, **radio).pack(anchor="w")
        row("mode", 10, "Copy", mf)

        self.cb_when = ttk.Combobox(form, state="readonly", width=54,
                                    values=[t for _, t in TASK_TRIGGERS])
        self.cb_when.current(next(i for i, (k, _) in enumerate(TASK_TRIGGERS)
                                  if k == self.t["trigger"]))
        self.cb_when.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        row("when", 11, "Run", self.cb_when)

        sch = tk.Frame(form, bg=BG)
        self.cb_every = ttk.Combobox(sch, state="readonly", width=14,
                                     values=["Every N minutes", "Daily", "Weekly"])
        self.cb_every.current({"minutes": 0, "daily": 1, "weekly": 2}[self.t["every"]])
        self.cb_every.bind("<<ComboboxSelected>>", lambda e: self._refresh())
        self.cb_every.pack(side="left")
        self.e_minutes = mk_entry(sch, width=6)
        self.e_minutes.insert(0, str(self.t["minutes"]))
        self.cb_day = ttk.Combobox(sch, state="readonly", width=11, values=DAYS)
        self.cb_day.current(int(self.t["day"]))
        self.lbl_at = mk_label(sch, "at", fg=FG_MUTED, font=FONT_SMALL)
        self.e_time = mk_entry(sch, width=7)
        self.e_time.insert(0, self.t["time"])
        self._sch_parts = sch
        row("schedule", 12, "Every", sch)

        self.notify_var = tk.BooleanVar(value=self.t["notify"])
        row("notify", 13, "", tk.Checkbutton(
            form, text="Notify me when it finishes (failures always notify)",
            variable=self.notify_var, **radio))

        self.lbl_err = mk_label(self, "", fg=DANGER, font=FONT_SMALL)
        self.lbl_err.pack(anchor="w", padx=22)
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=22, pady=(8, 16))
        mk_btn(bar, "Save", self._save, bg=ACCENT, fg="white").pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right")
        self.bind("<Escape>", lambda e: self.destroy())
        self._refresh()
        show_centered(self, parent)

    def _trigger(self):
        return TASK_TRIGGERS[self.cb_when.current()][0]

    def _refresh(self):
        kind, trig = self.type_var.get(), self._trigger()
        show = {"name", "type"}
        show |= {"todo": {"notes", "conn"}, "command": {"conn", "command"},
                 "program": {"program", "workdir", "wait"},
                 "copy": {"src", "dst", "mode"}}[kind]
        if kind != "todo":
            show |= {"when", "notify"}
            if trig == "schedule":
                show.add("schedule")
            elif trig == "connect":
                show.add("conn")
        for key, (lab, w) in self._rows.items():
            if key in show:
                lab.grid(); w.grid()
            else:
                lab.grid_remove(); w.grid_remove()
        self._rows["conn"][0].configure(
            text="Drive" if kind in ("program", "copy") else "Server")
        every = self.cb_every.current()
        for w in (self.e_minutes, self.cb_day, self.lbl_at, self.e_time):
            w.pack_forget()
        if every == 0:
            self.e_minutes.pack(side="left", padx=(8, 4))
            self._rows["schedule"][0].configure(text="Every")
        else:
            if every == 2:
                self.cb_day.pack(side="left", padx=(8, 0))
            self.lbl_at.pack(side="left", padx=6)
            self.e_time.pack(side="left")
            self._rows["schedule"][0].configure(text="Schedule")
        self.lbl_err.configure(text="")

    def _save(self):
        kind, trig = self.type_var.get(), self._trigger()
        conn_i = self.cb_conn.current()
        t = dict(self.t)
        t.update(name=self.e_name.get().strip(), type=kind,
                 conn_id=self.conns[conn_i - 1]["id"] if conn_i > 0 else "",
                 notes=self.txt_notes.get("1.0", "end").strip(),
                 workdir=self.e_workdir.get().strip(), wait=self.wait_var.get(),
                 src=self.e_src.get().strip(), dst=self.e_dst.get().strip(),
                 mode=self.mode_var.get(), trigger="manual" if kind == "todo" else trig,
                 every=["minutes", "daily", "weekly"][self.cb_every.current()],
                 day=self.cb_day.current(), time=self.e_time.get().strip(),
                 notify=self.notify_var.get())
        t["command"] = (self.txt_cmd.get("1.0", "end").strip() if kind == "command"
                        else self.e_prog.get().strip() if kind == "program" else "")
        try:
            t["minutes"] = max(1, int(self.e_minutes.get().strip()))
        except ValueError:
            t["minutes"] = 0
        err = None
        if not t["name"]:
            err = "Give the task a name."
        elif kind == "command" and not t["conn_id"]:
            err = "Pick the server to run the command on."
        elif kind in ("command", "program") and not t["command"]:
            err = "Enter the command to run."
        elif kind == "copy" and not (t["src"] and t["dst"]):
            err = "Pick the From and To folders."
        elif kind == "copy" and os.path.normcase(t["src"]) == os.path.normcase(t["dst"]):
            err = "From and To can't be the same folder."
        elif kind != "todo" and trig == "connect" and not t["conn_id"]:
            err = "Pick the drive that starts this task when it connects."
        elif kind != "todo" and trig == "schedule" and t["every"] == "minutes" and not t["minutes"]:
            err = "Enter how many minutes between runs."
        elif kind != "todo" and trig == "schedule" and t["every"] != "minutes" \
                and not valid_time(t["time"]):
            err = "Enter the time as HH:MM, e.g. 03:00."
        if err:
            self.lbl_err.configure(text=err)
            return
        self.destroy()
        self.on_save(t)

class OutputDialog(tk.Toplevel):
    """A task's last output."""

    def __init__(self, parent, title, text):
        super().__init__(parent)
        self.withdraw()
        self.title(title)
        self.configure(bg=BG)
        self.transient(parent)
        box = mk_text(self, 18, mono=True)
        box.configure(width=90, wrap="none")
        box.insert("1.0", text or "(no output)")
        box.configure(state="disabled")
        box.pack(fill="both", expand=True, padx=12, pady=(12, 6))
        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=12, pady=(0, 12))
        def copy():
            self.clipboard_clear()
            self.clipboard_append(text or "")
        mk_btn(bar, "Close", self.destroy).pack(side="right")
        mk_btn(bar, "Copy", copy).pack(side="right", padx=6)
        self.bind("<Escape>", lambda e: self.destroy())
        show_centered(self, parent)

TASK_ICONS = {"todo": "✓", "command": ">_", "program": "▶", "copy": "⇄"}

def _task_color(kind):
    return {"todo": ACCENT, "command": SUCCESS, "program": WARNING, "copy": "#A78BFA"}[kind]

class TasksWindow(tk.Toplevel):
    """All tasks as cards: to-dos with a tick-off tile, the rest with Run,
    Edit and Delete. Scrolls when the list is long."""

    FILTERS = [("all", "All"), ("todo", "To-dos"), ("auto", "Automations")]

    def __init__(self, app):
        super().__init__(app)
        self.withdraw()
        self.app = app
        self.title("Tasks")
        self.configure(bg=BG)
        # Not transient: Windows gives transient windows no maximize button.
        self.protocol("WM_DELETE_WINDOW", self._close)
        self._wraps = []  # description labels, re-wrapped when the window is resized
        self._filter = "all"
        self._order = None  # card order, kept while the window is open

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 8))
        mk_label(top, "Tasks", font=FONT_LARGE).pack(side="left")
        self.lbl_sum = mk_label(top, "", fg=DANGER, font=FONT_SMALL)
        self.lbl_sum.pack(side="left", padx=(10, 0), pady=(2, 0))
        self.tabs = tk.Frame(top, bg=BG)
        self.tabs.pack(side="right")

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=(14, 6))
        self.cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = DarkScrollbar(outer, command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.rows = tk.Frame(self.cv, bg=BG)
        win = self.cv.create_window((0, 0), window=self.rows, anchor="nw")
        def resized(e):
            self.cv.itemconfig(win, width=e.width - 6)
            self._wrap = max(250, e.width - 260)
            for lab in self._wraps:
                if lab.winfo_exists():
                    lab.configure(wraplength=self._wrap)
        self._wrap = 480
        self.cv.bind("<Configure>", resized)
        self.rows.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        _bind_mousewheel(self.cv, lambda d: self.cv.yview_scroll(-1 * (d // 120), "units"),
                         container=outer)

        bar = tk.Frame(self, bg=BG_HEAD)
        bar.pack(fill="x", side="bottom")
        mk_btn(bar, "+ Add Task", self._add, bg=SUCCESS).pack(side="left", padx=14, pady=7)
        mk_btn(bar, "Close", self._close).pack(side="right", padx=14, pady=7)
        self.minsize(600, 320)
        self.geometry("720x480")
        self.refresh()
        show_centered(self, app, size=(720, 480))
        self.grab_release()  # not modal: the main window stays usable

    def _close(self):
        self.app._tasks_window = None
        self.destroy()

    def _set_filter(self, key):
        self._filter = key
        self._order = None
        self.cv.yview_moveto(0)
        self.refresh()

    def _tabs(self, tasks):
        for w in self.tabs.winfo_children():
            w.destroy()
        counts = {"all": len(tasks), "todo": sum(t["type"] == "todo" for t in tasks)}
        counts["auto"] = counts["all"] - counts["todo"]
        for key, text in self.FILTERS:
            on = key == self._filter
            b = tk.Label(self.tabs, text=f"{text}  {counts[key]}", font=FONT_SMALL, padx=10, pady=2,
                         bg=ACCENT if on else BG_INPUT, fg="white" if on else FG,
                         cursor="hand2")
            b.pack(side="left", padx=(4, 0))
            b.bind("<Button-1>", lambda e, k=key: self._set_filter(k))
        failed = sum(t["type"] != "todo" and t["last_ok"] is False for t in tasks)
        self.lbl_sum.configure(text=f"{failed} failed" if failed else "")

    def refresh(self):
        if not self.winfo_exists():
            return
        top = self.cv.yview()[0]  # keep the scroll position across the rebuild
        self._build_rows()
        self.update_idletasks()
        self.cv.configure(scrollregion=self.cv.bbox("all"))
        self.cv.yview_moveto(top)

    def _build_rows(self):
        for w in self.rows.winfo_children():
            w.destroy()
        self._wraps = []
        tasks = self.app._tasks
        self._tabs(tasks)
        shown = [t for t in tasks if self._filter == "all"
                 or (self._filter == "todo") == (t["type"] == "todo")]
        # Done to-dos go last, but only when the list is first shown: ticking one
        # off must not make the cards jump around while you're looking at them.
        if self._order is None:
            shown.sort(key=lambda t: t["type"] == "todo" and t["done"])
            self._order = [t["id"] for t in shown]
        else:
            pos = {tid: i for i, tid in enumerate(self._order)}
            shown.sort(key=lambda t: pos.get(t["id"], len(pos)))  # new tasks at the end
            self._order = [t["id"] for t in shown]
        if not shown:
            box = tk.Frame(self.rows, bg=BG)
            box.pack(fill="x", pady=40)
            mk_label(box, "No tasks here yet", font=FONT_LARGE, fg=FG_MUTED).pack()
            mk_label(box, "Click + Add Task to add a to-do, a server command, a program "
                     "or a folder copy.", fg=FG_MUTED, font=FONT_SMALL).pack(pady=(4, 0))
            return
        for t in shown:
            self._card(t)

    def _card(self, t):
        app = self.app
        conn = app.find_conn(t["conn_id"]) if t["conn_id"] else None
        cname = conn["name"] if conn else None
        running = t["id"] in app._task_running
        todo = t["type"] == "todo"
        done = todo and t["done"]
        color = _task_color(t["type"])

        card = tk.Frame(self.rows, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 4))

        # Icon tile: the task type; for a to-do it's the tick-off box
        tile = tk.Canvas(card, width=28, height=28, bg=BG_CARD, highlightthickness=0)
        tile.pack(side="left", padx=(8, 10), pady=6, anchor="n")
        if todo:
            c = SUCCESS if done else FG_MUTED
            tile.create_image(14, 14, image=_tile_image(28, _blend(BG_CARD, c, 0.16 if done else 0.05),
                                                       _blend(BG_CARD, c, 0.7), radius=7))
            if done:
                tile.create_text(14, 14, text="✓", fill=SUCCESS, font=("Segoe UI", 11, "bold"))
            tile.configure(cursor="hand2")
            tile.bind("<Button-1>", lambda e: app.set_task_done(t["id"], not t["done"]))
            Tooltip(tile, "Mark as not done" if done else "Mark as done")
        else:
            tile.create_image(14, 14, image=_tile_image(28, _blend(BG_CARD, color, 0.16),
                                                       _blend(BG_CARD, color, 0.6), radius=7))
            tile.create_text(14, 14, text=TASK_ICONS[t["type"]], fill=color,
                             font=("Consolas", 9, "bold"))
            Tooltip(tile, dict(TASK_TYPES)[t["type"]])

        bf = tk.Frame(card, bg=BG_CARD)
        bf.pack(side="right", padx=8, pady=6, anchor="n")
        def small(b):
            b.configure(padx=8, pady=1, font=FONT_SMALL)
            b.pack(side="left", padx=2)
            return b
        if not todo:
            b = small(mk_btn(bf, "Running…" if running else "Run",
                             lambda: app.run_task(t["id"]), bg=ACCENT))
            if running:
                b.configure(state="disabled", bg=BG_INPUT, fg=FG_MUTED)
        small(mk_btn(bf, "Edit", lambda: TaskDialog(app, t, app.save_task, parent=self)))
        small(mk_btn(bf, "✕", lambda: self._delete(t)))

        info = tk.Frame(card, bg=BG_CARD)
        info.pack(side="left", fill="x", expand=True, pady=5)
        line = tk.Frame(info, bg=BG_CARD)
        line.pack(anchor="w")
        mk_label(line, t["name"], fg=FG_MUTED if done else FG, anchor="w",
                 font=("Segoe UI", 10, "bold overstrike" if done else "bold")).pack(side="left")
        pills = tk.Frame(line, bg=BG_CARD)
        pills.pack(side="left", padx=(10, 0))
        what = describe_task(t, cname)
        if todo and cname:
            what = f"{cname}: {what}" if what else cname
        if what:
            lab = mk_label(info, what, fg=FG_MUTED, font=FONT_SMALL, anchor="w", justify="left",
                           wraplength=self._wrap)
            lab.pack(anchor="w")
            self._wraps.append(lab)
        if todo:
            return
        trig = t["trigger"]
        icon = {"schedule": "⏱", "connect": "⚡", "start": "↻"}.get(trig, "")
        when = describe_when(t, cname)
        mk_pill(pills, f"{icon} {when[0].upper()}{when[1:]}".strip(),
                FG if trig == "manual" else ACCENT).pack(side="left", padx=(0, 4))
        if running:
            st = mk_pill(pills, "Running…", WARNING)
        elif t["last_run"]:
            stamp = time.strftime("%d %b %H:%M", time.localtime(t["last_run"]))
            st = mk_pill(pills, f"{'✓ OK' if t['last_ok'] else '✕ Failed'} · {stamp}",
                         SUCCESS if t["last_ok"] else DANGER)
            st.configure(cursor="hand2")
            st.bind("<Button-1>", lambda e: OutputDialog(self, f"Output - {t['name']}",
                                                         t["last_output"]))
            Tooltip(st, "Show the output of the last run")
        else:
            st = mk_pill(pills, "Not run yet", FG_MUTED)
        st.pack(side="left")

    def _add(self):
        TaskDialog(self.app, None, self.app.save_task, parent=self)

    def _delete(self, t):
        if messagebox.askyesno("Delete task", f"Delete the task '{t['name']}'?", parent=self):
            self.app.delete_task(t["id"])

"""Tunnels window and the Add / Edit Tunnel dialog."""

from . import ui as _ui

# These windows share the main module's helpers, widgets and theme colours.
# apply_theme() in ui.py updates this module's colours too (see _THEMED).
globals().update({k: v for k, v in vars(_ui).items() if not k.startswith("__")})
_ui._THEMED.append(__import__(__name__, fromlist=["_"]))

class TunnelDialog(tk.Toplevel):
    """Add or edit one tunnel. Calls on_save(tunnel_dict)."""

    def __init__(self, parent, tunnel, used_ports, on_save):
        super().__init__(parent)
        self.withdraw()
        self.t, self.used_ports, self.on_save = dict(tunnel), used_ports, on_save
        self.title("Edit Tunnel" if tunnel.get("name") else "New Tunnel")
        self.configure(bg=BG)
        self.resizable(False, False)
        self.transient(parent)

        form = tk.Frame(self, bg=BG)
        form.pack(fill="both", expand=True, padx=8, pady=(12, 0))
        form.columnconfigure(1, weight=1)
        radio = dict(bg=BG, fg=FG, selectcolor=BG_INPUT, activebackground=BG,
                     activeforeground=FG, font=FONT)

        def lbl(text, r):
            w = mk_label(form, text, fg=FG_MUTED, font=FONT_SMALL)
            w.grid(row=r, column=0, sticky="e", padx=(10, 8), pady=5)
            return w

        lbl("Preset", 0)
        self.preset_var = tk.StringVar(value="Custom")
        cb = ttk.Combobox(form, textvariable=self.preset_var, state="readonly", width=22,
                          values=[p[0] for p in TUNNEL_PRESETS] + ["Custom"])
        cb.grid(row=0, column=1, sticky="w", padx=(0, 14), pady=5)
        cb.bind("<<ComboboxSelected>>", lambda e: self._apply_preset())

        lbl("Name", 1)
        self.e_name = mk_entry(form, width=30)
        self.e_name.grid(row=1, column=1, sticky="ew", padx=(0, 14), pady=5)
        self.e_name.insert(0, self.t.get("name", ""))

        lbl("Type", 2)
        self.type_var = tk.StringVar(value=self.t.get("type", "local"))
        tf = tk.Frame(form, bg=BG)
        tf.grid(row=2, column=1, sticky="w", pady=5)
        for value, text in (("local", "Web panel / database"), ("socks", "SOCKS proxy"),
                            ("remote", "Remote")):
            tk.Radiobutton(tf, text=text, variable=self.type_var, value=value,
                           command=self._type_change, **radio).pack(side="left", padx=(0, 10))

        # A remote tunnel uses the same three fields: the port it opens on the
        # server, then the target on this side (see _type_change)
        remote = self.t.get("type") == "remote"
        self.lbl_lport = lbl("Local port", 3)
        self.e_lport = mk_entry(form, width=8)
        self.e_lport.grid(row=3, column=1, sticky="w", padx=(0, 14), pady=5)
        self.e_lport.insert(0, str(self.t.get("remote_port" if remote else "local_port", 8080)))

        self.lbl_rhost = lbl("Target host", 4)
        self.e_rhost = mk_entry(form, width=30)
        self.e_rhost.grid(row=4, column=1, sticky="ew", padx=(0, 14), pady=5)
        self.e_rhost.insert(0, self.t.get("local_host" if remote else "remote_host", "127.0.0.1"))
        self.lbl_rport = lbl("Target port", 5)
        self.e_rport = mk_entry(form, width=8)
        self.e_rport.grid(row=5, column=1, sticky="w", padx=(0, 14), pady=5)
        self.e_rport.insert(0, str(self.t.get("local_port" if remote else "remote_port") or 80))

        self.note = tk.Label(form, text="", bg=BG, fg=FG_MUTED, font=("Segoe UI", 8),
                             justify="left", anchor="w", wraplength=360)
        self.note.grid(row=6, column=0, columnspan=2, sticky="w", padx=(10, 14), pady=(0, 6))

        self.auto_start = tk.BooleanVar(value=self.t.get("auto_start", False))
        tk.Checkbutton(form, text="Start when the app starts", variable=self.auto_start,
                       **radio).grid(row=7, column=1, sticky="w")
        self.auto_restart = tk.BooleanVar(value=self.t.get("auto_restart", True))
        tk.Checkbutton(form, text="Restart if it drops", variable=self.auto_restart,
                       **radio).grid(row=8, column=1, sticky="w")

        bar = tk.Frame(self, bg=BG)
        bar.pack(fill="x", padx=14, pady=14)
        mk_btn(bar, "Save", self._save, bg=ACCENT).pack(side="right", padx=(6, 0))
        mk_btn(bar, "Cancel", self.destroy).pack(side="right")
        self._type_change()
        self.minsize(420, 0)
        show_centered(self, parent)
        self.e_name.focus_set()

    def _apply_preset(self):
        for name, kind, lport, rport in TUNNEL_PRESETS:
            if name == self.preset_var.get():
                self.e_name.delete(0, "end")
                self.e_name.insert(0, name)
                self.type_var.set(kind)
                self.e_lport.delete(0, "end")
                self.e_lport.insert(0, str(lport))
                if rport:
                    self.e_rport.delete(0, "end")
                    self.e_rport.insert(0, str(rport))
                    self.e_rhost.delete(0, "end")
                    self.e_rhost.insert(0, "127.0.0.1")
                self._type_change()

    def _type_change(self):
        kind = self.type_var.get()
        socks = kind == "socks"
        for w in (self.lbl_rhost, self.e_rhost, self.lbl_rport, self.e_rport):
            w.grid_remove() if socks else w.grid()
        self.lbl_lport.configure(text="Server port" if kind == "remote" else "Local port")
        if kind == "remote":
            self.note.configure(text=(
                "The server opens this port (on its own 127.0.0.1) and passes connections "
                "to the target, as seen from this PC: 127.0.0.1 = this PC, or another "
                "machine on your network. Example: let the server reach a web project "
                "running on this PC."))
            return
        self.note.configure(text=(
            "Point your browser's proxy settings at SOCKS5 127.0.0.1 and the local "
            "port. Its traffic then goes out through the server."
            if socks else
            "Where the tunnel leads, as seen from the server. 127.0.0.1 = the server "
            "itself; or enter another machine the server can reach. Open it on this PC "
            "at 127.0.0.1 and the local port."))

    def _port(self, entry, label):
        try:
            v = int(entry.get().strip())
            if 1 <= v <= 65535:
                return v
        except ValueError:
            pass
        messagebox.showerror("Tunnel", f"{label} must be a number from 1 to 65535.",
                             parent=self)
        return None

    def _save(self):
        name = self.e_name.get().strip()
        if not name:
            messagebox.showerror("Tunnel", "Give the tunnel a name.", parent=self)
            return
        kind = self.type_var.get()
        if kind == "remote":
            sport = self._port(self.e_lport, "Server port")
            tport = self._port(self.e_rport, "Target port") if sport else None
            if tport is None:
                return
            self.t.pop("remote_host", None)
            self.t.update(name=name, type="remote", remote_port=sport, local_port=tport,
                          local_host=self.e_rhost.get().strip() or "127.0.0.1",
                          auto_start=self.auto_start.get(),
                          auto_restart=self.auto_restart.get())
            self.t.setdefault("id", str(int(time.time() * 1000)))
            self.destroy()
            self.on_save(self.t)
            return
        self.t.pop("local_host", None)
        lport = self._port(self.e_lport, "Local port")
        if lport is None:
            return
        if lport in self.used_ports:
            messagebox.showerror("Tunnel", f"Local port {lport} is already used by "
                                 "another tunnel.", parent=self)
            return
        self.t.update(name=name, type=self.type_var.get(), local_port=lport,
                      auto_start=self.auto_start.get(), auto_restart=self.auto_restart.get())
        if self.t["type"] == "local":
            rport = self._port(self.e_rport, "Target port")
            if rport is None:
                return
            self.t.update(remote_host=self.e_rhost.get().strip() or "127.0.0.1",
                          remote_port=rport)
        self.t.setdefault("id", str(int(time.time() * 1000)))
        self.destroy()
        self.on_save(self.t)

class TunnelsWindow(tk.Toplevel):
    """Lists one connection's tunnels with Start/Stop, Open, Edit and Delete."""

    def __init__(self, app, cid):
        super().__init__(app)
        self.withdraw()
        self.app, self.cid = app, cid
        conn = app.find_conn(cid)
        self.title(f"Tunnels - {conn['name']}")
        self.configure(bg=BG)
        # Same look as the Tasks window; not transient so it can be maximized
        self.protocol("WM_DELETE_WINDOW", self._close)

        top = tk.Frame(self, bg=BG)
        top.pack(fill="x", padx=14, pady=(10, 8))
        mk_label(top, "Tunnels", font=FONT_LARGE).pack(side="left")
        mk_label(top, _conn_label(conn), fg=FG_MUTED, font=FONT_SMALL).pack(
            side="left", padx=(10, 0), pady=(2, 0))
        self.lbl_sum = mk_label(top, "", fg=SUCCESS, font=FONT_SMALL)
        self.lbl_sum.pack(side="right", pady=(2, 0))

        outer = tk.Frame(self, bg=BG)
        outer.pack(fill="both", expand=True, padx=(14, 6))
        self.cv = tk.Canvas(outer, bg=BG, highlightthickness=0)
        sb = DarkScrollbar(outer, command=self.cv.yview)
        self.cv.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.cv.pack(side="left", fill="both", expand=True)
        self.rows = tk.Frame(self.cv, bg=BG)
        win = self.cv.create_window((0, 0), window=self.rows, anchor="nw")
        self.cv.bind("<Configure>", lambda e: self.cv.itemconfig(win, width=e.width - 6))
        self.rows.bind("<Configure>", lambda e: self.cv.configure(scrollregion=self.cv.bbox("all")))
        _bind_mousewheel(self.cv, lambda d: self.cv.yview_scroll(-1 * (d // 120), "units"),
                         container=outer)

        bar = tk.Frame(self, bg=BG_HEAD)
        bar.pack(fill="x", side="bottom")
        mk_btn(bar, "+ Add Tunnel", self._add, bg=SUCCESS).pack(side="left", padx=14, pady=7)
        mk_btn(bar, "Close", self._close).pack(side="right", padx=14, pady=7)
        self.minsize(560, 260)
        self.refresh()
        show_centered(self, app, size=(680, 360))
        self.grab_release()  # not modal: the main window stays usable

    def _close(self):
        self.app._tunnel_windows.pop(self.cid, None)
        self.destroy()

    ICONS = {"local": "→", "remote": "←", "socks": "⇄"}
    KINDS = {"local": "Local", "remote": "Remote", "socks": "SOCKS proxy"}

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
        conn = self.app.find_conn(self.cid)
        tunnels = conn.get("tunnels", []) if conn else []
        running = sum(self.app.tunnels.state(t["id"])[0] == "running" for t in tunnels)
        self.lbl_sum.configure(text=f"{running} running" if running else "")
        if not tunnels:
            box = tk.Frame(self.rows, bg=BG)
            box.pack(fill="x", pady=40)
            mk_label(box, "No tunnels yet", font=FONT_LARGE, fg=FG_MUTED).pack()
            mk_label(box, "Click + Add Tunnel to reach a web panel or database, or to "
                     "browse through this server.", fg=FG_MUTED, font=FONT_SMALL).pack(pady=(4, 0))
            return
        for t in tunnels:
            self._card(t)

    def _card(self, t):
        colors = {"running": SUCCESS, "starting": WARNING, "retrying": WARNING,
                  "error": DANGER, "stopped": FG_MUTED}
        state, msg = self.app.tunnels.state(t["id"])
        color = colors.get(state, FG_MUTED)
        active = state in TunnelManager.ACTIVE
        local = t["type"] == "local"
        db = local and t.get("remote_port") in DB_PORTS

        card = tk.Frame(self.rows, bg=BG_CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(0, 4))

        # Icon tile: tunnel direction, coloured by its state
        tile = tk.Canvas(card, width=28, height=28, bg=BG_CARD, highlightthickness=0)
        tile.pack(side="left", padx=(8, 10), pady=6, anchor="n")
        tile.create_image(14, 14, image=_tile_image(28, _blend(BG_CARD, color, 0.16),
                                                   _blend(BG_CARD, color, 0.6), radius=7))
        tile.create_text(14, 14, text=self.ICONS.get(t["type"], "→"), fill=color,
                         font=("Segoe UI", 11, "bold"))
        Tooltip(tile, self.KINDS.get(t["type"], ""))

        bf = tk.Frame(card, bg=BG_CARD)
        bf.pack(side="right", padx=8, pady=6, anchor="n")
        def small(b):
            b.configure(padx=8, pady=1, font=FONT_SMALL)
            b.pack(side="left", padx=2)
            return b
        if local:
            b = small(mk_btn(bf, "Copy" if db else "Open", lambda: self._open(t, db),
                             bg=SUCCESS if state == "running" else BG_INPUT))
            if state != "running":
                b.configure(state="disabled", fg=FG_MUTED)
        small(mk_btn(bf, "Stop" if active else "Start", lambda: self._toggle(t),
                     bg=DANGER if active else ACCENT))
        small(mk_btn(bf, "Edit", lambda: self._edit(t)))
        small(mk_btn(bf, "✕", lambda: self._delete(t)))

        info = tk.Frame(card, bg=BG_CARD)
        info.pack(side="left", fill="x", expand=True, pady=5)
        line = tk.Frame(info, bg=BG_CARD)
        line.pack(anchor="w")
        name = mk_label(line, t["name"], font=FONT_BOLD)
        name.pack(side="left")
        text = {"running": "Running", "starting": "Starting…", "retrying": "Retrying…",
                "error": "Error", "stopped": "Stopped"}.get(state, state.title())
        mk_pill(line, text, color).pack(side="left", padx=(10, 0))
        addr = mk_label(info, describe_tunnel(t), fg=FG_MUTED, font=FONT_SMALL, anchor="w")
        addr.pack(anchor="w")
        if msg and state in ("error", "retrying"):
            mk_label(info, msg, fg=color, font=FONT_SMALL, anchor="w", justify="left",
                     wraplength=360).pack(anchor="w")
        if local and state == "running":
            # Clicking the tunnel's text does the same as its Open / Copy button
            for w in (info, line, name, addr):
                w.configure(cursor="hand2")
                w.bind("<Button-1>", lambda e: self._open(t, db))
                w.bind("<Enter>", lambda e: addr.configure(fg=ACCENT))
                w.bind("<Leave>", lambda e: addr.configure(fg=FG_MUTED))

    def _used_ports(self, exclude_id=None):
        # Ports this app listens on here (a remote tunnel's port is on the server)
        return {t["local_port"] for c in self.app._connections
                for t in c.get("tunnels", [])
                if t.get("id") != exclude_id and t.get("type") != "remote"}

    def _toggle(self, t):
        if self.app.tunnels.is_active(t["id"]):
            self.app.tunnels.stop(t["id"])
        else:
            self.app.tunnels.start(self.app.find_conn(self.cid), t, self.app.log)
        self.refresh()

    def _open(self, t, db):
        addr = f"127.0.0.1:{t['local_port']}"
        if db:
            self.clipboard_clear()
            self.clipboard_append(addr)
            self.app.log(f"[TUNNEL] Copied {addr} for your database client.\n")
        else:
            import webbrowser
            webbrowser.open(f"http://{addr}")

    def _add(self):
        TunnelDialog(self, {}, self._used_ports(), self._saved)

    def _edit(self, t):
        TunnelDialog(self, t, self._used_ports(t["id"]), self._saved)

    def _saved(self, t):
        conn = self.app.find_conn(self.cid)
        tunnels = conn.setdefault("tunnels", [])
        for i, old in enumerate(tunnels):
            if old["id"] == t["id"]:
                if self.app.tunnels.is_active(t["id"]):  # apply the new settings
                    self.app.tunnels.stop(t["id"])
                    self.app.after(1500, lambda: self.app.tunnels.start(conn, t, self.app.log))
                tunnels[i] = t
                break
        else:
            tunnels.append(t)
        save_connections(self.app._connections)
        self.refresh()

    def _delete(self, t):
        if not messagebox.askyesno("Delete tunnel", f"Delete '{t['name']}'?", parent=self):
            return
        self.app.tunnels.forget(t["id"])
        conn = self.app.find_conn(self.cid)
        conn["tunnels"] = [x for x in conn.get("tunnels", []) if x["id"] != t["id"]]
        save_connections(self.app._connections)
        self.refresh()
        self.app.on_tunnel_change(self.cid, t["id"])

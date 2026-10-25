"""
Fusion Editor - Roblox FastFlag & Font editor (no injection)

It only edits files on disk:
  - <Roblox version folder>/ClientSettings/ClientAppSettings.json   (FastFlags)
  - <Roblox version folder>/content/fonts/*                         (Fonts, with backup)

It never touches the running Roblox process. Windows only.
Roblox updates create a new version folder, so reopen this tool and press
"Save & Apply" (or "Apply font") again after each Roblox update.

Run:  python fusion_editor.py
"""

import ctypes
import json
import os
import re
import shutil
import subprocess
import tkinter as tk
from pathlib import Path
from tkinter import ttk, filedialog, messagebox

APP_NAME = "Fusion Editor"
DATA_DIR = Path.home() / ".fusion_editor"
SETTINGS_FILE = DATA_DIR / "settings.json"
BACKUP_ROOT = DATA_DIR / "font_backups"
LEGACY_BACKUP_ROOT = Path.home() / ".flag_manager" / "font_backups"  # from the first version

# Gives the app its own taskbar icon instead of grouping under python.exe
try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("FusionEditor.App")
except Exception:
    pass

# ---------- themes (buttons are monochrome and follow the theme) ----------
THEMES = {
    "dark": {
        "bg": "#16171a", "side": "#0e0f11", "panel": "#212328", "line": "#33363d",
        "fg": "#f2f2f2", "muted": "#8d929b", "btn": "#f2f2f2", "btn_fg": "#111111",
        "btn_hover": "#c9c9c9", "sel": "#3b3e46", "sel_fg": "#ffffff", "err": "#ff8a8a",
    },
    "light": {
        "bg": "#f3f3f4", "side": "#e4e4e6", "panel": "#ffffff", "line": "#cfd0d4",
        "fg": "#111111", "muted": "#6b7078", "btn": "#111111", "btn_fg": "#ffffff",
        "btn_hover": "#3d3d3d", "sel": "#d5d6da", "sel_fg": "#000000", "err": "#b00020",
    },
}

DEFAULT_SETTINGS = {"theme": "dark", "after_launch": "stay"}  # after_launch: stay | hide | close


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        s.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    if s.get("theme") not in THEMES:
        s["theme"] = "dark"
    if s.get("after_launch") not in ("stay", "hide", "close"):
        s["after_launch"] = "stay"
    return s


def save_settings(s):
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(json.dumps(s, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------- FastFlag validation ----------
NAME_RE = re.compile(
    r"^(?:FFlag|DFFlag|SFFlag|FInt|DFInt|SFInt|FLog|DFLog|FString|DFString|SFString)[A-Za-z0-9_]+$"
)
NAME_HELP = ("Name must start with FFlag, DFFlag, SFFlag, FInt, DFInt, SFInt, "
             "FLog, DFLog, FString, DFString or SFString, followed by letters, numbers or _")


def flag_kind(name):
    """Which value type a flag name expects: 'bool', 'int', 'str' or None (invalid name)."""
    if not NAME_RE.match(name):
        return None
    if name.startswith(("FFlag", "DFFlag", "SFFlag")):
        return "bool"
    if name.startswith(("FInt", "DFInt", "SFInt", "FLog", "DFLog")):
        return "int"
    return "str"


def validate_flag(name, value):
    """Returns (True, clean_value) or (False, error_message)."""
    name = str(name).strip()
    kind = flag_kind(name)
    if kind is None:
        return False, NAME_HELP
    if kind == "bool":
        if isinstance(value, bool):
            return True, value
        s = str(value).strip().lower()
        if s in ("true", "false"):
            return True, s == "true"
        return False, f"{name} needs the value true or false."
    if kind == "int":
        if isinstance(value, bool):
            return False, f"{name} needs a whole number."
        try:
            return True, int(str(value).strip())
        except ValueError:
            return False, f"{name} needs a whole number (like 60 or -1)."
    text = str(value)
    if not text.strip():
        return False, f"{name} needs a text value."
    return True, text


def find_latest_version():
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    root = Path(base) / "Roblox" / "Versions"
    if not root.exists():
        return None
    dirs = [d for d in root.iterdir() if d.is_dir() and (d / "RobloxPlayerBeta.exe").exists()]
    return max(dirs, key=lambda d: d.stat().st_mtime) if dirs else None


# ---------- dialogs ----------
def center_over(win, parent):
    win.update_idletasks()
    x = parent.winfo_x() + (parent.winfo_width() - win.winfo_width()) // 2
    y = parent.winfo_y() + (parent.winfo_height() - win.winfo_height()) // 2
    win.geometry(f"+{max(x, 0)}+{max(y, 0)}")


class FlagDialog(tk.Toplevel):
    """Add a new flag, or edit the value of an existing one."""

    def __init__(self, app, name="", value="", edit=False):
        super().__init__(app)
        t = app.t
        self.result = None
        self.title("Edit Flag" if edit else "Add Flag")
        self.configure(bg=t["bg"], padx=22, pady=18)
        self.resizable(False, False)
        self.transient(app)

        tk.Label(self, text="Flag name", bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x")
        self.name_var = tk.StringVar(value=name)
        self.name_entry = ttk.Entry(self, textvariable=self.name_var, width=52)
        self.name_entry.pack(fill="x", pady=(4, 10))

        tk.Label(self, text="Value", bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x")
        self.val_var = tk.StringVar(value=str(value).lower() if isinstance(value, bool) else str(value))
        self.val_entry = ttk.Entry(self, textvariable=self.val_var, width=52)
        self.val_entry.pack(fill="x", pady=(4, 8))

        self.hint = tk.Label(self, text="", bg=t["bg"], fg=t["muted"], anchor="w",
                             justify="left", wraplength=380, font=("Segoe UI", 9))
        self.hint.pack(fill="x")
        self.err = tk.Label(self, text="", bg=t["bg"], fg=t["err"], anchor="w",
                            justify="left", wraplength=380, font=("Segoe UI", 9))
        self.err.pack(fill="x", pady=(4, 0))

        row = tk.Frame(self, bg=t["bg"])
        row.pack(fill="x", pady=(14, 0))
        ttk.Button(row, text="Save" if edit else "Add", style="Primary.TButton",
                   command=self.submit).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=8)

        self.name_var.trace_add("write", self._update_hint)
        self._update_hint()
        if edit:
            self.name_entry.state(["readonly"])
        self.bind("<Return>", lambda e: self.submit())
        self.bind("<Escape>", lambda e: self.destroy())

        center_over(self, app)
        self.grab_set()
        (self.val_entry if edit else self.name_entry).focus_set()

    def _update_hint(self, *_):
        kind = flag_kind(self.name_var.get().strip())
        self.hint.config(text={
            "bool": "Value type: true or false",
            "int": "Value type: whole number",
            "str": "Value type: text",
            None: NAME_HELP,
        }[kind])

    def submit(self):
        ok, res = validate_flag(self.name_var.get(), self.val_var.get())
        if not ok:
            self.err.config(text=res)
            return
        self.result = (self.name_var.get().strip(), res)
        self.destroy()


class ImportDialog(tk.Toplevel):
    """Paste (or open) a JSON object of flags. Valid ones are added, invalid ones are rejected."""

    def __init__(self, app):
        super().__init__(app)
        t = app.t
        self.result = None
        self.title("Import Flags (JSON)")
        self.configure(bg=t["bg"], padx=22, pady=18)
        self.transient(app)

        tk.Label(self, text='Paste flags as JSON, e.g. {"DFIntTaskSchedulerTargetFps": 144}',
                 bg=t["bg"], fg=t["fg"], anchor="w").pack(fill="x")
        self.text = tk.Text(self, width=70, height=14, bg=t["panel"], fg=t["fg"],
                            insertbackground=t["fg"], relief="flat", padx=8, pady=8,
                            highlightthickness=1, highlightbackground=t["line"],
                            highlightcolor=t["line"], font=("Consolas", 10))
        self.text.pack(fill="both", expand=True, pady=(8, 8))
        self.err = tk.Label(self, text="", bg=t["bg"], fg=t["err"], anchor="w")
        self.err.pack(fill="x")

        row = tk.Frame(self, bg=t["bg"])
        row.pack(fill="x", pady=(10, 0))
        ttk.Button(row, text="Open file...", command=self.open_file).pack(side="left")
        ttk.Button(row, text="Import", style="Primary.TButton", command=self.submit).pack(side="right")
        ttk.Button(row, text="Cancel", command=self.destroy).pack(side="right", padx=8)

        self.bind("<Escape>", lambda e: self.destroy())
        center_over(self, app)
        self.grab_set()
        self.text.focus_set()

    def open_file(self):
        f = filedialog.askopenfilename(parent=self, filetypes=[("JSON", "*.json"), ("All files", "*.*")])
        if f:
            try:
                self.text.delete("1.0", "end")
                self.text.insert("1.0", Path(f).read_text(encoding="utf-8"))
            except Exception as e:
                self.err.config(text=f"Could not read file: {e}")

    def submit(self):
        try:
            data = json.loads(self.text.get("1.0", "end"))
        except json.JSONDecodeError as e:
            self.err.config(text=f"That is not valid JSON: {e}")
            return
        if not isinstance(data, dict) or not data:
            self.err.config(text="Expected a JSON object like {\"FFlagName\": true}.")
            return
        accepted, rejected = {}, []
        for name, value in data.items():
            ok, res = validate_flag(name, value)
            if ok:
                accepted[str(name).strip()] = res
            else:
                rejected.append(f"{name}: {res}")
        self.result = (accepted, rejected)
        self.destroy()


# ---------- main app ----------
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        self.geometry("960x640")
        self.minsize(860, 580)

        self.settings = load_settings()
        self.t = THEMES[self.settings["theme"]]
        self.flags = {}
        self.version_dir = None
        self.font_path = None
        self.current_page = "flags"
        self._anchor = None

        # window / taskbar icon + in-app logos (embedded at the bottom of this file)
        self._icon = tk.PhotoImage(data=ICON_B64)
        try:
            self.iconphoto(True, self._icon)
        except tk.TclError:
            pass
        self._logos = {"dark": tk.PhotoImage(data=LOGO_DARK_B64),
                       "light": tk.PhotoImage(data=LOGO_LIGHT_B64)}

        self.rebuild()
        self.refresh_version()
        self.load_flags()

    # ----- theme / layout -----
    def rebuild(self):
        for w in self.winfo_children():
            w.destroy()
        self.t = THEMES[self.settings["theme"]]
        self.configure(bg=self.t["bg"])
        self._style()
        self._build_layout()
        self.refresh_tree()
        self._update_status()
        self.show_page(self.current_page)

    def _style(self):
        t = self.t
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure(".", background=t["bg"], foreground=t["fg"])
        s.configure("Treeview", background=t["panel"], fieldbackground=t["panel"],
                    foreground=t["fg"], rowheight=28, borderwidth=0, relief="flat")
        s.map("Treeview", background=[("selected", t["sel"])], foreground=[("selected", t["sel_fg"])])
        s.configure("Treeview.Heading", background=t["side"], foreground=t["fg"],
                    borderwidth=0, relief="flat", padding=6)
        s.map("Treeview.Heading", background=[("active", t["side"])])
        s.configure("TButton", background=t["panel"], foreground=t["fg"], bordercolor=t["line"],
                    lightcolor=t["panel"], darkcolor=t["panel"], focuscolor=t["panel"],
                    padding=(12, 7), relief="flat")
        s.map("TButton", background=[("active", t["line"])], foreground=[("active", t["fg"])])
        s.configure("Primary.TButton", background=t["btn"], foreground=t["btn_fg"],
                    bordercolor=t["btn"], lightcolor=t["btn"], darkcolor=t["btn"],
                    focuscolor=t["btn"], padding=(12, 8))
        s.map("Primary.TButton", background=[("active", t["btn_hover"])],
              foreground=[("active", t["btn_fg"])])
        s.configure("TEntry", fieldbackground=t["panel"], foreground=t["fg"], insertcolor=t["fg"],
                    bordercolor=t["line"], lightcolor=t["line"], darkcolor=t["line"], padding=6)
        s.map("TEntry", fieldbackground=[("readonly", t["panel"])], foreground=[("readonly", t["muted"])])
        s.configure("TRadiobutton", background=t["bg"], foreground=t["fg"],
                    indicatorcolor=t["panel"], focuscolor=t["bg"], padding=4)
        s.map("TRadiobutton", background=[("active", t["bg"])], indicatorcolor=[("selected", t["fg"])])
        s.configure("Vertical.TScrollbar", background=t["line"], troughcolor=t["bg"],
                    bordercolor=t["bg"], arrowcolor=t["fg"], lightcolor=t["line"], darkcolor=t["line"])

    def _build_layout(self):
        t = self.t
        self.sidebar = tk.Frame(self, bg=t["side"], width=210)
        self.sidebar.pack(side="left", fill="y")
        self.sidebar.pack_propagate(False)

        tk.Label(self.sidebar, image=self._logos[self.settings["theme"]], bg=t["side"]).pack(pady=(22, 2))
        tk.Label(self.sidebar, text="E D I T O R", bg=t["side"], fg=t["muted"],
                 font=("Segoe UI", 9, "bold")).pack(pady=(0, 20))

        self.nav = {}
        for key, label in (("flags", "Edit Flags"), ("fonts", "Fonts"), ("settings", "Settings")):
            row = tk.Frame(self.sidebar, bg=t["side"], cursor="hand2")
            row.pack(fill="x", pady=1)
            bar = tk.Frame(row, bg=t["side"], width=4)
            bar.pack(side="left", fill="y")
            lbl = tk.Label(row, text=label, bg=t["side"], fg=t["fg"], anchor="w",
                           font=("Segoe UI", 11), padx=16, pady=11)
            lbl.pack(side="left", fill="x", expand=True)
            for w in (row, lbl):
                w.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            self.nav[key] = (row, bar, lbl)

        # bottom of the sidebar: status, then the launch button above it
        self.status = tk.Label(self.sidebar, text="", bg=t["side"], fg=t["muted"],
                               justify="left", anchor="w", font=("Segoe UI", 8))
        self.status.pack(side="bottom", fill="x", padx=16, pady=(0, 14))
        ttk.Button(self.sidebar, text="\u25B6  Launch Roblox", style="Primary.TButton",
                   command=self.launch_roblox).pack(side="bottom", fill="x", padx=14, pady=(0, 12))

        self.container = tk.Frame(self, bg=t["bg"])
        self.container.pack(side="left", fill="both", expand=True)
        self.pages = {
            "flags": self._build_flags_page(),
            "fonts": self._build_fonts_page(),
            "settings": self._build_settings_page(),
        }

    def show_page(self, key):
        t = self.t
        self.current_page = key
        for k, page in self.pages.items():
            page.pack_forget()
            row, bar, lbl = self.nav[k]
            for w in (row, lbl):
                w.configure(bg=t["side"])
            bar.configure(bg=t["side"])
        self.pages[key].pack(fill="both", expand=True, padx=24, pady=22)
        row, bar, lbl = self.nav[key]
        for w in (row, lbl):
            w.configure(bg=t["panel"])
        bar.configure(bg=t["fg"])

    def _title(self, parent, text):
        return tk.Label(parent, text=text, bg=self.t["bg"], fg=self.t["fg"], font=("Segoe UI", 18, "bold"))

    def _muted(self, parent, text):
        return tk.Label(parent, text=text, bg=self.t["bg"], fg=self.t["muted"],
                        font=("Segoe UI", 9), justify="left", anchor="w")

    # ----- Roblox detection / launch -----
    def refresh_version(self):
        self.version_dir = find_latest_version()
        self._update_status()

    def _update_status(self):
        if not hasattr(self, "status") or not self.status.winfo_exists():
            return
        if self.version_dir:
            self.status.config(text=f"Roblox detected\n{self.version_dir.name}")
        else:
            self.status.config(text="Roblox not found")

    def launch_roblox(self):
        launched = False
        for uri in ("roblox-player:1+launchmode:app", "roblox://"):
            try:
                os.startfile(uri)  # Windows: opens through Roblox's registered protocol
                launched = True
                break
            except Exception:
                continue
        if not launched:
            self.refresh_version()
            exe = self.version_dir / "RobloxPlayerBeta.exe" if self.version_dir else None
            if exe and exe.exists():
                try:
                    subprocess.Popen([str(exe), "--app"], cwd=str(exe.parent))
                    launched = True
                except Exception:
                    pass
        if not launched:
            messagebox.showerror(APP_NAME, "Could not launch Roblox. Is it installed?")
            return
        mode = self.settings["after_launch"]
        if mode == "hide":
            self.iconify()
        elif mode == "close":
            self.after(1500, self.destroy)

    # ================= FLAGS PAGE =================
    def _build_flags_page(self):
        t = self.t
        page = tk.Frame(self.container, bg=t["bg"])

        head = tk.Frame(page, bg=t["bg"])
        head.pack(fill="x")
        self._title(head, "Edit Flags").pack(side="left")
        self.count_lbl = tk.Label(head, text="", bg=t["bg"], fg=t["muted"])
        self.count_lbl.pack(side="right")
        self._muted(page, "Hold the left mouse button and drag to select several flags. "
                          "Double-click a flag to edit it.").pack(fill="x", pady=(2, 10))

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh_tree())
        ttk.Entry(page, textvariable=self.search_var).pack(fill="x", pady=(0, 8))

        wrap = tk.Frame(page, bg=t["line"], padx=1, pady=1)
        wrap.pack(fill="both", expand=True)
        inner = tk.Frame(wrap, bg=t["panel"])
        inner.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(inner, columns=("name", "value"), show="headings", selectmode="extended")
        self.tree.heading("name", text="Flag name", anchor="w")
        self.tree.heading("value", text="Value", anchor="w")
        self.tree.column("name", width=420, anchor="w")
        self.tree.column("value", width=160, anchor="w")
        sb = ttk.Scrollbar(inner, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

        self.tree.bind("<ButtonPress-1>", self._on_press)
        self.tree.bind("<B1-Motion>", self._on_drag)
        self.tree.bind("<Double-1>", lambda e: self.edit_flag())
        self.tree.bind("<Control-a>", self._select_all)
        self.tree.bind("<Delete>", lambda e: self.delete_flags())

        row1 = tk.Frame(page, bg=t["bg"])
        row1.pack(fill="x", pady=(10, 6))
        ttk.Button(row1, text="+  Add Flag", command=self.add_flag).pack(side="left")
        ttk.Button(row1, text="Import JSON", command=self.import_flags).pack(side="left", padx=6)
        ttk.Button(row1, text="Edit", command=self.edit_flag).pack(side="left")
        ttk.Button(row1, text="Delete", command=self.delete_flags).pack(side="left", padx=6)

        row2 = tk.Frame(page, bg=t["bg"])
        row2.pack(fill="x")
        ttk.Button(row2, text="Save & Apply", style="Primary.TButton",
                   command=self.save_flags).pack(side="left")
        ttk.Button(row2, text="Reload", command=self.reload_flags).pack(side="left", padx=6)
        ttk.Button(row2, text="Reset (delete all flags)", command=self.reset_flags).pack(side="left")
        return page

    # --- drag-to-select (hold left click and drag over rows) ---
    def _on_press(self, e):
        if self.tree.identify_region(e.x, e.y) != "cell":
            self._anchor = None
            return
        if e.state & 0x0005:  # Shift or Ctrl held: use the normal click behaviour
            self._anchor = None
            return
        self.tree.focus_set()
        row = self.tree.identify_row(e.y)
        self._anchor = row or None
        if row:
            self.tree.selection_set(row)
            self.tree.focus(row)
        else:
            self.tree.selection_set(())
        return "break"

    def _on_drag(self, e):
        if not self._anchor or not self.tree.exists(self._anchor):
            return
        h = self.tree.winfo_height()
        if e.y < 30:
            self.tree.yview_scroll(-1, "units")
            self.tree.update_idletasks()
            row = self.tree.identify_row(30)
        elif e.y > h - 4:
            self.tree.yview_scroll(1, "units")
            self.tree.update_idletasks()
            row = self.tree.identify_row(h - 4)
        else:
            row = self.tree.identify_row(e.y)
        if not row:
            return "break"
        kids = self.tree.get_children()
        i, j = kids.index(self._anchor), kids.index(row)
        lo, hi = sorted((i, j))
        self.tree.selection_set(kids[lo:hi + 1])
        return "break"

    def _select_all(self, e=None):
        self.tree.selection_set(self.tree.get_children())
        return "break"

    # --- flag data ---
    def settings_path(self):
        if not self.version_dir:
            return None
        return self.version_dir / "ClientSettings" / "ClientAppSettings.json"

    def load_flags(self):
        self.flags = {}
        skipped = []
        p = self.settings_path()
        if p and p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("file is not a JSON object")
                for name, value in data.items():
                    ok, res = validate_flag(name, value)
                    if ok:
                        self.flags[str(name).strip()] = res
                    else:
                        skipped.append(str(name))
            except Exception as e:
                messagebox.showwarning(APP_NAME, f"Could not read the settings file:\n{e}\n\nStarting empty.")
        self.refresh_tree()
        if skipped:
            shown = "\n".join(skipped[:10]) + ("\n..." if len(skipped) > 10 else "")
            messagebox.showwarning(
                APP_NAME,
                f"{len(skipped)} invalid flag(s) in the existing file were skipped "
                f"(they are removed if you save):\n\n{shown}")

    def reload_flags(self):
        self.refresh_version()
        self.load_flags()

    def refresh_tree(self, select=None):
        if not hasattr(self, "tree") or not self.tree.winfo_exists():
            return
        self.tree.delete(*self.tree.get_children())
        q = self.search_var.get().strip().lower()
        for k in sorted(self.flags):
            if q in k.lower():
                v = self.flags[k]
                self.tree.insert("", "end", iid=k, values=(k, str(v).lower() if isinstance(v, bool) else v))
        if select:
            ids = [s for s in select if self.tree.exists(s)]
            if ids:
                self.tree.selection_set(ids)
                self.tree.see(ids[0])
        n = len(self.flags)
        self.count_lbl.config(text=f"{n} flag{'s' if n != 1 else ''}")

    def add_flag(self):
        dlg = FlagDialog(self)
        self.wait_window(dlg)
        if dlg.result:
            name, value = dlg.result
            self.flags[name] = value
            self.search_var.set("")
            self.refresh_tree(select=[name])

    def edit_flag(self):
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showinfo(APP_NAME, "Select exactly one flag to edit.")
            return
        name = sel[0]
        dlg = FlagDialog(self, name=name, value=self.flags[name], edit=True)
        self.wait_window(dlg)
        if dlg.result:
            self.flags[name] = dlg.result[1]
            self.refresh_tree(select=[name])

    def delete_flags(self):
        sel = self.tree.selection()
        if not sel:
            return
        for name in sel:
            self.flags.pop(name, None)
        self.refresh_tree()

    def import_flags(self):
        dlg = ImportDialog(self)
        self.wait_window(dlg)
        if not dlg.result:
            return
        accepted, rejected = dlg.result
        self.flags.update(accepted)
        self.refresh_tree(select=list(accepted))
        if rejected:
            shown = "\n".join(rejected[:12]) + ("\n..." if len(rejected) > 12 else "")
            messagebox.showwarning(
                APP_NAME, f"Added {len(accepted)} flag(s). Rejected {len(rejected)} invalid flag(s):\n\n{shown}")
        else:
            messagebox.showinfo(APP_NAME, f"Added {len(accepted)} flag(s).")

    def save_flags(self):
        self.refresh_version()
        p = self.settings_path()
        if not p:
            messagebox.showerror(APP_NAME, "Roblox installation not found. Install Roblox, then press Reload.")
            return
        bad = [n for n, v in self.flags.items() if not validate_flag(n, v)[0]]
        if bad:
            messagebox.showerror(APP_NAME, "Invalid flags, nothing was saved:\n\n" + "\n".join(bad[:10]))
            return
        p.parent.mkdir(exist_ok=True)
        p.write_text(json.dumps(self.flags, indent=2), encoding="utf-8")
        messagebox.showinfo(APP_NAME, f"Saved {len(self.flags)} flag(s).\nRestart Roblox for them to apply.")

    def reset_flags(self):
        if messagebox.askyesno(APP_NAME, "Remove all flags and delete the settings file?"):
            self.flags = {}
            p = self.settings_path()
            if p and p.exists():
                p.unlink()
            self.refresh_tree()

    # ================= FONTS PAGE =================
    def _build_fonts_page(self):
        t = self.t
        page = tk.Frame(self.container, bg=t["bg"])
        self._title(page, "Fonts").pack(anchor="w")
        self._muted(page, "Replaces Roblox's fonts with the font you choose. "
                          "Originals are backed up so you can restore them.").pack(fill="x", pady=(2, 0))

        box = tk.Frame(page, bg=t["panel"], highlightbackground=t["line"], highlightthickness=1)
        box.pack(fill="x", pady=(18, 12))
        self.font_label = tk.Label(box, text="", bg=t["panel"], anchor="w", padx=14, pady=12,
                                   font=("Segoe UI", 11))
        self.font_label.pack(side="left", fill="x", expand=True)
        ttk.Button(box, text="Choose font...", command=self.choose_font).pack(side="right", padx=8, pady=6)
        self._update_font_label()

        row = tk.Frame(page, bg=t["bg"])
        row.pack(fill="x")
        ttk.Button(row, text="Apply font", style="Primary.TButton", command=self.apply_font).pack(side="left")
        ttk.Button(row, text="Restore originals", command=self.restore_fonts).pack(side="left", padx=8)
        return page

    def _update_font_label(self):
        if self.font_path:
            self.font_label.config(text=self.font_path.name, fg=self.t["fg"])
        else:
            self.font_label.config(text="No font selected", fg=self.t["muted"])

    def fonts_dir(self):
        return self.version_dir / "content" / "fonts" if self.version_dir else None

    def backup_dir(self):
        name = self.version_dir.name
        old = LEGACY_BACKUP_ROOT / name
        return old if old.exists() else BACKUP_ROOT / name

    def choose_font(self):
        f = filedialog.askopenfilename(title="Choose a font", filetypes=[("Fonts", "*.ttf *.otf")])
        if f:
            self.font_path = Path(f)
            self._update_font_label()

    def apply_font(self):
        self.refresh_version()
        d = self.fonts_dir()
        if not d or not d.exists():
            messagebox.showerror(APP_NAME, "Roblox fonts folder not found. Is Roblox installed?")
            return
        if not self.font_path or not self.font_path.is_file():
            messagebox.showerror(APP_NAME, "Choose a .ttf or .otf font first.")
            return
        files = [f for f in d.iterdir() if f.is_file() and f.suffix.lower() in (".ttf", ".otf")]
        if not files:
            messagebox.showerror(APP_NAME, "No font files found in Roblox's fonts folder.")
            return
        if not messagebox.askyesno(APP_NAME, f"Replace {len(files)} Roblox font file(s) with "
                                             f"'{self.font_path.name}'?\n\nOriginals are backed up."):
            return
        bk = self.backup_dir()
        bk.mkdir(parents=True, exist_ok=True)
        try:
            for f in files:
                if not (bk / f.name).exists():  # keep the very first (original) copy
                    shutil.copy2(f, bk / f.name)
                shutil.copyfile(self.font_path, f)
        except PermissionError:
            messagebox.showerror(APP_NAME, "Permission denied. Close Roblox and try again.")
            return
        messagebox.showinfo(APP_NAME, "Font applied. Restart Roblox.")

    def restore_fonts(self):
        self.refresh_version()
        d = self.fonts_dir()
        if not d or not d.exists():
            messagebox.showerror(APP_NAME, "Roblox fonts folder not found.")
            return
        bk = self.backup_dir()
        if not bk.exists():
            messagebox.showinfo(APP_NAME, "No backups found for this Roblox version.")
            return
        try:
            for f in bk.iterdir():
                shutil.copyfile(f, d / f.name)
        except PermissionError:
            messagebox.showerror(APP_NAME, "Permission denied. Close Roblox and try again.")
            return
        messagebox.showinfo(APP_NAME, "Original fonts restored.")

    # ================= SETTINGS PAGE =================
    def _build_settings_page(self):
        t = self.t
        page = tk.Frame(self.container, bg=t["bg"])
        self._title(page, "Settings").pack(anchor="w")

        def section(text):
            tk.Label(page, text=text, bg=t["bg"], fg=t["fg"],
                     font=("Segoe UI", 11, "bold")).pack(anchor="w", pady=(22, 4))

        section("After launching Roblox")
        self.after_var = tk.StringVar(value=self.settings["after_launch"])
        for value, label in (("stay", "Keep Fusion Editor open"),
                             ("hide", "Hide Fusion Editor (minimize to the taskbar)"),
                             ("close", "Close Fusion Editor")):
            ttk.Radiobutton(page, text=label, value=value, variable=self.after_var,
                            command=self.on_after_change).pack(anchor="w")

        section("Appearance")
        self.theme_var = tk.StringVar(value=self.settings["theme"])
        for value, label in (("dark", "Dark mode"), ("light", "Light mode")):
            ttk.Radiobutton(page, text=label, value=value, variable=self.theme_var,
                            command=self.on_theme_change).pack(anchor="w")
        self._muted(page, "Buttons are monochrome and switch between white and black with the theme."
                    ).pack(anchor="w", pady=(8, 0))
        return page

    def on_after_change(self):
        self.settings["after_launch"] = self.after_var.get()
        save_settings(self.settings)

    def on_theme_change(self):
        self.settings["theme"] = self.theme_var.get()
        save_settings(self.settings)
        self.after(20, self.rebuild)


# ---------- embedded logo images (generated from your Fusion logo) ----------
ICON_B64 = (
    "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"
    "nxAg5P8HffebXpiUdp8AAAAASUVORK5CYII="
)

LOGO_DARK_B64 = (
    "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"
    "Ufv4PzlGCe10jOM6AAAAAElFTkSuQmCC"
)

LOGO_LIGHT_B64 = (
    "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"
    "RK5CYII="
)


if __name__ == "__main__":
    App().mainloop()
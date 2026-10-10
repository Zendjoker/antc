"""Small desktop window for the ZendAgent launcher."""

from __future__ import annotations

import os
import threading
import tkinter as tk
from pathlib import Path
from tkinter import scrolledtext

from launcher.model import LABELS, close_action
from launcher.redact import redact
from launcher.shortcut import install, png_to_ico
from launcher.supervisor import DASHBOARD_PORT, ROLES, Supervisor

BG = "#F5FAFF"
CARD = "#FFFFFF"
INK = "#101828"
MUTED = "#5E6B7E"
LINE = "#E3EAF4"
BLUE = "#0866F5"
CYAN = "#00E5F1"
SOFT = "#EAF2FF"

DOT = {
    "running": "#079455",
    "starting": "#009DFF",
    "stopping": "#009DFF",
    "stopped": "#98A2B3",
    "failed": "#D92D20",
    "external": "#0646DA",
    "unknown": "#C01048",
}


class LauncherApp:
    def __init__(self, root_dir: Path, screenshot: str | None = None, autostart: bool = True):
        self.root_dir = Path(root_dir)
        self.screenshot = screenshot
        self._autostart = autostart and not screenshot
        self.supervisor = Supervisor(self.root_dir)
        self._shots = []
        self.rows = {}
        self.tray = None
        self._probing = False
        self._lock = _instance_lock(self.root_dir)
        self._prepare_icon()
        self._build()
        self._start_tray()
        self.root.after(200, self._probe)
        if screenshot:
            self._capture_waits = 0
            self.root.after(400, self._capture_and_close)

    def _prepare_icon(self) -> None:
        logo = self.root_dir / "UI" / "brand" / "zend-favicon.png"
        icon = self.root_dir / "launcher" / "zendagent.ico"
        if logo.is_file() and not icon.is_file():
            try:
                png_to_ico(logo, icon)
            except (OSError, ValueError):
                return

    def _build(self) -> None:
        root = tk.Tk()
        self.root = root
        root.title("ZendAgent")
        root.configure(bg=BG)
        root.geometry("500x940")
        root.minsize(460, 860)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        try:
            root.iconbitmap(str(self.root_dir / "launcher" / "zendagent.ico"))
        except tk.TclError:
            pass

        accent = tk.Frame(root, bg=BLUE, height=3)
        accent.pack(fill="x")

        header = tk.Frame(root, bg=BG)
        header.pack(fill="x", padx=22, pady=(18, 8))
        logo = self.root_dir / "UI" / "brand" / "zend-logo.png"
        if logo.is_file():
            try:
                image = tk.PhotoImage(file=str(logo))
                if image.width() > 48:
                    image = image.subsample(max(1, image.width() // 36))
                self._shots.append(image)
                tk.Label(header, image=image, bg=BG).pack(side="left", padx=(0, 12))
            except tk.TclError:
                pass
        titles = tk.Frame(header, bg=BG)
        titles.pack(side="left", fill="x")
        tk.Label(titles, text="ZendAgent", bg=BG, fg=INK, font=("Segoe UI", 20, "bold")).pack(anchor="w")
        tk.Label(titles, text="Desktop launcher", bg=BG, fg=MUTED, font=("Segoe UI", 10)).pack(anchor="w")

        body = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        body.pack(fill="both", expand=True, padx=18, pady=8)

        mode_row = tk.Frame(body, bg=CARD)
        mode_row.pack(fill="x", padx=16, pady=(14, 4))
        tk.Label(mode_row, text="FRONTEND", bg=CARD, fg=MUTED, font=("Segoe UI", 8, "bold")).pack(anchor="w")
        switch = tk.Frame(mode_row, bg=SOFT)
        switch.pack(anchor="w", pady=(6, 0))
        self.classic_btn = self._mode_button(switch, "Classic", "classic")
        self.next_btn = self._mode_button(switch, "Next.js dev", "next")
        self.production_btn = self._mode_button(switch, "Production", "production")
        self.mode_label = tk.Label(body, bg=CARD, fg=INK, font=("Segoe UI", 10), wraplength=390, justify="left")
        self.mode_label.pack(anchor="w", padx=16, pady=(8, 4))

        for name in ("backend", "dashboard", "frontend"):
            self.rows[name] = self._status_row(body, name)

        self.port_label = tk.Label(body, bg=CARD, fg=MUTED, font=("Segoe UI", 8), wraplength=390, justify="left")
        self.port_label.pack(anchor="w", padx=16, pady=(4, 8))

        actions = tk.Frame(root, bg=BG)
        actions.pack(fill="x", padx=18, pady=(4, 0))
        self._wide(actions, "Start all", self.start_all, primary=True).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._wide(actions, "Restart all", self.restart_all, primary=True).pack(side="left", expand=True, fill="x", padx=6)
        self._wide(actions, "Stop all", self.stop_all).pack(side="left", expand=True, fill="x", padx=(6, 0))

        second = tk.Frame(root, bg=BG)
        second.pack(fill="x", padx=18, pady=8)
        self._wide(second, "Restart backend", self.restart_backend).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._wide(second, "Restart frontend", self.restart_frontend).pack(side="left", expand=True, fill="x", padx=(6, 0))

        third = tk.Frame(root, bg=BG)
        third.pack(fill="x", padx=18)
        self._wide(third, "Open ZendAgent", self.open_zend, primary=True).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._wide(third, "Open Classic", self.open_classic).pack(side="left", expand=True, fill="x", padx=(6, 0))

        fourth = tk.Frame(root, bg=BG)
        fourth.pack(fill="x", padx=18, pady=8)
        self._wide(fourth, "Open logs", self.view_logs).pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._wide(fourth, "Refresh status", self.refresh_status).pack(side="left", expand=True, fill="x", padx=(6, 0))

        self._wide(root, "Clean frontend cache", self.clean_cache).pack(fill="x", padx=18)

        self.event = tk.Label(root, bg=BG, fg=INK, font=("Segoe UI", 9), wraplength=410, justify="left", anchor="w")
        self.event.pack(fill="x", padx=22, pady=(12, 4))

        options = tk.Frame(root, bg=BG)
        options.pack(fill="x", padx=22, pady=(0, 4))
        self.tray_var = tk.BooleanVar(value=self.supervisor.close_to_tray)
        self.exit_var = tk.BooleanVar(value=self.supervisor.stop_on_exit)
        tk.Checkbutton(
            options, text="Closing the window hides it in the tray", variable=self.tray_var,
            command=self._save_options, bg=BG, fg=INK, activebackground=BG, font=("Segoe UI", 9),
            selectcolor=CARD,
        ).pack(anchor="w")
        tk.Checkbutton(
            options, text="Exit launcher also stops ZendAgent", variable=self.exit_var,
            command=self._save_options, bg=BG, fg=INK, activebackground=BG, font=("Segoe UI", 9),
            selectcolor=CARD,
        ).pack(anchor="w")
        tk.Label(
            root, text="Restart loads saved files. It does not pull, merge, install, or change .env.",
            bg=BG, fg=MUTED, font=("Segoe UI", 8),
        ).pack(anchor="w", padx=22)
        tk.Button(
            root, text="Create desktop shortcut", command=self.create_shortcut,
            bg=BG, fg=BLUE, activebackground=BG, activeforeground=BLUE, bd=0,
            font=("Segoe UI", 9, "underline"), cursor="hand2",
        ).pack(anchor="w", padx=18, pady=(2, 12))
        self.render()

    def _mode_button(self, parent, text, mode):
        button = tk.Button(
            parent, text=text, command=lambda: self._choose(mode),
            bd=0, padx=14, pady=6, font=("Segoe UI", 9), cursor="hand2",
        )
        button.pack(side="left", padx=2, pady=2)
        return button

    def _status_row(self, parent, name):
        frame = tk.Frame(parent, bg="#F8FBFF", highlightbackground=LINE, highlightthickness=1)
        frame.pack(fill="x", padx=16, pady=4)
        top = tk.Frame(frame, bg="#F8FBFF")
        top.pack(fill="x", padx=10, pady=(8, 0))
        dot = tk.Canvas(top, width=10, height=10, bg="#F8FBFF", highlightthickness=0)
        dot.pack(side="left", padx=(0, 8))
        mark = dot.create_oval(1, 1, 9, 9, fill=DOT["stopped"], outline="")
        title = tk.Label(top, text=self.supervisor.role_label(name), bg="#F8FBFF", fg=INK, font=("Segoe UI", 10, "bold"))
        title.pack(side="left")
        state = tk.Label(top, text="Stopped", bg="#F8FBFF", fg=MUTED, font=("Segoe UI", 9))
        state.pack(side="right")
        detail = tk.Label(frame, text="", bg="#F8FBFF", fg=MUTED, font=("Segoe UI", 8), anchor="w", justify="left", wraplength=370)
        detail.pack(fill="x", padx=28, pady=(0, 8))
        return {"frame": frame, "dot": dot, "mark": mark, "title": title, "state": state, "detail": detail}

    def _wide(self, parent, text, command, primary=False):
        if primary:
            return tk.Button(
                parent, text=text, command=command, bg=BLUE, fg="white",
                activebackground="#0646DA", activeforeground="white",
                bd=0, padx=8, pady=9, font=("Segoe UI", 9, "bold"), cursor="hand2",
            )
        return tk.Button(
            parent, text=text, command=command, bg=CARD, fg=INK,
            activebackground=SOFT, activeforeground=INK,
            highlightbackground=LINE, highlightthickness=1,
            bd=0, padx=8, pady=8, font=("Segoe UI", 9), cursor="hand2",
        )

    def render(self) -> None:
        selected = self.supervisor.mode
        for button, mode in ((self.classic_btn, "classic"), (self.next_btn, "next"), (self.production_btn, "production")):
            on = mode == selected
            button.configure(bg=BLUE if on else SOFT, fg="white" if on else INK, activebackground=BLUE if on else SOFT)
        self.mode_label.configure(text=self.supervisor.active_label())
        for name, widgets in self.rows.items():
            row = self.supervisor.status[name]
            state = row["state"]
            widgets["title"].configure(text=self.supervisor.role_label(name))
            widgets["state"].configure(text=LABELS.get(state, state), fg=DOT.get(state, MUTED))
            bits = [part for part in (
                f"pid {row['pid']}" if row.get("pid") else "",
                f"port {row['port']}" if row.get("port") else "",
                row.get("health") or "",
            ) if part]
            lines = [" · ".join(bits), row.get("url") or "", row.get("detail") or ""]
            if row.get("error") and row.get("error") != row.get("detail"):
                lines.append(row["error"])
            widgets["detail"].configure(text="\n".join(line for line in lines if line))
            widgets["dot"].itemconfigure(widgets["mark"], fill=DOT.get(state, MUTED))
        self.port_label.configure(text=self.supervisor.port_note)
        self.event.configure(text=self.supervisor.message)

    def _choose(self, mode: str) -> None:
        self.supervisor.set_mode(mode)
        self.render()

    def _save_options(self) -> None:
        self.supervisor.close_to_tray = bool(self.tray_var.get())
        self.supervisor.stop_on_exit = bool(self.exit_var.get())
        self.supervisor.save_settings()

    def _probe(self) -> None:
        if self._autostart:
            self._autostart = False
            self.start_all()
            self.root.after(2000, self._probe)
            return
        if not self.supervisor.busy and not self._probing:
            self._probing = True
            threading.Thread(target=self._probe_work, daemon=True).start()
        self.root.after(2000, self._probe)

    def _probe_work(self) -> None:
        try:
            if not self.supervisor.busy:
                self.supervisor.refresh()
                self.root.after(0, self.render)
        finally:
            self._probing = False

    def start_all(self) -> None:
        self._run(self.supervisor.start_all)

    def stop_all(self) -> None:
        self._run(self.supervisor.stop_all)

    def restart_all(self) -> None:
        self._run(self.supervisor.restart_all)

    def restart_backend(self) -> None:
        self._run(self.supervisor.restart_backend)

    def restart_frontend(self) -> None:
        self._run(self.supervisor.restart_frontend)

    def _run(self, method) -> None:
        if self.supervisor.busy:
            self.supervisor.message = "A start, stop, or restart is already running."
            self.render()
            return

        def work():
            text = method()
            self.root.after(0, lambda: self._finished(text))

        self.supervisor.message = "Working…"
        self.render()
        threading.Thread(target=work, daemon=True).start()

    def _finished(self, text: str) -> None:
        self.supervisor.message = text
        self.render()

    def open_dashboard(self) -> None:
        self.open_zend()

    def open_zend(self) -> None:
        from launcher.desktop import open_desktop
        self.supervisor.message = open_desktop(self.supervisor.sign_in_url())
        self.render()

    def open_classic(self) -> None:
        from launcher.desktop import open_desktop
        self.supervisor.message = open_desktop(self.supervisor.classic_sign_in_url())
        self.render()

    def refresh_status(self) -> None:
        def work() -> str:
            self.supervisor.refresh()
            return "Status refreshed. Nothing was started or stopped."
        self._run(work)

    def clean_cache(self) -> None:
        from tkinter import messagebox
        if not messagebox.askyesno(
            "ZendAgent",
            "Remove frontend/.next and frontend/node_modules/.cache?\n\nRuntime files, .env, and logs are left in place.",
        ):
            return
        self._run(self.supervisor.clean_frontend_cache)

    def view_logs(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("ZendAgent logs")
        window.configure(bg=BG)
        window.geometry("640x420")
        text = scrolledtext.ScrolledText(window, font=("Consolas", 9), bg=CARD, fg=INK, bd=0)
        text.pack(fill="both", expand=True, padx=12, pady=12)
        logs = self.root_dir / "logs"
        chunks = []
        for name in ("launcher-backend.log", "launcher-dashboard.log", "launcher-frontend.log"):
            path = logs / name
            chunks.append(f"—— {name} ——")
            if not path.is_file():
                chunks.append("No log yet.")
            else:
                try:
                    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    lines = ["Could not read this log."]
                chunks.extend(lines[-200:])
            chunks.append("")
        text.insert("1.0", redact("\n".join(chunks)) or "No logs yet.")
        text.configure(state="disabled")
        tk.Button(window, text="Open log folder", command=lambda: os.startfile(logs), bg=BLUE, fg="white", bd=0, padx=10, pady=6).pack(pady=(0, 12))

    def create_shortcut(self) -> None:
        try:
            link = install(self.root_dir)
        except (OSError, FileNotFoundError) as exc:
            self.supervisor.message = f"Shortcut was not created ({exc.__class__.__name__})."
        else:
            self.supervisor.message = f"Desktop shortcut: {link}"
        self.render()

    def on_close(self) -> None:
        action = close_action(self.supervisor.close_to_tray, self.supervisor.stop_on_exit)
        if action == "hide":
            self.root.withdraw()
            if self.tray:
                self.tray.notify("Still running in the tray. Exit Launcher closes this window for good.")
            return
        self.quit(stop=action == "stop-owned-and-exit")

    def quit(self, stop: bool = False) -> None:
        if stop:
            self._run(self._stop_then_quit)
            return
        self._destroy()

    def _stop_then_quit(self) -> str:
        text = self.supervisor.stop_all()
        self.root.after(0, self._destroy)
        return text

    def _destroy(self) -> None:
        if self.tray:
            self.tray.stop()
        self.root.destroy()

    def _start_tray(self) -> None:
        from launcher.tray import TrayIcon

        def on_action(action: str) -> None:
            self.root.after(0, lambda: self._tray_action(action))

        icon = self.root_dir / "launcher" / "zendagent.ico"
        self.tray = TrayIcon(icon, on_action)
        if not self.tray.start():
            self.tray = None
            if self.supervisor.message.startswith("Checking"):
                self.supervisor.message = "Checking what is already running. The tray icon is unavailable."
                self.render()

    def _tray_action(self, action: str) -> None:
        if action == "show":
            self.root.deiconify()
            self.root.lift()
            return
        commands = {
            "Open Dashboard": self.open_dashboard,
            "Start All": self.start_all,
            "Restart All": self.restart_all,
            "Restart Backend": self.restart_backend,
            "Restart Frontend": self.restart_frontend,
            "Stop All": self.stop_all,
            "View Logs": self._show_and_logs,
            "Exit Launcher": lambda: self.quit(stop=self.supervisor.stop_on_exit),
        }
        command = commands.get(action)
        if command:
            command()

    def _show_and_logs(self) -> None:
        self.root.deiconify()
        self.view_logs()

    def _capture_and_close(self) -> None:
        from launcher.capture import save_hwnd

        if not self.supervisor.probed and self._capture_waits < 40:
            self._capture_waits += 1
            self.root.after(400, self._capture_and_close)
            return
        self.root.update_idletasks()
        self.root.update()
        save_hwnd(self.root.winfo_id(), self.screenshot)
        self.quit(stop=False)


def _instance_lock(root: Path):
    """One launcher per Windows session. A second double-click is refused."""
    if os.name != "nt":
        return None
    import ctypes

    kernel = ctypes.windll.kernel32
    kernel.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p]
    kernel.CreateMutexW.restype = ctypes.c_void_p
    kernel.GetLastError.restype = ctypes.c_ulong
    handle = kernel.CreateMutexW(None, False, "Local\\ZendAgentLauncher")
    if not handle or kernel.GetLastError() == 183:
        raise SystemExit("ZendAgent Launcher is already running. Use its tray icon.")
    return handle


def main(screenshot: str | None = None, autostart: bool = True) -> None:
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass
    root = Path(__file__).resolve().parents[1]
    app = LauncherApp(root, screenshot=screenshot, autostart=autostart)
    app.root.mainloop()

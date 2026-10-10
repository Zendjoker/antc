"""Open the existing ZendAgent page in an Edge app window. No second install."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def edge_path() -> str | None:
    candidates = []
    for key in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
        base = os.environ.get(key, "")
        if base:
            candidates.append(Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        candidates.append(Path(local) / "Microsoft" / "Edge" / "Application" / "msedge.exe")
    for path in candidates:
        if path.is_file():
            return str(path)
    return None


def app_command(url: str) -> list[str] | None:
    edge = edge_path()
    if not edge or not url.startswith("http://127.0.0.1:"):
        return None
    return [edge, f"--app={url}", "--new-window"]


def open_desktop(url: str) -> str:
    command = app_command(url)
    if command is None:
        import webbrowser
        webbrowser.open(url)
        return "Opened in the browser."
    subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "Opened the ZendAgent window."

"""Desktop shortcut that launches ZendAgent without a console window."""

from __future__ import annotations

import struct
import subprocess
from pathlib import Path

CREATE_NO_WINDOW = 0x08000000


def png_to_ico(png_path: Path, ico_path: Path) -> None:
    """Wrap a PNG in a Vista-style .ico. Windows shortcuts need an icon file."""
    data = Path(png_path).read_bytes()
    if data[12:16] != b"IHDR":
        raise ValueError("not a PNG")
    width, height = struct.unpack(">II", data[16:24])
    entry = struct.pack(
        "<BBBBHHII",
        width if width < 256 else 0,
        height if height < 256 else 0,
        0, 0, 1, 32, len(data), 22,
    )
    ico_path.write_bytes(struct.pack("<HHH", 0, 1, 1) + entry + data)


def desktop_dir() -> Path:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
        ) as key:
            return Path(winreg.QueryValueEx(key, "Desktop")[0])
    except OSError:
        return Path.home() / "Desktop"


def install(root: Path, desktop: Path | None = None) -> Path:
    root = Path(root).resolve()
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    python = root / ".venv" / "Scripts" / "python.exe"
    target = pythonw if pythonw.is_file() else python
    if not target.is_file():
        raise FileNotFoundError("Python environment not found at .venv\\Scripts\\python.exe")
    logo = root / "UI" / "brand" / "zend-favicon.png"
    icon = root / "launcher" / "zendagent.ico"
    if logo.is_file():
        png_to_ico(logo, icon)
    folder = Path(desktop) if desktop else desktop_dir()
    folder.mkdir(parents=True, exist_ok=True)
    link = folder / "ZendAgent.lnk"
    target_s = str(target).replace("'", "''")
    root_s = str(root).replace("'", "''")
    link_s = str(link).replace("'", "''")
    icon_s = (str(icon) if icon.is_file() else "").replace("'", "''")
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{link_s}'); "
        f"$s.TargetPath = '{target_s}'; "
        "$s.Arguments = '-m launcher'; "
        f"$s.WorkingDirectory = '{root_s}'; "
        "$s.Description = 'Start ZendAgent'; "
        f"$s.IconLocation = '{icon_s},0'; "
        "$s.WindowStyle = 7; "
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW,
    )
    if not link.is_file():
        raise OSError("the shortcut was not created")
    return link

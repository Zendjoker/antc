"""Creating and changing files in your folders: save a note / report, make a folder, move or rename, delete (to the
Recycle Bin only). Every change is checked on disk before it's called done.

Rules:
  - only inside your own folders (computer/files.py `allowed`): never system folders, never app data, never secrets files
  - an existing file is never overwritten unless they confirmed it
  - delete = Recycle Bin (recoverable), and it always asks first; folders are never deleted here
  - research reports: the sources section is written by code from the pages that were really read
"""

import ctypes
import datetime
import os
import re
from pathlib import Path

from room_agent.computer import files

FOLDERS = {"desktop": "Desktop", "documents": "Documents", "docs": "Documents", "downloads": "Downloads",
           "pictures": "Pictures", "music": "Music", "videos": "Videos"}
WRITE_EXT = {".md", ".txt", ".csv", ".json", ".html"}


# Windows known-folder ids: the Desktop / Documents the user actually sees (OneDrive can move them: "C:\\Users\\x\\
# OneDrive\\Desktop"); a file saved to the plain profile folder would then be invisible to them
_KNOWN = {"Desktop": "{B4BFCC3A-DB2C-424C-B029-7FE99A87C641}", "Documents": "{FDD39AD0-238F-46AF-ADB4-6C85480369C7}",
          "Downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "Pictures": "{33E28130-4E1E-4676-835A-98395C3BC3BB}",
          "Music": "{4BD8D571-6D19-48D3-BE97-422220080E43}", "Videos": "{18989B1D-99B5-455B-841C-AB7C74E4DDFC}"}
_known_cache = {}


def known_folder(name):
    """Where Windows really keeps this folder for them (OneDrive-aware), or the profile folder of that name."""
    if name in _known_cache:
        return _known_cache[name]
    path = None
    if os.name == "nt" and name in _KNOWN:
        try:
            import uuid
            from ctypes import wintypes

            class GUID(ctypes.Structure):
                _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD), ("Data3", wintypes.WORD),
                            ("Data4", ctypes.c_ubyte * 8)]

            u = uuid.UUID(_KNOWN[name])
            g = GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))
            out = ctypes.c_wchar_p()
            if ctypes.windll.shell32.SHGetKnownFolderPath(ctypes.byref(g), 0, None, ctypes.byref(out)) == 0:
                path = Path(out.value)
                ctypes.windll.ole32.CoTaskMemFree(out)
        except Exception:  # noqa: BLE001 (the plain profile folder below)
            path = None
    if path is None or not path.is_dir():
        path = files.HOME / name
    _known_cache[name] = path
    return path


def folder(name="desktop"):
    n = str(name or "desktop").lower().strip()
    if n in FOLDERS:
        return known_folder(FOLDERS[n])
    p = Path(name)
    if not p.is_absolute():
        p = files.HOME / name
    return p


def _safe_name(name, default_ext=".md"):
    n = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "").strip())[:120].strip(" .")
    if not n:
        n = "Jarvis note " + datetime.datetime.now().strftime("%Y-%m-%d %H%M")
    if Path(n).suffix.lower() not in WRITE_EXT:
        n += default_ext
    return n


def write(name, content, where="desktop", overwrite=False):
    """-> tool result. Text files only (.md, .txt, .csv, .json, .html)."""
    target_dir = folder(where)
    ok, why = files.allowed(target_dir / "x.txt")
    if not ok:
        return f"FAILED: not saved: {why}."
    if not target_dir.is_dir():
        return f"FAILED: there's no folder {target_dir.name} in your files. Nothing was saved."
    path = target_dir / _safe_name(name)
    ok, why = files.allowed(path)
    if not ok:
        return f"FAILED: not saved: {why}."
    if path.exists() and not overwrite:
        return (f"NEEDS: {path.name} already exists in {target_dir.name}. Nothing was saved. To ADD to it, call "
                "append_to_file (it keeps what's there); only if they want it REPLACED, ask, then call again with "
                "overwrite=true.")
    text = str(content or "")
    if not text.strip():
        return "NEEDS: what to write in it."
    path.write_text(text, encoding="utf-8")
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        return f"UNKNOWN: not confirmed: {path.name} doesn't read back as written."
    return f"OK: saved {path.name} in your {target_dir.name} folder ({len(text)} characters, checked on disk)."


def _find_existing(name, where):
    """An existing file they named, in that folder: the exact name, or the same name with a text extension they left
    out ("LED strip issue" -> "LED_strip_issue.txt"). -> Path or None."""
    target_dir = folder(where)
    raw = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "").strip()).strip(" .")
    if not raw or not target_dir.is_dir():
        return None
    exact = target_dir / raw
    if exact.is_file():
        return exact
    key = re.sub(r"[\s_-]+", "", Path(raw).stem.lower())
    for p in sorted(target_dir.iterdir()):
        if p.is_file() and p.suffix.lower() in WRITE_EXT and re.sub(r"[\s_-]+", "", p.stem.lower()) == key:
            return p
    return None


def append(name, content, where="desktop", create=True):
    """Add text to the END of a text file (a log, an issues list), keeping everything already in it. -> tool result.
    Checked on disk: what was there before is unchanged and the file now ends with exactly the new text."""
    target_dir = folder(where)
    ok, why = files.allowed(target_dir / "x.txt")
    if not ok:
        return f"FAILED: nothing was added: {why}."
    if not target_dir.is_dir():
        return f"FAILED: there's no folder {target_dir.name} in your files. Nothing was added."
    path = _find_existing(name, where) or target_dir / _safe_name(name, ".txt")
    ok, why = files.allowed(path)
    if not ok:
        return f"FAILED: nothing was added: {why}."
    if path.suffix.lower() not in WRITE_EXT:
        return f"FAILED: {path.name} isn't a text file I can add to. Nothing was added."
    text = str(content or "").strip("\n")
    if not text.strip():
        return "NEEDS: what to add to it."
    existed = path.is_file()
    if not existed and not create:
        return f"FAILED: there's no {path.name} in your {target_dir.name} folder. Nothing was added."
    try:
        before = path.read_text(encoding="utf-8") if existed else ""
    except (OSError, UnicodeDecodeError) as e:
        return f"FAILED: couldn't read {path.name} ({e.__class__.__name__}), so nothing was added."
    sep = "" if not before or before.endswith("\n") else "\n"
    added = sep + text + "\n"
    try:
        with open(path, "a", encoding="utf-8", newline="") as f:
            f.write(added)
    except OSError as e:
        return f"FAILED: couldn't write to {path.name} ({e.strerror or e.__class__.__name__}). Nothing was added."
    try:
        after = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        after = None
    if after != before + added:
        return (f"UNKNOWN: not confirmed: {path.name} in {target_dir.name} doesn't read back as expected after adding to "
                "it. Don't say it was added; tell them it couldn't be checked.")
    lines = len(text.splitlines())
    return (f"OK: added {lines} line{'s' if lines != 1 else ''} to the end of {path.name} in your {target_dir.name} folder "
            f"({'it was created' if not existed else 'what was already there is unchanged'}; checked on disk).")


def make_folder(name, where="documents"):
    base = folder(where)
    path = base / re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", str(name or "")).strip(" .")
    ok, why = files.allowed(path / "x.txt")
    if not ok or path == base:
        return f"FAILED: not created: {why or 'no folder name'}."
    if path.exists():
        return f"OK: nothing needed: the folder {path.name} already exists in {base.name}."
    path.mkdir(parents=False)
    return f"OK: made the folder {path.name} in {base.name} (checked)." if path.is_dir() else "UNKNOWN: not confirmed."


def move(src, dest_folder="", new_name=""):
    """Move and / or rename a file inside their folders. -> tool result."""
    s = Path(src)
    ok, why = files.allowed(s)
    if not ok or not s.is_file():
        return f"FAILED: {why or 'that file is not there'}."
    target_dir = folder(dest_folder) if dest_folder else s.parent
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", new_name).strip(" .") if new_name else s.name
    if new_name and not Path(name).suffix:
        name += s.suffix  # (renaming keeps the type unless they name one)
    dest = target_dir / name
    ok, why = files.allowed(dest)
    if not ok:
        return f"FAILED: not moved: {why}."
    if not target_dir.is_dir():
        return f"FAILED: there's no folder {target_dir.name}."
    if dest.exists():
        return f"FAILED: {dest.name} already exists there; nothing was moved (pick another name)."
    s.rename(dest)
    if not dest.is_file() or s.exists():
        return "UNKNOWN: not confirmed: the file isn't where it should be."
    return f"OK: {s.name} is now {dest.name} in {target_dir.name} (checked)."


def recycle(path):
    """Send one file to the Recycle Bin (recoverable). Folders are refused."""
    p = Path(path)
    ok, why = files.allowed(p)
    if not ok:
        return f"FAILED: not deleted: {why}."
    if not p.exists():
        return "FAILED: that file isn't there."
    if p.is_dir():
        return "FAILED: folders aren't deleted by voice. Do that in File Explorer."

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [("hwnd", ctypes.c_void_p), ("wFunc", ctypes.c_uint), ("pFrom", ctypes.c_wchar_p),
                    ("pTo", ctypes.c_wchar_p), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", ctypes.c_int),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", ctypes.c_wchar_p)]

    from room_agent import config

    if config.TEST_MODE and getattr(ctypes.windll.shell32.SHFileOperationW, "__name__", "") == "SHFileOperationW":
        config.real_desktop("the Recycle Bin")  # (a test that fakes SHFileOperationW may use it)
    op = SHFILEOPSTRUCTW(None, 3, str(p.resolve()) + "\0", None, 0x0040 | 0x0010 | 0x0400 | 0x0004, 0, None, None)
    rc = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))  # FO_DELETE + ALLOWUNDO + NOCONFIRMATION + NOERRORUI + SILENT
    if rc != 0 or p.exists():
        return f"FAILED: Windows didn't move {p.name} to the Recycle Bin (code {rc}). Nothing was deleted."
    return f"OK: {p.name} is in the Recycle Bin (restore it from there if needed)."


def research_report(report, summary="", where="desktop", name=""):
    """A Markdown report from the last research: their question, Jarvis's summary, then each source with the passages
    actually read and its link, and what couldn't be read. Sources and quotes come from the Report object, not the model."""
    if report is None or not report.sources:
        return "FAILED: there's no finished research with sources to save. Research it first."
    when = datetime.datetime.fromtimestamp(report.at)
    lines = [f"# {report.question.strip().rstrip('?')}", "",
             f"*Research by Jarvis, {when:%B %d, %Y %H:%M}. {len(report.sources)} sources read.*", ""]
    if summary.strip():
        lines += ["## Summary", "", "*Written by Jarvis from the sources below; check them for anything important.*", "",
                  summary.strip(), ""]
    lines += ["## What the sources say", ""]
    for s in report.sources:
        lines.append(f"### [{s.n}] {s.title}" + (" (official / primary source)" if s.primary else ""))
        lines += [f"> {p}" for p in s.passages]
        lines += [f"Source: <{s.url}>", ""]
    if report.failed:
        lines += ["## Couldn't be read", ""] + [f"- {u} ({w})" for u, w in report.failed] + [""]
    lines += ["## Sources", ""] + [f"{s.n}. {s.title}: {s.url}" for s in report.sources]
    topic = re.sub(r"[^\w -]", "", report.question)[:60].strip()
    fname = name or f"Research - {topic} {when:%Y-%m-%d}"
    return write(fname, "\n".join(lines) + "\n", where)

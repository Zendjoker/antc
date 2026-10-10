"""Creating and changing files in your folders: save a note / report, make a folder, move or rename, delete (to the
Recycle Bin only). Every change is checked on disk before it's called done.

Rules:
  - only inside your own folders (computer/files.py `allowed`): never system folders, never app data, never secrets files
  - an existing file is never overwritten unless they confirmed it (append=True is a separate, explicit third option:
    adds to the file instead of either refusing or replacing it)
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


def folder(name="desktop"):
    n = str(name or "desktop").lower().strip()
    if n in FOLDERS:
        return files.HOME / FOLDERS[n]
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


ISSUE_HEADER = re.compile(r"^##\s*Issue\s+(\d+)\b", re.I | re.M)


def _next_issue_number(existing_text):
    nums = [int(n) for n in ISSUE_HEADER.findall(existing_text)]
    return (max(nums) + 1) if nums else 1


def _normalize(text):
    return " ".join(str(text or "").split()).strip().lower()


def _is_duplicate_entry(existing_text, new_body):
    """An entry with this exact (whitespace-normalized) body is already in the file - deterministic, not fuzzy: only
    an exact match after normalizing whitespace counts as a duplicate, so two genuinely different issues are never
    merged just because they're topically similar. Compares bodies only (strips each entry's own timestamp line first),
    so the same issue text added hours or days apart is still recognized as a duplicate."""
    new_norm = _normalize(new_body)
    if not new_norm:
        return False
    parts = ISSUE_HEADER.split(existing_text)
    blocks = parts[2::2] if len(parts) > 2 else ([existing_text] if existing_text.strip() else [])
    for block in blocks:
        halves = block.split("\n\n", 1)  # drop the "- <timestamp>" remainder before the blank line
        body = halves[1] if len(halves) > 1 else block
        if _normalize(body) == new_norm:
            return True
    return False


def write(name, content, where="desktop", overwrite=False, append=False):
    """-> tool result. Text files only (.md, .txt, .csv, .json, .html).
    append=True: adds a new numbered '## Issue N' entry to an existing file instead of refusing or replacing it
    (an exact duplicate of an existing entry's text is not added again); if the file doesn't exist yet, it's created
    as 'Issue 1' so the first and later calls produce the same one master file."""
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
    text = str(content or "")
    if not text.strip():
        return "NEEDS: what to write in it."

    if append:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if _is_duplicate_entry(existing, text):
            return f"OK: nothing added: {path.name} already has this exact entry (no duplicate created)."
        n = _next_issue_number(existing)
        stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        block = f"## Issue {n} - {stamp}\n\n{text.strip()}\n"
        full = (existing.rstrip("\n") + "\n\n" + block) if existing.strip() else block
        path.write_text(full, encoding="utf-8")
        if not path.is_file() or path.read_text(encoding="utf-8") != full:
            return f"UNKNOWN: not confirmed: {path.name} doesn't read back as written."
        files.remember_saved(path)
        return (f"OK: added issue {n} to {path.name} in your {target_dir.name} folder ({len(full)} characters total, "
                "checked on disk).")

    if path.exists() and not overwrite:
        return (f"NEEDS: {path.name} already exists in {target_dir.name}. Ask whether to replace it (then call again with "
                "overwrite=true) or use another name.")
    path.write_text(text, encoding="utf-8")
    if not path.is_file() or path.read_text(encoding="utf-8") != text:
        return f"UNKNOWN: not confirmed: {path.name} doesn't read back as written."
    files.remember_saved(path)
    return f"OK: saved {path.name} in your {target_dir.name} folder ({len(text)} characters, checked on disk)."


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

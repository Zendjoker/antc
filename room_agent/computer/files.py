"""Files on this PC: find them, read them, open them.

    find      Windows Search's index (names AND contents, instant), else a bounded walk of your folders
    read      text, code, Markdown, CSV / JSON, PDF (pypdf), Word .docx, Excel .xlsx, PowerPoint .pptx
    "this"    the file selected in File Explorer, else the one open in the window in front (by its title), else the
              most recently used file with that name
    open      with its usual app (documents, images, media only: never programs or scripts)

Only your own folders (your user profile and OneDrive) are searched. Files that hold secrets (password databases, keys,
.env files, wallets) are never read. What a file says is personal: the tools are private (never logged or learned from).
"""

import os
import re
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

HOME = Path(os.environ.get("USERPROFILE", str(Path.home())))
TEXT_EXT = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".log", ".py", ".js", ".ts", ".html", ".htm", ".css",
            ".xml", ".yaml", ".yml", ".ini", ".cfg", ".toml", ".rst", ".tex", ".sql", ".java", ".c", ".cpp", ".h", ".cs",
            ".go", ".rs", ".rb", ".php", ".sh", ".bat", ".ps1", ".srt", ".vtt"}
DOC_EXT = {".pdf", ".docx", ".xlsx", ".pptx"}
OPENABLE = TEXT_EXT - {".bat", ".ps1", ".sh", ".py", ".js"} | DOC_EXT | {
    ".doc", ".xls", ".ppt", ".odt", ".rtf", ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".heic", ".svg", ".mp3", ".wav",
    ".flac", ".m4a", ".mp4", ".mkv", ".mov", ".avi", ".webm", ".zip"}
SECRET_NAMES = re.compile(r"(^|[\\/])(\.env(\..*)?|id_rsa|id_dsa|id_ecdsa|id_ed25519|.*\.pem|.*\.key|.*\.pfx|.*\.p12|"
                          r".*\.kdbx?|.*\.keychain|.*\.ppk|.*\.jks|.*\.keystore|wallet\.dat|.*passwords?.*|"
                          r".*credentials?.*|.*secrets?\..*|.*\.ovpn|.*recovery.?codes?.*|\.npmrc|\.pypirc|_?\.?netrc|"
                          r"\.git-credentials|client_secrets?.*\.json|.*service.?account.*\.json|.*tokens?\.json|"
                          r"google_client\.json|.*\.tfstate|\.htpasswd)$", re.I)
# Folders that only hold keys / tokens / cloud credentials: nothing inside them is read, whatever its name.
SECRET_DIRS = {".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".claude", ".config", ".gcloud", ".vault-token",
               ".password-store", ".terraform.d"}
# Jarvis's own records (memory, conversations, connected accounts, tasks, logs): never handed to a tool as a "file".
JARVIS_PRIVATE_EXT = {".json", ".jsonl", ".db", ".db-wal", ".db-shm", ".npz", ".lock", ".bak", ".log"}
SKIP_DIRS = {"appdata", "node_modules", ".git", ".venv", "venv", "__pycache__", "$recycle.bin", ".cache", "site-packages"}
MAX_CHARS = 12000
WALK_BUDGET_S = 3.0


@dataclass
class Found:
    path: str
    name: str
    modified: float
    size: int


def roots():
    out = [HOME / d for d in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos")]
    out += [p for p in HOME.glob("OneDrive*") if p.is_dir()]
    return [p for p in out if p.exists()] or [HOME]


def allowed(path):
    """Inside the user's own folders (no system folders, no other users), and not a secrets file."""
    try:
        p = Path(path).resolve()
    except OSError:
        return False, "that path isn't valid"
    home = HOME.resolve()
    if p != home and home not in p.parents:  # (a real containment check: "C:\\Users\\adam2" isn't inside "C:\\Users\\adam")
        return False, "it's outside your own folders"
    inside = [part.lower() for part in p.parts[len(home.parts):]]
    if "appdata" in inside:
        return False, "it's in app data, not your files"
    if SECRET_NAMES.search(str(p)) or any(part in SECRET_DIRS for part in inside[:-1] or inside):
        return False, "it looks like it holds passwords, keys or secrets"
    if _jarvis_private(p):
        return False, "it's one of Jarvis's own private records (memory, conversations, accounts, logs)"
    return True, ""


def _jarvis_private(p):
    try:
        from room_agent import config

        root = Path(config.HERE).resolve()
    except Exception:  # noqa: BLE001
        return False
    if p == root or root not in p.parents:
        return False
    rel = [x.lower() for x in p.parts[len(root.parts):]]
    return (p.suffix.lower() in JARVIS_PRIVATE_EXT or rel[0] in ("logs", "models", ".venv", ".git")
            or p.name.lower().startswith(".env"))


# ---------------------------------------------------------------- finding
def _index_search(query, kind=None, limit=8):
    """Windows Search (the index Explorer uses): names and contents, newest first. None if the index isn't available."""
    try:
        import comtypes.client
    except ImportError:
        return None
    words = [w for w in re.findall(r"[\w.-]{2,}", str(query)) if w.lower() not in ("my", "the", "file", "files", "a", "an")]
    if not words:
        return []
    safe = [w.replace("'", "''") for w in words[:6]]
    name_cond = " AND ".join(f"System.FileName LIKE '%{w}%'" for w in safe)
    text_cond = "CONTAINS(*, '" + " AND ".join(f'"{w}"' for w in safe) + "')"
    scopes = " OR ".join(f"SCOPE='file:{str(r)}'" for r in roots())
    ext = f" AND System.FileExtension = '.{kind.strip('.').lower()}'" if kind else ""
    sql = (f"SELECT TOP {int(limit)} System.ItemPathDisplay, System.FileName, System.DateModified, System.Size FROM SystemIndex "
           f"WHERE ({scopes}) AND System.ItemType <> 'Directory' AND (({name_cond}) OR {text_cond}){ext} "
           "ORDER BY System.DateModified DESC")
    try:
        conn = comtypes.client.CreateObject("ADODB.Connection", dynamic=True)
        conn.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        rs = comtypes.client.CreateObject("ADODB.Recordset", dynamic=True)
        rs.Open(sql, conn)
        out = []
        while not rs.EOF:
            path = str(rs.Fields.Item(0).Value or "")
            mod = rs.Fields.Item(2).Value
            out.append(Found(path, str(rs.Fields.Item(1).Value or Path(path).name),
                             mod.timestamp() if hasattr(mod, "timestamp") else 0.0, int(rs.Fields.Item(3).Value or 0)))
            rs.MoveNext()
        rs.Close()
        conn.Close()
        return [f for f in out if allowed(f.path)[0]]
    except Exception:
        return None


def _walk_search(query, kind=None, limit=8):
    """A bounded walk of the user's folders by file name (when the index isn't available)."""
    words = [w.lower() for w in re.findall(r"[\w.-]{2,}", str(query)) if w.lower() not in ("my", "the", "file", "a", "an")]
    from room_agent import cancel

    deadline, hits = time.time() + WALK_BUDGET_S, []
    for root in roots():
        if cancel.requested():
            break
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for fn in filenames:
                low = fn.lower()
                if all(w in low for w in words) and (not kind or low.endswith("." + kind.strip(".").lower())):
                    p = os.path.join(dirpath, fn)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    if allowed(p)[0]:
                        hits.append(Found(p, fn, st.st_mtime, st.st_size))
            if time.time() > deadline or cancel.requested():
                break
        if time.time() > deadline:
            break
    return sorted(hits, key=lambda f: -f.modified)[:limit]


def find(query, kind=None, limit=8):
    """-> [Found] newest first."""
    hits = _index_search(query, kind, limit)
    if hits is None:
        hits = _walk_search(query, kind, limit)
    return hits


# ---------------------------------------------------------------- "this file"
def selected_in_explorer():
    """The file selected in the front-most File Explorer window, or None."""
    try:
        import comtypes.client

        shell = comtypes.client.CreateObject("Shell.Application", dynamic=True)
        import ctypes

        fg = ctypes.windll.user32.GetForegroundWindow()
        windows = shell.Windows()
        for i in range(windows.Count):
            w = windows.Item(i)
            try:
                if int(w.HWND) != fg:
                    continue
                items = w.Document.SelectedItems()
                if items.Count:
                    return str(items.Item(0).Path)
            except Exception:
                continue
    except Exception:
        return None
    return None


def _recent_files():
    """Recently used files (Windows' Recent folder), newest first: [(name, target path)]."""
    recent = Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Recent"
    out = []
    try:
        import comtypes.client

        shell = comtypes.client.CreateObject("WScript.Shell", dynamic=True)
        links = sorted(recent.glob("*.lnk"), key=lambda p: -p.stat().st_mtime)[:60]
        for link in links:
            try:
                target = str(shell.CreateShortcut(str(link)).TargetPath)
            except Exception:
                continue
            if target and os.path.isfile(target):
                out.append((Path(target).name, target))
    except Exception:
        pass
    return out


def in_front():
    """The file shown in the window in front: its title names it ('report.pdf - Adobe Acrobat', 'notes.txt - Notepad')."""
    import ctypes

    from room_agent.computer.browsers import title_of

    title = title_of(ctypes.windll.user32.GetForegroundWindow())
    m = re.search(r"([^\\/:*?\"<>|]+\.(%s))" % "|".join(e.strip(".") for e in (TEXT_EXT | DOC_EXT | OPENABLE)), title, re.I)
    if not m:
        return None, title
    name = m.group(1).strip().lstrip("*● ").strip()
    for rname, target in _recent_files():
        if rname.lower() == name.lower():
            return target, title
    hits = find(name, limit=1)
    return (hits[0].path if hits else None), title


def resolve(said):
    """'this file' / a path / a name -> (path, how) or (None, why not)."""
    s = str(said or "").strip().strip("'\"")
    if not s or re.fullmatch(r"(this|that|the|current|open)?\s*(file|document|doc|pdf|one|it|this|that)?", s, re.I):
        sel = selected_in_explorer()
        if sel:
            return sel, "selected in File Explorer"
        path, title = in_front()
        if path:
            return path, f"open in \"{title[:60]}\""
        return None, ("no file is selected in File Explorer and the window in front doesn't show one. Ask which file (or "
                      "to select it in File Explorer).")
    if os.path.isabs(s) and os.path.isfile(s):
        return s, "the path given"
    hits = find(s, limit=3)
    if not hits:
        return None, f"no file matching \"{s}\" was found in your folders"
    return hits[0].path, "found by search" + (f" (newest of {len(hits)} matches)" if len(hits) > 1 else "")


# ---------------------------------------------------------------- reading
def _docx(p):
    with zipfile.ZipFile(p) as z:
        xml = z.read("word/document.xml")
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    root = ElementTree.fromstring(xml)
    paras = ["".join(t.text or "" for t in para.iter(ns + "t")) for para in root.iter(ns + "p")]
    return "\n".join(x for x in paras if x.strip())


def _xlsx(p, max_rows=200):
    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    with zipfile.ZipFile(p) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(ns + "si"):
                shared.append("".join(t.text or "" for t in si.iter(ns + "t")))
        sheets = sorted(n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        out = []
        for n, sheet in enumerate(sheets[:5], 1):
            out.append(f"[sheet {n}]")
            for i, row in enumerate(ElementTree.fromstring(z.read(sheet)).iter(ns + "row")):
                if i >= max_rows:
                    out.append("...")
                    break
                cells = []
                for c in row.iter(ns + "c"):
                    v = c.find(ns + "v")
                    val = v.text if v is not None else "".join(t.text or "" for t in c.iter(ns + "t"))
                    if c.get("t") == "s" and val is not None and val.isdigit() and int(val) < len(shared):
                        val = shared[int(val)]
                    cells.append(val or "")
                if any(cells):
                    out.append("\t".join(cells))
    return "\n".join(out)


def _pptx(p):
    ns = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
    with zipfile.ZipFile(p) as z:
        slides = sorted((n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)),
                        key=lambda n: int(re.search(r"(\d+)", n).group(1)))
        out = []
        for n, s in enumerate(slides, 1):
            texts = [t.text for t in ElementTree.fromstring(z.read(s)).iter(ns + "t") if t.text]
            out.append(f"[slide {n}] " + " ".join(texts))
    return "\n".join(out)


def _pdf(p, max_pages=40):
    from pypdf import PdfReader

    r = PdfReader(p)
    if r.is_encrypted:
        try:
            r.decrypt("")
        except Exception:
            raise ValueError("the PDF is password-protected")
    pages = []
    for i, page in enumerate(r.pages[:max_pages]):
        pages.append(f"[page {i + 1}] " + (page.extract_text() or "").strip())
    text = "\n".join(pages)
    if len(re.sub(r"\[page \d+\]|\s", "", text)) < 20:
        raise ValueError("the PDF has no text layer (it's a scan or images)")
    return text + (f"\n[... {len(r.pages) - max_pages} more pages not read]" if len(r.pages) > max_pages else "")


def read(path, max_chars=MAX_CHARS):
    """-> {"text", "chars", "truncated", "kind"}; raises ValueError with what to tell them."""
    ok, why = allowed(path)
    if not ok:
        raise ValueError(f"not read: {why}")
    p = Path(path)
    if not p.is_file():
        raise ValueError("that file doesn't exist anymore")
    ext = p.suffix.lower()
    if p.stat().st_size > 50_000_000:
        raise ValueError("it's too big to read (over 50 MB)")
    try:
        if ext == ".pdf":
            text, kind = _pdf(str(p)), "PDF"
        elif ext == ".docx":
            text, kind = _docx(p), "Word document"
        elif ext == ".xlsx":
            text, kind = _xlsx(p), "spreadsheet"
        elif ext == ".pptx":
            text, kind = _pptx(p), "presentation"
        elif ext in TEXT_EXT or not ext:
            raw = p.read_bytes()[:2_000_000]
            for enc in ("utf-8-sig", "utf-16", "cp1252"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                raise ValueError("it isn't readable text")
            if "\x00" in text[:2000]:
                raise ValueError("it's a binary file, not text")
            kind = "text file"
        else:
            raise ValueError(f"I can't read {ext} files (text, PDF, Word, Excel and PowerPoint work)")
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError):
        raise ValueError("the file is damaged or not really that format")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return {"text": text[:max_chars], "chars": len(text), "truncated": len(text) > max_chars, "kind": kind}


def open_with_default(path):
    """Open a document / image / media file with its usual app. Never programs or scripts."""
    ok, why = allowed(path)
    if not ok:
        raise ValueError(f"not opened: {why}")
    ext = Path(path).suffix.lower()
    if ext not in OPENABLE:
        raise ValueError(f"not opened: {ext or 'files without an extension'} could run code; only documents, images and "
                         "media are opened")
    from room_agent import config

    if getattr(os.startfile, "__module__", "") == "nt":
        config.real_desktop("opening a file")  # (a test that fakes os.startfile may use it)
    os.startfile(path)  # noqa: S606 (a document with its registered app, never an executable: checked above)

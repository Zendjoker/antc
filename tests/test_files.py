"""Files, offline: a temporary home folder with real files (text, Word, Excel, PowerPoint, PDF) built by the test. Your
real folders are never searched, read or opened here.

Run:  .venv\\Scripts\\python -m tests.test_files
"""

import os
import tempfile
import time
import zipfile
from pathlib import Path

from tests.harness import setup_env

setup_env()

from room_agent import runtime as rt  # noqa: E402
from room_agent.actions import core, executor  # noqa: E402
from room_agent.computer import files  # noqa: E402
from tests.harness import Checker  # noqa: E402

t = Checker()
core.ensure_loaded()
home = Path(tempfile.mkdtemp())
files.HOME = home
docs, down = home / "Documents", home / "Downloads"
docs.mkdir()
down.mkdir()
files._index_search = lambda q, kind=None, limit=8: None  # (the test folder isn't in Windows' index: the walk is used)


def make_docx(path, paras):
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paras)
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                   f"<w:body>{body}</w:body></w:document>")


def make_xlsx(path):
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/sharedStrings.xml", f'<sst {ns}><si><t>Item</t></si><si><t>Rent</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {ns}><sheetData><row><c t="s"><v>0</v></c><c><v>Cost</v></c></row>'
                   '<row><c t="s"><v>1</v></c><c><v>1200</v></c></row></sheetData></worksheet>')


def make_pptx(path):
    ns = 'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
    with zipfile.ZipFile(path, "w") as z:
        for n, text in ((1, "Quarterly plan"), (2, "Ship the voice agent")):
            z.writestr(f"ppt/slides/slide{n}.xml", f"<p:sld xmlns:p='x' {ns}><a:t>{text}</a:t></p:sld>")


def make_pdf(path, text):
    """A minimal valid PDF with one line of text (no library needed to write it)."""
    stream = f"BT /F1 18 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF" % (len(objs) + 1, xref)
    path.write_bytes(out)


(docs / "notes.txt").write_text("Remember: the router password is on the sticker.\nCall Sam on Monday.", encoding="utf-8")
make_docx(docs / "Adoum CV 2026.docx", ["Adoum Azzouz", "Software engineer", "Built a voice agent called Jarvis"])
make_xlsx(docs / "budget.xlsx")
make_pptx(docs / "plan.pptx")
make_pdf(down / "lease agreement.pdf", "Notice period: two months")
old_cv = docs / "old CV 2019.docx"
make_docx(old_cv, ["An old CV"])
past = time.time() - 5 * 365 * 86400
os.utime(old_cv, (past, past))
(docs / ".env").write_text("OPENAI_API_KEY=sk-secret", encoding="utf-8")
(docs / "passwords.txt").write_text("bank: hunter2", encoding="utf-8")
(docs / "tool.bat").write_text("echo hi", encoding="utf-8")
(docs / "blob.bin").write_bytes(bytes(range(256)) * 10)
scan = down / "scan.pdf"
make_pdf(scan, "")


def run(name, args, said):
    rt.new_turn(said)
    return executor.execute(name, args)


print("Finding:")
r = run("find_files", {"query": "CV"}, "where's my CV")
t.check("'where's my CV?' -> both CVs, newest first", r.success and r.message.index("Adoum CV 2026") < r.message.index("old CV 2019"),
        r.message)
t.check("...said by folder, not a full path", "your Documents" in r.message and str(home) not in r.message, r.message)
r = run("find_files", {"query": "lease", "type": "pdf"}, "find the lease pdf")
t.check("by name and type", "lease agreement.pdf" in r.message)
r = run("find_files", {"query": "unicorn"}, "find unicorn")
t.check("nothing found -> says so", "no file matching" in r.message)
t.check("secret files are never even listed", not any("passwords" in f.name or f.name == ".env" for f in files.find("pass")))

print("Reading:")
r = run("read_file", {"number": 1}, "read the first one")
t.check("'read the first one' -> the first file of the last search with results (the lease)", r.success
        and "Notice period: two months" in r.message, r.message[:200])
r = run("read_file", {"file": "Adoum CV 2026"}, "read my CV")
t.check("Word .docx -> its text", r.success and "Built a voice agent called Jarvis" in r.message, r.message[:200])
r = run("read_file", {"file": "lease agreement"}, "what does my lease say about notice")
t.check("PDF -> its text", r.success and "Notice period: two months" in r.message, r.message[:200])
r = run("read_file", {"file": "budget"}, "read my budget spreadsheet")
t.check("Excel .xlsx -> rows with shared strings resolved", r.success and "Rent\t1200" in r.message, r.message[:200])
r = run("read_file", {"file": "plan.pptx"}, "summarize the plan slides")
t.check("PowerPoint .pptx -> slide by slide", r.success and "[slide 2] Ship the voice agent" in r.message)
r = run("read_file", {"file": "notes"}, "read my notes file")
t.check("plain text", r.success and "Call Sam on Monday" in r.message)
r = run("read_file", {"file": str(docs / ".env")}, "read that env file")
t.check(".env -> refused (holds secrets)", not r.success and "secrets" in r.message and "sk-secret" not in r.message, r.message)
r = run("read_file", {"file": str(docs / "passwords.txt")}, "read passwords.txt")
t.check("passwords.txt -> refused", not r.success and "hunter2" not in r.message)
r = run("read_file", {"file": os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "win.ini")}, "read win.ini")
t.check("a file outside their own folders -> refused", not r.success and ("outside" in r.message or "found" in r.message), r.message)
r = run("read_file", {"file": str(scan)}, "read the scan")
t.check("a scanned PDF (no text) -> says so instead of guessing", not r.success and "scan" in r.message, r.message)
r = run("read_file", {"file": str(docs / "blob.bin")}, "read blob.bin")
t.check("binary / unsupported -> refused", not r.success)

print("'This file':")
files.selected_in_explorer = lambda: str(down / "lease agreement.pdf")
r = run("read_file", {}, "summarize this file")
t.check("'summarize this file' -> the one selected in File Explorer", r.success and "selected in File Explorer" in r.message
        and "two months" in r.message, r.message[:200])
files.selected_in_explorer = lambda: None
files.in_front = lambda: (None, "Visual Studio Code")
r = run("read_file", {}, "summarize this file")
t.check("nothing selected or open -> asks which file", not r.success and "Ask which file" in r.message, r.message)
rt.pending = None
r = run("read_file", {"file": "Adoum CV 2026"}, "set a timer for 5 minutes")
t.check("their words didn't ask for a file -> asks first (a page or email can't make it read files)",
        r.message.startswith("NEEDS_CONFIRMATION"), r.message)
rt.pending = None
t.check("file reading is private (never logged, learned or remembered)", core.get("read_file").private and core.get("find_files").private)

print("Opening:")
opened = []
os.startfile = lambda p: opened.append(p)
from room_agent.computer import browsers  # noqa: E402

browsers._top_windows = lambda: []  # (no real window list in tests: the opened file's window never shows here)
r = run("open_file", {"file": str(docs / "tool.bat")}, "open tool.bat")
t.check("a script is never opened", not r.success and not opened and "could run code" in r.message, r.message)
r = run("open_file", {"file": "budget"}, "open my budget")
t.check("a document is handed to its app; not seen opening -> 'not confirmed'", opened and not r.success
        and "not confirmed" in r.message, r.message)

print("Save -> open -> append, on a mocked Desktop (the 2026-10-09 live bug):")
desktop = home / "Desktop"
desktop.mkdir()
opened.clear()
r = run("save_file", {"name": "LED_strip_issue", "content": "The LED strip flickers at low brightness.",
                      "folder": "desktop", "append": True}, "save that as issue 1 in my issues file")
t.check("save_file (append, file doesn't exist yet) creates it as Issue 1 and verifies", r.success and "issue 1" in r.message.lower(), r.message)
saved_path = desktop / "LED_strip_issue.md"
t.check("the exact file really exists where save_file said", saved_path.is_file())

# The real bug: Windows Search can report success with ZERO rows for a brand-new file (not "unavailable" - just
# stale). find() must still fall back to the real walk here, not report "not found".
files._index_search = lambda q, kind=None, limit=8: []
r = run("open_file", {"file": "LED_strip_issue"}, "open that")
t.check("open_file finds it immediately even when the search index is up but hasn't caught the new file yet "
        "(empty result still falls back to the real walk, not just an unavailable index)",
        "no file matching" not in r.message, r.message)
t.check("...and opens the exact same verified path save_file just wrote, not a different resolution",
        opened and Path(opened[-1]).resolve() == saved_path.resolve(), opened)

files._index_search = lambda q, kind=None, limit=8: None  # back to "index unavailable" for the rest of the file
r2 = run("save_file", {"name": "LED_strip_issue", "content": "Also: the effect speed control was missing.",
                       "folder": "desktop", "append": True}, "add that to my issues file")
text = saved_path.read_text(encoding="utf-8")
t.check("append adds a second numbered issue without touching the first (one master file, not a second one)",
        r2.success and "## Issue 2" in text and "flickers" in text and "effect speed" in text, text)

r3 = run("save_file", {"name": "LED_strip_issue", "content": "Also: the effect speed control was missing.",
                       "folder": "desktop", "append": True}, "add that to my issues file again")
t.check("appending the exact same text again is recognized as a duplicate and not added twice", r3.success
        and "nothing added" in r3.message and saved_path.read_text(encoding="utf-8").count("## Issue") == 2, r3.message)

t.done("FILES")

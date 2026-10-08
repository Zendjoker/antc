"""Windows UI Automation (the accessibility API): what's in a window, read without screenshots.

Browsers expose their address bar, tabs and the page itself (text, links with their real href, buttons, fields) this
way, so elements are found by what they are and acted on directly (Invoke, SetValue), not by screen coordinates.
Chromium builds the page's accessibility tree only once a client asks, so the first lookup on a page retries briefly.

Elements are returned as `El` snapshots (plain values + the COM element for acting on it). COM is initialised per
thread on first use.
"""

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("room-agent")
_local = threading.local()

KINDS = {"link": "Hyperlink", "button": "Button", "edit": "Edit", "tab": "TabItem", "menuitem": "MenuItem",
         "checkbox": "CheckBox", "radio": "RadioButton", "listitem": "ListItem", "combo": "ComboBox",
         "document": "Document", "text": "Text", "image": "Image"}


def api():
    """(IUIAutomation, the generated module) for this thread."""
    if getattr(_local, "auto", None) is None:
        import comtypes
        import comtypes.client

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
        except OSError:
            pass  # (already initialised on this thread)
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as U

        _local.U = U
        _local.auto = comtypes.client.CreateObject(U.CUIAutomation, interface=U.IUIAutomation)
    return _local.auto, _local.U


@dataclass
class El:
    name: str
    kind: str                       # "Hyperlink", "Button", "Edit", ...
    href: str = ""                  # links: where it goes
    value: str = ""                 # fields: what's in them
    rect: tuple = (0, 0, 0, 0)      # left, top, right, bottom (physical pixels)
    offscreen: bool = False
    enabled: bool = True
    password: bool = False
    automation_id: str = ""
    raw: Any = field(default=None, repr=False)


def _kind_id(U, kind):
    return getattr(U, f"UIA_{KINDS.get(kind, kind)}ControlTypeId")


def _kind_name(U, type_id):
    return next((v for v in KINDS.values() if getattr(U, f"UIA_{v}ControlTypeId", None) == type_id), str(type_id))


def window(hwnd):
    auto, _ = api()
    return auto.ElementFromHandle(hwnd)


def _value(U, e):
    try:
        return e.GetCurrentPattern(U.UIA_ValuePatternId).QueryInterface(U.IUIAutomationValuePattern).CurrentValue or ""
    except Exception:
        return ""


def snap(e, U=None, with_value=True):
    U = U or api()[1]
    r = e.CurrentBoundingRectangle
    kind = _kind_name(U, e.CurrentControlType)
    val = _value(U, e) if with_value else ""
    return El(name=(e.CurrentName or "").strip(), kind=kind, href=val if kind == "Hyperlink" else "",
              value=val if kind != "Hyperlink" else "", rect=(r.left, r.top, r.right, r.bottom),
              offscreen=bool(e.CurrentIsOffscreen), enabled=bool(e.CurrentIsEnabled), password=bool(e.CurrentIsPassword),
              automation_id=e.CurrentAutomationId or "", raw=e)


def find(root, kinds, limit=400):
    """Descendants of `root` (an element or hwnd) of the given kinds, in document order."""
    auto, U = api()
    if isinstance(root, int):
        root = window(root)
    conds = [auto.CreatePropertyCondition(U.UIA_ControlTypePropertyId, _kind_id(U, k)) for k in kinds]
    cond = conds[0]
    for c in conds[1:]:
        cond = auto.CreateOrCondition(cond, c)
    found = root.FindAll(U.TreeScope_Descendants, cond)
    return [snap(found.GetElement(i), U) for i in range(min(found.Length, limit))]


def document(hwnd, wait=2.5):
    """The page in a browser window (the active tab), or None. Retries: Chromium builds it on first request."""
    auto, U = api()
    root = window(hwnd)
    cond = auto.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_DocumentControlTypeId)
    deadline = time.time() + wait
    while True:
        found = root.FindAll(U.TreeScope_Descendants, cond)
        docs = [found.GetElement(i) for i in range(found.Length)]
        docs = [d for d in docs if not d.CurrentIsOffscreen] or docs
        if docs:
            return max(docs, key=lambda d: (d.CurrentBoundingRectangle.right - d.CurrentBoundingRectangle.left)
                       * (d.CurrentBoundingRectangle.bottom - d.CurrentBoundingRectangle.top))
        if time.time() >= deadline:
            return None
        time.sleep(0.3)


ADDRESS_NAMES = ("address field", "address and search bar", "search or enter address", "address bar", "search or type")


def address(hwnd):
    """The URL in a browser window's address bar ('' if it can't be read). Chromium may show it without https://."""
    try:
        edits = [e for e in find(hwnd, ["edit"], limit=40) if e.value]
    except Exception as e:
        log.debug("address bar not readable: %s", e)
        return ""
    for want in ADDRESS_NAMES:
        hit = next((e for e in edits if want in e.name.lower()), None)
        if hit:
            return hit.value
    hit = next((e for e in edits if e.automation_id in ("urlbar-input",)), None)
    return hit.value if hit else ""


def tabs(hwnd):
    """[(title, El, selected)] of a browser window's tabs."""
    _, U = api()
    out = []
    for t in find(hwnd, ["tab"], limit=200):
        selected = False
        try:
            selected = bool(t.raw.GetCurrentPattern(U.UIA_SelectionItemPatternId)
                            .QueryInterface(U.IUIAutomationSelectionItemPattern).CurrentIsSelected)
        except Exception:
            pass
        out.append((t.name, t, selected))
    return out


def page_text(doc, limit=200_000):
    _, U = api()
    try:
        tp = doc.GetCurrentPattern(U.UIA_TextPatternId).QueryInterface(U.IUIAutomationTextPattern)
        return (tp.DocumentRange.GetText(limit) or "").strip()
    except Exception:
        return ""


def runtime_id(e):
    try:
        return tuple(e.GetRuntimeId())
    except Exception:
        return ()


def invoke(el):
    """Activate an element by its accessibility action (no mouse). -> how, or '' if it has no such action."""
    _, U = api()
    e = el.raw if isinstance(el, El) else el
    for pid, iface, call in ((U.UIA_InvokePatternId, U.IUIAutomationInvokePattern, "Invoke"),
                             (U.UIA_SelectionItemPatternId, U.IUIAutomationSelectionItemPattern, "Select"),
                             (U.UIA_TogglePatternId, U.IUIAutomationTogglePattern, "Toggle"),
                             (U.UIA_LegacyIAccessiblePatternId, U.IUIAutomationLegacyIAccessiblePattern, "DoDefaultAction")):
        try:
            getattr(e.GetCurrentPattern(pid).QueryInterface(iface), call)()
            return call
        except Exception:
            continue
    return ""


def scroll_into_view(el):
    _, U = api()
    try:
        el.raw.GetCurrentPattern(U.UIA_ScrollItemPatternId).QueryInterface(U.IUIAutomationScrollItemPattern).ScrollIntoView()
        return True
    except Exception:
        return False


def focus(el):
    try:
        el.raw.SetFocus()
        return True
    except Exception:
        return False


def set_value(el, text):
    """Fill a field directly (no keystrokes). -> True if the field took it."""
    _, U = api()
    try:
        vp = el.raw.GetCurrentPattern(U.UIA_ValuePatternId).QueryInterface(U.IUIAutomationValuePattern)
        if vp.CurrentIsReadOnly:
            return False
        vp.SetValue(text)
        return True
    except Exception:
        return False


def read_value(el):
    return _value(api()[1], el.raw)


def scroll_info(doc):
    """(vertical percent 0-100, or None if the page doesn't say)."""
    _, U = api()
    try:
        sp = doc.GetCurrentPattern(U.UIA_ScrollPatternId).QueryInterface(U.IUIAutomationScrollPattern)
        pct = sp.CurrentVerticalScrollPercent
        return None if pct < 0 else float(pct)
    except Exception:
        return None


def scroll(doc, down=True, big=False):
    """Scroll a page by its accessibility scroll action. -> True if the page accepted it."""
    _, U = api()
    try:
        sp = doc.GetCurrentPattern(U.UIA_ScrollPatternId).QueryInterface(U.IUIAutomationScrollPattern)
        amount = (U.ScrollAmount_LargeIncrement if big else U.ScrollAmount_SmallIncrement) if down else \
                 (U.ScrollAmount_LargeDecrement if big else U.ScrollAmount_SmallDecrement)
        sp.Scroll(U.ScrollAmount_NoAmount, amount)
        return True
    except Exception:
        return False


def focused():
    auto, U = api()
    try:
        return snap(auto.GetFocusedElement(), U)
    except Exception:
        return None

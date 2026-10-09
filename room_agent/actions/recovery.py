"""Bounded self-correction for run_task steps (actions/tasks.py calls attempt() after a step FAILED).

    1. classify the failure (actions/tasks: transient / not found / wrong result / refused / unknown outcome)
    2. had it external effects? A state-changing step whose outcome is UNKNOWN, anything refused by a rule, a
       permission or the user, a cancelled task: never recovered here (nothing is retried blindly)
    3. a safe alternative, if one exists for this tool and this failure - every action goes through the same executor
       (permissions, confirmations, constraints, verification), read-only lookups first:
         get_weather down          -> web_search for the weather there
         open_url wrong page/down  -> a search restricted to THE SAME SITE, then that page (re-checked)
         read_file not found       -> find_files by its name, then read the match
         research found nothing    -> research again, deeper, with the question's key words
         save_file: folder missing -> make the folder their request named, then save
       an alternative that a past experience recorded as working for this failure is tried first (cognition/experience)
    4. at most one alternative per step and TASK_MAX_RECOVERIES per task; the step is COMPLETED only if the
       alternative's result passes the step's own check, and it's labelled "recovered via ..."
"""

import os
import re
from pathlib import Path
from urllib.parse import urlparse

NOT_FOUND = ("no file matching", "not there", "doesn't exist", "isn't there", "not found", "no such")
DOWN = ("couldn't connect", "network", "unreachable", "didn't answer", "timed out", "timeout", "is down", "503", "502")


def classify(step, result_text, check_failed=False):
    m = (result_text or "").lower()
    if step["state"] == "UNKNOWN" or m.startswith("unknown"):
        return "unknown_outcome"
    if "not done: they said" in m or "they didn't ask" in m or "constraint" in m:
        return "refused"
    if check_failed or "success check failed" in m:
        return "wrong_result"
    if any(w in m for w in NOT_FOUND):
        return "not_found"
    if any(w in m for w in DOWN):
        return "unavailable"
    if "there's no folder" in m:
        return "missing_folder"
    if m.startswith(("needs", "unavailable")):
        return "needs_input"
    return "failed"


def _same_site(original, query):
    from room_agent.tools import web

    host = urlparse(original).hostname or ""
    try:
        results = web.search(query, news=False) or []
    except Exception:  # noqa: BLE001
        return None, f"the search for '{query}' failed"
    for r in results[:8]:
        url = r.get("href") or r.get("url") or ""
        if url.startswith("http") and (urlparse(url).hostname or "") == host:
            return url, f"found on the same site ({host})"
    return None, f"no result on {host} for '{query}'"


def alternatives(step, kind):
    """-> [(label, [(tool, args) ...])] safe alternatives for this failure, best first (no execution here)."""
    tool, a = step["tool"], step["args"] or {}
    out = []
    if tool == "get_weather" and kind in ("unavailable", "failed"):
        where = a.get("location") or ""
        out.append(("web_search", [("web_search", {"query": f"weather today {where}".strip()})]))
    if tool == "open_url" and kind in ("wrong_result", "unavailable", "not_found", "failed"):
        chk = step.get("check") or {}
        words = " ".join(str(v) for v in chk.values()) if chk else ""
        out.append(("same-site search", [("_same_site", {"site": a.get("site", ""), "query": words})]))
    if tool == "read_file" and kind == "not_found" and a.get("file"):
        name = Path(str(a["file"]).replace("\\", "/")).name
        out.append(("find_files", [("find_files", {"query": name}), ("read_file", {"number": 1})]))
    if tool == "research_web" and kind in ("wrong_result", "failed", "unavailable"):
        q = re.sub(r"[^\w\s.+-]", " ", str(a.get("question", "")))
        out.append(("deeper research", [("research_web", {"question": " ".join(q.split()[:10]), "depth": "deep"})]))
    if tool == "save_file" and kind == "missing_folder":
        folder = str(a.get("folder", ""))
        parent, _, name = folder.replace("\\", "/").rpartition("/")
        if name and parent:
            out.append(("make the folder", [("make_folder", {"name": name, "folder": parent}), ("save_file", dict(a))]))
    return out


def attempt(t, s, plan, result_text, check_failed, run_step, run_check, budget_left):
    """Try ONE safe alternative for failed step s. -> (recovered: bool, note). run_step(tool, args) -> ActionResult."""
    from room_agent import config

    if not config.TASK_RECOVERY or budget_left <= 0 or s.get("recovered_via") is not None:
        return False, ""
    kind = classify(s, result_text, check_failed)
    if kind in ("unknown_outcome", "refused", "needs_input"):
        return False, ""
    alts = alternatives(s, kind)
    if not alts:
        return False, ""
    alts = _prefer_known(s["tool"], kind, alts)
    label, actions = alts[0]
    s["recovered_via"] = label
    last = None
    for tool, args in actions:
        if tool == "_same_site":
            url, why = _same_site(args["site"], (args["query"] + " " + (urlparse(args["site"]).hostname or "")).strip())
            if url is None:
                _remember(s["tool"], kind, label, False)
                return False, f"step {s['n']} ({s['tool']}): {why}"
            tool, args = "open_url", {"site": url}
        last = run_step(tool, args)
        if last is None or not last.success:
            _remember(s["tool"], kind, label, False)
            return False, f"step {s['n']} ({s['tool']}): the alternative ({label}) didn't work either"
    if s.get("check") and s["tool"] not in ("read_file", "get_weather"):
        ok, why = run_check(s["check"])
        if ok is False:
            _remember(s["tool"], kind, label, False)
            return False, f"step {s['n']} ({s['tool']}): the alternative ({label}) ran but the check still fails ({why})"
    s.update(state="COMPLETED", result=f"recovered via {label}: {last.message[:200]}",
             evidence=(s.get("evidence") or "") + f"; recovered via {label} after: {kind}")
    _remember(s["tool"], kind, label, True)
    return True, f"step {s['n']} ({s['tool']}) failed ({kind}); recovered via {label}"


def _prefer_known(tool, kind, alts):
    """An alternative that worked before for this tool + failure goes first; one that keeps failing goes last."""
    try:
        from room_agent.cognition import experience_v2 as xp

        score = {lab: xp.pattern_score(tool, kind, lab) for lab, _ in alts}
        return sorted(alts, key=lambda x: -score.get(x[0], 0))
    except Exception:  # noqa: BLE001
        return alts


def _remember(tool, kind, label, ok):
    try:
        from room_agent import config
        from room_agent.cognition import experience_v2 as xp

        if config.TASK_EXPERIENCE:
            xp.record_pattern(tool, kind, label, ok)
    except Exception:  # noqa: BLE001
        pass


_ = os

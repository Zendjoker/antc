"""Named crash points for the recovery tests (tests/test_recovery.py).

hit(name) does nothing in normal use. Only when the automated tests run (JARVIS_TEST=1) AND JARVIS_CRASH_AT names this
point, the process ends on the spot with os._exit: no finally blocks, no flushing, no rollback - the same as the power
going out or the process being killed. The tests then start a fresh process and check what recovery does.

Points (in execution order):
    before_operation        a step is about to be claimed
    during_operation        inside a step handler (test workflows)
    after_external_response a paid provider answered; nothing about it is persisted yet
    during_settlement       inside the ledger transaction, between its two updates (not committed)
    before_db_commit        a step's work is done; its completion isn't recorded yet
    after_db_commit         a step's completion (and its follow-up steps) is committed
    before_site_swap        a demo-site edit's paid result is stored; the new version isn't applied yet
    during_site_swap        between the two directory renames of a demo-site update
    after_site_swap         the new site version is live; the edit isn't recorded yet
    after_gmail_draft       Gmail created the draft; the approval isn't marked done yet
"""

import os


def hit(name):
    if os.environ.get("JARVIS_TEST") == "1" and os.environ.get("JARVIS_CRASH_AT") == name:
        os._exit(86)

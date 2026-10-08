"""Computer interaction: browsers, page reading, clicking / typing, on-demand screen vision, web research.

Most reliable first, and each step only when the one before can't do it:
    1. launch the browser with the URL     opening sites and searches: a new tab, the user's tabs untouched
    2. accessibility (UI Automation)        reading the address bar, tabs, page text and links; clicking a link or
                                            button and filling a field by what it IS, not where it is
    3. keyboard input                       back / forward / refresh / new tab, Enter to submit a search
    4. screenshot + vision model            only when asked about the screen, with permission; coordinates last

Nothing here is kept as long-term memory: the desktop context (computer/context.py) is in memory only and expires.
"""

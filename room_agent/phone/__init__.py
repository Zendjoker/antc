"""Phone mode: the same Jarvis (brain, memory, email, calendar, tools) on a phone call, and calls to you while you
drive, only when something important comes up. Setup: phone.md.

    twilio.py   Twilio's REST API (place a call, point the number at Jarvis) + checking Twilio's request signatures
    state.py    are you driving? (from your iPhone's Shortcuts) + the phone's GPS
    session.py  one call: what you say comes in as text, Jarvis's reply goes back as text (Twilio speaks it)
    watcher.py  while you drive: what's important enough to call about, without calling twice or too often
    server.py   the small web server Twilio and your iPhone talk to (behind a public HTTPS URL, e.g. Tailscale Funnel)

Security: only your own number (MY_PHONE) is ever answered or called; Twilio's signature is checked on every request;
the iPhone's messages need the secret PHONE_TOKEN. Nothing here can be reached without them.
"""

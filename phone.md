# Phone mode

Already done: Twilio is set up in `.env`, your number is verified, and `PHONE_TUNNEL=cloudflared` makes Jarvis open its own public address on every start.

1. Start Jarvis: `python main.py` (the phone line starts with it).
2. Call Jarvis: **+1 (279) 266-8448**. Or have it call you: `python main.py --test-call`.
3. On a call, say "bye" or "you can hang up" and Jarvis ends the call.

**Later, for the driving calls (needs a fixed address):**
1. Install Tailscale (`winget install --id tailscale.tailscale -e`), sign in, then run `tailscale funnel --bg 8770`.
2. In `.env`: remove `PHONE_TUNNEL=cloudflared` and set `PUBLIC_URL=` to the `https://...ts.net` address.
3. `python main.py --setup-phone` → add the 2 iPhone Shortcuts it prints.

# Phone mode

Already done: Twilio is set up in `.env`, your number is verified, and `PHONE_TUNNEL=cloudflared` makes Jarvis open its own public address on every start.

1. Start Jarvis: `python main.py` (the phone line starts with it).
2. Call Jarvis: **+1 (279) 266-8448**. Or have it call you: `python main.py --test-call`.
3. On a call, say "bye" or "you can hang up" and Jarvis ends the call.

**Later, for the driving calls (needs a fixed address):**
1. Install Tailscale (`winget install --id tailscale.tailscale -e`), sign in, then run `tailscale funnel --bg 8770`.
2. In `.env`: remove `PHONE_TUNNEL=cloudflared` and set `PUBLIC_URL=` to the `https://...ts.net` address.
3. `python main.py --setup-phone` → add the 2 iPhone Shortcuts it prints.

**Errand calls (test mode): Jarvis phones a restaurant for you (`room_agent/phone/errand.py`):**
1. Test call to your own phone (you play the restaurant):
   `.venv\Scripts\python -m room_agent.phone.errand --business "Luigi's" --party 4 --date Friday --from 19:00 --to 20:00 --name Adam`
2. It runs its own small server and tunnel, so the running Jarvis and your number's settings aren't touched.
3. Restricted mode:
   - it says it's an AI assistant and has no tools or access to your data
   - it shares only the brief, never a phone number, email or payment details
   - it accepts only a booking inside your date, party size and time window; anything else is "I'll check and call back"
4. The model (`ERRAND_MODEL`, default `gpt-5` at minimal thinking, ~1-2c a call) runs the conversation; code only
   checks the hard limits above before each sentence is spoken. `ERRAND_MODEL=claude-sonnet-5-5` also works (slower).
5. The outcome is texted to you and saved in `errands.json`. Your calendar isn't changed.
6. Calling real businesses isn't built yet: only your own number is ever called.

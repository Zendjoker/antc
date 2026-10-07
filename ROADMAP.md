# Roadmap

## Improve what exists
1. Test the real model's tool choices (~50 real requests, ~$0.20–0.50).
2. Speed: read the `timing:` lines after a day of use, fix the slowest stage.
3. Small talk fast path (no tools for casual chat).
4. Faster startup (load Whisper in the background).
5. Fix or remove `tests/scenarios.py` and `test_turn_taking.py`.
6. Stop writing "SKIP" memory summaries.
7. Test the local-model fallback when the budget runs out.

## Done: Location awareness (PC)
- Windows location first, then the internet connection's city; weather and "where am I" use it. `LOCATION_SOURCE=off` turns it off.
- Next: the phone's GPS when you're out (comes with the phone companion below).

## New: Jarvis calls you (phone mode)
- Jarvis can call your phone (or you call it) and it's the same Jarvis: same memory, email, calendar, tasks.
- Proactive: "Hey, you got an email from Andrew, want me to read it or reply?"
- Voice confirmations for anything that sends or changes things.
- Likely built on a phone service like Twilio (a phone number + voice line).

## New: Knows when you're driving
- Detect driving: your phone connects to the car's Bluetooth, or phone motion detection, or a car API (e.g. Smartcar/Tesla).
- Driving mode: short answers, read things out loud, no "look at your screen", only important interruptions.
- Decide when it's worth calling you (important email, meeting changed, reminder due).
- Needs: a phone companion (or Shortcuts / Tasker automation) + Gmail/Calendar push notifications.

## Decided
- Phone: iPhone. Driving detection: iPhone Driving Focus / CarPlay connects -> Shortcuts automation -> tells Jarvis
  "driving" (no Apple Watch needed).
- Calls only for important things.

## Done: Phone mode (code + tests; setup in phone.md)
- Call Jarvis's number, or Jarvis calls you while you drive: VIP / Gmail-important email, meeting soon or moved, alarm.
- iPhone Shortcuts tell it you're driving (CarPlay / Driving Focus) and can send your GPS.
- Next: say "always call me if Sarah emails" to add VIPs by voice; quiet hours; a text first instead of a call.

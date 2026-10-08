# JARVIS — FULL RELIABILITY, VOICE, MEMORY & PERFORMANCE FIX

You are working inside my existing Windows-based Jarvis AI assistant project (`room-agent`).

Your job is to investigate, fix, and test the reliability problems identified in a real conversation log.

**This is an implementation task, not a research task.** Inspect the actual codebase, identify the root causes, implement targeted fixes, and run tests.

Do not rewrite the architecture unnecessarily. Do not remove working features. Do not claim a bug is fixed without evidence.

## EXISTING SYSTEM

Jarvis currently uses:

- Python on Windows
- Whisper large-v3-turbo with CUDA for speech recognition
- GPT-5-mini for reasoning
- Local speech synthesis
- Wake-word detection
- Echo cancellation and noise suppression
- Barge-in / speech interruption handling
- Persistent memory and conversation summaries
- Cognitive runtime and tool execution
- Zigbee2MQTT smart-home integration
- Door and environmental sensors
- LED light controls
- Phone-related services

Preserve the working integrations.

---

# ISSUE 1 — FALSE MEMORIES AND INCORRECT LEARNING

## Observed behavior

During a conversation, Jarvis incorrectly interpreted speech as a request for a "dailies list."

The user explicitly corrected Jarvis:

"I didn't say none of this."

Despite this, the system subsequently recorded:

`learned: +Wants a dailies list (daily checklist or habits)`

The conversation summary also described the supposed checklist request as though it were genuine.

## Required changes

1. Trace how transcripts become memories, learned preferences, and conversation summaries.
2. Distinguish confirmed user statements from uncertain transcriptions and assistant-generated assumptions.
3. Prevent inferred preferences from being saved without sufficient evidence.
4. Treat explicit user corrections as high-priority evidence.
5. When the user denies saying something, invalidate the related unconfirmed inference.
6. Prevent conversation summarization from reintroducing invalidated claims.
7. Track provenance for durable memories: user statement, confirmation, correction, tool observation, or model inference.
8. Do not let an assistant's own suggestion become a user preference.
9. Add a correction mechanism for previously learned false information.
10. Keep ordinary conversational context functioning.

## Acceptance tests

- User says "I didn't say that" → related unconfirmed memory is not saved.
- Assistant suggests a checklist → no preference is created.
- User explicitly requests a daily checklist → valid preference may be saved.
- User corrects a mistaken interpretation → the correction survives summarization and restart.
- Uncertain STT output does not silently become permanent personal knowledge.

---

# ISSUE 2 — SPEECH RECOGNITION AND FALSE TRANSCRIPTS

## Observed behavior

Jarvis interpreted fragments of speech as:

- "dailies"
- "Thank you"
- "If you want"
- "And you're in danger"

Some appeared during interruption handling or immediately after TTS playback.

The logs also show rejected and doubtful speech candidates.

## Required changes

1. Inspect the full microphone → VAD → echo cancellation → Whisper → transcript pipeline.
2. Identify whether incorrect transcripts originate from noise, speaker playback, segmentation, or recognition.
3. Improve handling of short, ambiguous utterances.
4. Prevent hallucinated transcripts from being treated as reliable commands.
5. Preserve meaningful short commands such as "stop," "yes," "no," and "wait."
6. Use confidence and audio-quality signals where supported, without assuming Whisper scores are calibrated probabilities.
7. Review the current `logprob` and no-speech rejection thresholds using actual examples.
8. Improve utterance segmentation so incomplete speech is not processed prematurely.
9. Avoid unnecessary repeated transcription of the same audio.
10. Add diagnostic logging that distinguishes raw transcript, accepted transcript, rejected candidate, and rejection reason.

Do not solve this by simply rejecting all short speech.

## Acceptance tests

- Quiet room → no fabricated user messages.
- Jarvis speaking → its own speech does not become a user command.
- User says "stop" → recognized promptly.
- User speaks a complete sentence → captured accurately.
- User pauses briefly mid-sentence → reasonable continuation handling.
- Low-confidence ambiguous speech → no irreversible action or permanent memory.

---

# ISSUE 3 — BARGE-IN / INTERRUPTIONS

## Observed behavior

The logs contain many events like:

`barge-in rejected: too short`

Some candidates contained several voice frames but were repeatedly rejected.

Other interruptions were successfully confirmed, and TTS was canceled.

## Required changes

1. Inspect the existing interruption state machine and audio buffers.
2. Investigate whether VAD state resets too aggressively between adjacent frames.
3. Determine why substantial microphone activity is repeatedly classified as too short.
4. Preserve continuity across short pauses where appropriate.
5. Improve detection of genuine user speech during playback.
6. Avoid accepting Jarvis's own speaker audio as a user interruption.
7. Ensure interruption cancels the current speech output promptly.
8. Ensure canceled TTS audio does not continue playing from stale buffers.
9. Ensure the user's interruption is processed once, not duplicated.
10. Keep the conversation state coherent after cancellation.

Add specific handling for brief interruption commands without making the system overly sensitive to random noises.

## Acceptance tests

- Interrupt with "stop."
- Interrupt with "wait."
- Interrupt with "no."
- Interrupt with a full sentence.
- Speak over Jarvis from the same room.
- Let Jarvis finish without interruption.
- Play audio through the configured speaker and confirm echo does not trigger false interruptions.

Measure interruption detection and cancellation latency.

---

# ISSUE 4 — SMART-HOME SENSOR GROUNDING

## Observed behavior

The logs repeatedly show:

`door opened: no greeting (busy (listening))`

Later, when asked whether it noticed the user entering the room, Jarvis answered:

"Oops, I didn't catch the door or movement—sensors say no movement since I started."

This response was not consistent with the logged door-opening events.

## Required changes

1. Inspect Zigbee2MQTT event ingestion.
2. Trace door events, motion events, sensor state, and their timestamps.
3. Distinguish door-open events from motion detection.
4. Make relevant recent sensor events available to the reasoning system.
5. Ensure Jarvis does not claim a sensor reported something unless supported by actual readings.
6. Represent unavailable, stale, and unknown sensor data explicitly.
7. Preserve event history long enough for natural follow-up questions.
8. Distinguish a suppressed greeting from an undetected event.
9. Avoid repeated greetings while Jarvis is already interacting.
10. Keep smart-home state synchronized across events and tool queries.

## Acceptance tests

- Door opens → event is recorded.
- Greeting suppressed because Jarvis is busy → event remains available.
- User asks "Did you notice me come in?" → answer reflects the observed door event.
- No motion sensor exists → Jarvis does not invent motion readings.
- Sensor offline → Jarvis reports unavailable data instead of guessing.

---

# ISSUE 5 — CAPABILITY-AWARE ACTIONS

## Observed behavior

Jarvis offered:

"Want me to cool it down or open the window?"

When asked to open the window, it admitted no window controller was connected.

It also asked whether to turn off or dim an LED strip when the user had already expressed an apparent intent to turn it off.

## Required changes

1. Inspect tool registration, capability discovery, and action planning.
2. Give the assistant an accurate view of currently available devices and actions.
3. Distinguish installed tools from currently reachable devices.
4. Check capabilities before offering to perform physical actions.
5. Do not claim unsupported actions are available.
6. Avoid unnecessary confirmation questions for clear, low-risk, reversible commands.
7. Preserve confirmation requirements for high-impact, destructive, or security-sensitive actions.
8. Verify tool execution before claiming success.
9. When a capability is unavailable, explain the limitation concisely.
10. Ensure device-state changes are reflected in subsequent responses.

## Acceptance tests

- "Dim the LED to 30%" → execute and verify.
- "Change the LED to blue" → execute and verify.
- "Turn off the LED" → execute directly when the target is clear.
- "Open the window" without a controller → state that it cannot.
- Device disconnected → do not claim success.
- Ambiguous device name → ask only the necessary clarification.

---

# ISSUE 6 — EXCESSIVE INPUT TOKENS AND RESPONSE LATENCY

## Observed behavior

Simple exchanges often used approximately 7,000–10,000 input tokens.

Examples included:

- "You hear me?"
- "Talk normal."
- "Change the color to blue."
- "Turn the light off."

Some tool interactions required multiple model calls.

## Required changes

1. Profile prompt construction for each model call.
2. Measure tokens by component:
   - System instructions
   - Conversation history
   - Tool definitions
   - Memory
   - Sensor context
   - Cognitive runtime
   - Other injected context
3. Identify redundant or duplicated prompt content.
4. Reduce unnecessary history and irrelevant tool exposure.
5. Preserve important user preferences and active-task context.
6. Investigate why prompt caching is inconsistent.
7. Use deterministic execution for simple commands where safe and appropriate.
8. Avoid a second model call when a verified tool result can produce a reliable response without one.
9. Preserve the reasoning model for genuinely complex requests.
10. Add token, latency, and estimated cost measurements by request type.

Do not sacrifice reliability or reasoning quality solely to reduce cost.

## Acceptance tests

- Simple conversational response.
- Direct LED command.
- Multi-step tool workflow.
- Complex reasoning request.
- Follow-up requiring memory.
- Interrupted conversation.

Compare token usage, cost, and response latency before and after changes.

---

# ISSUE 7 — AUDIO SHUTDOWN AND CLEAN EXIT

## Observed behavior

The application ended with:

`KeyboardInterrupt`

during:

`sounddevice.PortAudioStream.stop()`

This occurred inside the audio engine's shutdown path.

## Required changes

1. Inspect `room_agent/audio/engine.py`, particularly the stream cleanup logic.
2. Inspect shutdown orchestration in `room_agent/cli.py`.
3. Determine whether the interruption came from a second Ctrl+C or a shutdown deadlock.
4. Make shutdown idempotent.
5. Stop active recording, playback, background workers, and audio streams in a safe order.
6. Prevent shutdown from hanging indefinitely.
7. Handle interruptions during cleanup gracefully.
8. Preserve useful error reporting for genuine failures.
9. Ensure no unnecessary orphaned processes remain.
10. Avoid suppressing unrelated exceptions.

## Acceptance tests

- Normal application exit.
- Ctrl+C during idle listening.
- Ctrl+C during TTS playback.
- Ctrl+C during speech recognition.
- Repeated Ctrl+C during shutdown.
- Restart after clean shutdown.

---

# ISSUE 8 — CONVERSATION STATE AND CONTEXT ACCURACY

Investigate why Jarvis sometimes continues pursuing a misunderstood topic after the user corrects it.

Implement:

1. Clear separation between user intent, assistant hypothesis, and confirmed task.
2. Proper cancellation or revision of invalidated goals.
3. Explicit handling of "that's not what I said."
4. Reliable follow-up resolution.
5. No automatic continuation of rejected tasks.
6. No repetition of questions the user already answered.
7. Conversation summaries that preserve corrections.
8. Protection against corrupted state after interruption.
9. Appropriate uncertainty when context is ambiguous.
10. State transitions that can be inspected in logs.

Test the exact sequence:

Assistant misunderstands → user corrects → assistant acknowledges → incorrect task is discarded → later conversation remains accurate.

---

# IMPLEMENTATION RULES

**Do not:**

- Rewrite the whole project.
- Replace Whisper, GPT-5-mini, or the TTS engine without evidence that replacement is necessary.
- Install unrelated AI models.
- Start researching emotion classifiers.
- Modify unrelated UI components.
- Remove working Zigbee, phone, wake-word, or memory functionality.
- Hardcode responses to the specific log examples.
- Invent tool results or simulated success.
- Make unsupported claims about latency improvements.
- Treat passing mocked tests as proof that physical hardware works.

**Do:**

- Inspect existing architecture first.
- Identify exact files and functions responsible.
- Fix root causes rather than symptoms.
- Add regression tests for every issue.
- Use mocks for reproducible automated tests and hardware tests where available.
- Keep backward compatibility.
- Preserve current configuration and existing user data.
- Avoid exposing credentials, phone numbers, private addresses, or tokens in diagnostic logs.
- Implement changes incrementally.
- Run the relevant test suite after each major change.

---

# REQUIRED WORKFLOW

## Phase 1 — Audit

Inspect the project and map each issue to its actual code path.

Produce a concise table:

Issue | Root cause or hypothesis | Relevant files | Proposed fix | Risk

Distinguish verified causes from hypotheses.

## Phase 2 — Implementation

Fix issues in this priority order:

1. False memories and incorrect learning
2. Speech recognition and hallucinated transcripts
3. Barge-in reliability
4. Sensor grounding
5. Capability-aware execution
6. Conversation state accuracy
7. Token usage and latency
8. Clean shutdown

Do not stop after the audit. Proceed with implementation unless a decision genuinely requires my input.

## Phase 3 — Testing

Create or update automated tests.

Run them and report actual results.

If a test requires physical hardware or live API access, explain that clearly and provide an exact manual test procedure.

## Phase 4 — Final Report

Return:

1. Root causes confirmed
2. Files changed
3. Fixes implemented
4. Tests passed and failed
5. Before/after performance measurements, if measured
6. Remaining risks
7. Exact commands to launch Jarvis and reproduce the verification tests

**Definition of done:** Jarvis must no longer turn user corrections into false memories, must handle interruptions more reliably, must ground claims in real device observations, must avoid offering unavailable actions, and must preserve its existing working capabilities.

Start by inspecting the codebase. Then implement and test the fixes.
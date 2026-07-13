# RomanVoice North Star

Status: active product direction as of 2026-07-13.

This document defines what RomanVoice must reliably do. `AGENTS.md` contains
agent guardrails, and `README.md` contains setup and operating instructions.
When those documents or the current code disagree with this North Star, call
out the disagreement and resolve it deliberately rather than preserving drift.

## Primary outcome

Roman can dictate into the text field he is already using, with low friction
and without losing, duplicating, or unexpectedly exposing his words.

RomanVoice has two supported everyday surfaces:

1. On Windows, `Ctrl+Space` starts and stops local dictation from the hidden tray
   app, and the result is inserted into the focused field.
2. On the Pixel, the RomanVoice Quick Settings tile starts and stops dictation
   while the preferred keyboard remains active, and the floating accessibility
   service inserts the result into the focused editable field.

The phone surface is expected to work both at home and away from home whenever
the home PC is online and the private RomanVoice path is healthy.

## Product contract

### Local-first transcription

- The home PC owns Faster-Whisper, the loaded model, transcription history, and
  the dictation service.
- Audio and transcripts remain local to Roman's devices and private network by
  default.
- Cloud transcription, a public Internet gateway, and multi-user accounts are
  not part of the default product.

### Windows dictation

- The everyday app starts hidden through `scripts\romanvoice.cmd`.
- `Ctrl+Space` remains the normal start/stop hotkey.
- Focused-field insertion is the default outcome. Clipboard copy is opt-in and
  clipboard paste is a fallback.
- A stop, retry, partial update, or final reconciliation must not duplicate text.
- Quiet but non-empty audio continues to Whisper. Truly empty audio fails with
  an actionable message.

### Phone dictation

- The Quick Settings tile is the preferred trigger.
- The floating accessibility service owns focused-field insertion while Gboard,
  SwiftKey, or another preferred keyboard remains selected.
- The user must always have a visible cancel path during recording.
- Replacement partials and the final transcript must reconcile without
  duplication, truncation, or overwriting unrelated text.
- Losing focus, losing connectivity, process recreation, or a late final must
  fail safely and preserve text already committed by the user.

### Connectivity away from home

- Tailscale is the supported private transport between the Pixel and home PC
  when the phone is off the home Wi-Fi.
- The normal phone configuration uses the PC's stable Tailscale address, not a
  `192.168.x.x` home-LAN address and not a public port-forwarded address.
- Tailscale must be connected on both devices, the RomanVoice desktop service
  must be running, and the phone must have the matching bearer token.
- A LAN-only URL is an explicit temporary/developer mode. Provisioning must not
  silently leave the phone in a home-only configuration that looks fully ready.
- Before recording, the phone surface should distinguish at least these states:
  ready, PC/service unreachable, Tailscale/private path unavailable, auth
  rejected, microphone unavailable, and accessibility insertion unavailable.
  A generic stale `Start` or `Ready` state is not sufficient evidence of health.

### Authentication and exposure

- The service remains authenticated for every health, heartbeat, batch, and
  streaming endpoint that carries operational or dictation data.
- The current single-user private-network design uses a high-entropy bearer
  token. OAuth is not required for this architecture because there is no public
  multi-user identity boundary.
- The token must never be committed, logged, placed in screenshots, or exposed
  through an unauthenticated diagnostic endpoint.
- No public router port forwarding is part of the supported design. Any future
  public gateway, cloud relay, shared-user model, or OAuth flow requires an
  explicit architecture and security decision.

## Reliability and custody rules

- One hidden tray process owns the model and phone-facing service. Do not create
  competing service owners or duplicate launch paths.
- The canonical source checkout is this repository. Startup scripts, watchdogs,
  Android build/install scripts, and documentation must point here.
- A local-only commit, dirty worktree fix, manually edited phone preference, or
  one-time ADB command is not a shipped fix. Durable behavior belongs in source,
  tests, provisioning, and documentation.
- The installed Android build must be identifiable by version and build time.
  Reinstalling must preserve or deliberately migrate the correct Tailscale URL,
  token, permission, accessibility, tile, and preferred-keyboard state.
- `pyproject.toml` plus `uv.lock` are the Python dependency source of truth.
  Any compatibility `requirements.txt` must stay synchronized or be clearly
  marked secondary.
- Operational health is end to end. A live desktop process alone is not proof
  that the phone can record, authenticate, transcribe, and insert text.

## Release gates

A phone-facing release is not complete until these scenarios are either
automated or manually evidenced:

1. Home Wi-Fi dictation into a real focused field.
2. Cellular-only dictation through Tailscale into a real focused field.
3. Tailscale off or PC unreachable produces a specific, actionable state.
4. Wrong or missing token produces an auth-specific state without leaking it.
5. Microphone denied and accessibility disabled are identified before recording.
6. Start, stop, cancel, rapid repeat, short speech, long speech, and connection
   loss do not duplicate or overwrite unrelated text.
7. Android process recreation and app reinstall do not silently revert the
   phone to a LAN-only or placeholder URL.

For Windows-facing changes, run the focused tests for the touched area, the full
Python suite, and a real focused-text-box smoke test when insertion changes.
For Android-facing changes, build and signature-verify the APK, inspect the
installed package and settings over ADB, and perform the relevant real-device
scenario above.

## Sources of truth

- Product direction: `docs/NORTH_STAR.md`
- Agent and architecture constraints: `AGENTS.md`
- Setup, launch, recovery, and operator commands: `README.md` and
  `clients/android-ime/README.md`
- Python environment: `pyproject.toml` and `uv.lock`
- Desktop runtime configuration: `%APPDATA%\RomanVoice\config.json` plus
  explicit `ROMANVOICE_*` environment overrides
- Desktop bearer token: `%APPDATA%\RomanVoice\service_token.txt`
- Token rotation: `scripts\rotate-romanvoice-service-token.ps1`, followed by
  immediate reprovisioning and restart of every known consumer.
- Phone endpoint and token: RomanVoice Android app preferences, provisioned by
  `clients/android-ime/install-to-connected-phone.ps1`

## Decision rule

Prefer the smallest change that restores this end-to-end contract. Do not solve
a phone reachability failure by adding a public cloud dependency, weakening
authentication, changing the Windows ownership model, or replacing local
Whisper unless Roman explicitly approves that new direction.

# AGENTS.md

RomanVoice is a local-first Windows dictation app forked from OpenWhisper. The
product goal is a hidden, hotkey-first dictation loop: press `Ctrl+Space`, speak
into the selected microphone, transcribe locally with Faster-Whisper, insert the
result into the focused Windows text field, and keep clipboard copy available as
an opt-in behavior.

This file is for coding agents working in this repository. Keep changes aligned
with the current Windows desktop shape unless Roman explicitly approves a new
product or architecture direction.

## Core Constraints

- Treat Windows desktop dictation as the primary target. Do not start a
  cross-platform rewrite or web-app rewrite unless Roman explicitly asks for it.
- Keep the app local-first. Faster-Whisper/local CUDA is the primary
  transcription path; OpenAI/API-era upstream text in README is historical unless
  current code proves otherwise.
- Keep the normal runtime hidden/background by default. The everyday launcher is
  `scripts\romanvoice.cmd`; `scripts\romanvoice-ui.cmd` is only for explicit UI,
  settings, history, or debugging work.
- Preserve `Ctrl+Space` as the start/stop hotkey unless the task is explicitly
  about changing hotkeys. Do not interfere with unrelated Windows shortcuts such
  as `Win+Shift+S`.
- Do not modify unrelated startup helpers. In particular, leave any non-RomanVoice
  scripts such as `start_whisper_watcher.vbs` alone.
- Ask before moving the repo. Startup watchdog paths can point directly at this
  checkout.

## Runtime Facts

- Use Python 3.12 through `uv`. The machine default `python` may resolve to a
  newer version that is not appropriate for CTranslate2/Faster-Whisper wheels.
- Current target stack: `faster-whisper==1.2.1`, `ctranslate2==4.7.1`, CUDA
  `float16`, and the `turbo` model on supported NVIDIA GPUs.
- Durable app data belongs outside the repo:
  - `%APPDATA%\RomanVoice\config.json`
  - `%APPDATA%\RomanVoice\history.sqlite`
  - `%APPDATA%\RomanVoice\service_token.txt`
  - `%LOCALAPPDATA%\RomanVoice\recordings`
  - `%LOCALAPPDATA%\RomanVoice\romanvoice.log`
- The tray/background app owns the local dictation service. Config defaults to
  `127.0.0.1:8799`, but the background launcher sets
  `ROMANVOICE_SERVICE_HOST=0.0.0.0` so Roman's Pixel can reach the service over
  the local/private network. Keep the service authenticated with a bearer token
  and in-process with the tray app unless Roman approves a different ownership
  model.
- The service exposes `POST /v1/transcribe` for batch audio and
  `GET /v1/transcribe/stream` for authenticated WebSocket streaming. Streaming
  clients send PCM16 mono chunks and receive replacement partials plus a final
  transcript.
- The Android client in `clients/android-ime` has two phone input surfaces: a
  full RomanVoice IME and an opt-in `RomanVoice Floating Mic` accessibility
  service that keeps SwiftKey/Gboard active while inserting text into the
  focused editable field.
- The known preferred microphone path is the WASAPI default resolving to
  `Microphone (3- Razer Kiyo)` when that device is present.
- The currently working startup fallback is the Startup-folder VBS watchdog:
  `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\RomanVoice Background Watchdog.vbs`
  running `scripts\watch-romanvoice-background.ps1`.
- Task Scheduler registration may fail with `Access denied`; that is not a
  blocker if the Startup-folder watchdog is installed and verified.

## Product Behavior To Preserve

- Text insertion is a core product behavior, not a preview-only feature.
  Streaming partials should reconcile into the focused text field, and final
  completion should reconcile or insert the final transcript.
- Keep clipboard copy available as an opt-in setting, but do not make it active
  by default. The normal stop-time outcome is focused-field insertion.
- Use Windows `SendInput` with `KEYEVENTF_UNICODE` as the primary typing path.
  Clipboard paste is a fallback for long text, failed Unicode insertion, or an
  explicit clipboard mode.
- If the final VAD-filtered batch transcript is empty but a streaming transcript
  exists, use the latest streaming text rather than dropping the dictation.
- Quiet recordings should continue to Whisper when there are samples and signal.
  Only fail fast on truly empty/no-sample input.
- Avoid duplicate insertion. If partial live text was already typed and final
  reconciliation fails, do not blindly paste the full final transcript on top of
  the partial text.

## Feature Gates

Use a Feature Gate before non-trivial coding only when a new product or
architecture choice is needed. A Feature Gate should name the scenario and the
choice being made, then wait for approval.

Do not stop for every small implementation detail after a direction has already
been approved. For conservative bug fixes, tests, refactors within the existing
runtime shape, docs, and verification work, proceed independently.

Examples that need a Feature Gate:
- Replacing Faster-Whisper with a different transcription engine.
- Changing the default hotkey model.
- Moving the repo or changing startup ownership paths.
- Switching from the hidden desktop app model to a service, web app, or broker.
- Adding cloud transcription or external network dependencies to the default path.

Examples that do not need a Feature Gate:
- Fixing `SendInput` struct definitions or fallback timing.
- Adding tests for live typing, clipboard copy, watchdog scripts, or settings.
- Updating README/AGENTS documentation to match already-approved behavior.
- Small UI/debug settings changes that expose existing config.

## Verification Expectations

Run focused tests for the area you touch, then the full suite before handoff when
code changes are non-trivial.

Baseline commands:

```powershell
uv run python -m compileall config.py services transcriber ui_qt scripts
uv run pytest
```

Useful focused commands:

```powershell
uv run pytest tests/test_text_injector.py tests/test_application_controller.py
uv run pytest tests/test_win32_hotkey_manager.py tests/test_hotkey_manager.py
uv run pytest tests/test_recorder.py tests/test_settings.py tests/test_polisher.py
```

For Windows input changes, also run a real focused text-box smoke test when
possible. A unit test proving `SendInput` returned success is not enough; verify
that text actually appears in a focused control. For clipboard fallback, make
sure the target app has time to consume paste before the clipboard is restored.

For startup/background work, verify all of the following:
- A live `pythonw.exe app_qt.py` RomanVoice process exists.
- The app starts hidden and logs `Main window hidden on startup; running from tray`.
- `Ctrl+Space` is registered by the Win32 hotkey backend.
- The watchdog log sees the current RomanVoice pid.
- The Startup-folder VBS still points at this checkout if the repo was not moved.

Check logs directly:

```powershell
Get-Content "$env:LOCALAPPDATA\RomanVoice\romanvoice.log" -Tail 120
Get-Content "$env:LOCALAPPDATA\RomanVoice\startup-watchdog.log" -Tail 80
```

## Manual Testing Boundary

Do as much verification as possible locally before returning. Only stop for Roman
when the next step requires one of these:
- Real spoken dictation into a target app such as Notepad, browser, or VS Code.
- Credentials or external account access.
- Deployment/publishing confirmation.
- A genuine product decision that cannot be inferred from this file, README, the
  current code, or prior approvals.

When handing off for manual testing, say exactly what is already verified and
what remains manual.

## Git Hygiene

- Keep commits clean and feature-scoped.
- Do not revert unrelated user changes.
- Do not use destructive git commands such as `git reset --hard` unless Roman
  explicitly requests that exact operation.
- Commit docs-only changes separately from runtime behavior changes when it keeps
  history easier to review.
- Leave generated app data, logs, recordings, and local config out of git.

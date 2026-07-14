# RomanVoice Android IME

This is a native Android keyboard client for the RomanVoice tray app. It records
mono PCM16 audio at 16 kHz, streams it to RomanVoice over an authenticated
WebSocket, writes live partials into the active Android text field with
`InputConnection.setComposingText()`, then commits the final transcript with
`InputConnection.commitText()`.

It also includes an opt-in floating mic accessibility service. That path keeps
your normal keyboard, such as Gboard or SwiftKey, active while a small draggable
RomanVoice button inserts dictated text into the focused editable field.

The preferred phone trigger is the RomanVoice Quick Settings tile. Add the tile
from RomanVoice Settings or Android's notification shade editor, then tap it to
start listening and tap it again to stop and insert. The tile shows `Connecting`,
`Listening`, `Ready`, or a persistent actionable error. `Ready` follows an
authenticated service preflight. Tap an error state to retry, and tap an active
connecting, listening, or finishing state to cancel or stop as appropriate. The
floating service still needs to be enabled because it owns focused-field
insertion, but its overlay stays hidden while idle and appears during active
work as a small status/cancel pill.

## Service URL

RomanVoice config still defaults to `127.0.0.1:8799`, which a phone cannot
reach. The normal background launchers set `ROMANVOICE_SERVICE_HOST=0.0.0.0`
unless you override it, so the Pixel can reach the desktop service on the
local/private network. To persist that behavior for other launch methods, set:

```powershell
[Environment]::SetEnvironmentVariable("ROMANVOICE_SERVICE_HOST", "0.0.0.0", "User")
```

Then restart RomanVoice. Keep Tailscale enabled and use the PC's Tailscale IP in
the IME settings:

```text
ws://<PC_TAILSCALE_IP>:8799/v1/transcribe/stream
```

The service requires the bearer token stored at:

```text
%APPDATA%\RomanVoice\service_token.txt
```

Do not paste that token into untrusted apps or URLs.

## Build

Open this folder in Android Studio:

```text
clients/android-ime
```

Build and install the `app` module on the Pixel 7. After install:

1. Open RomanVoice Settings and grant microphone permission.
2. Paste the streaming URL and token.
3. Enable the keyboard in Android system keyboard settings.
4. Select RomanVoice as the current keyboard in a text field.

For the floating mic path, keep your normal keyboard selected and enable
`RomanVoice Floating Mic` in Android Accessibility settings. The RomanVoice
Settings screen has a shortcut button to open that system settings page.
Then tap `Add RomanVoice Quick Settings tile` in RomanVoice Settings, or add the
`RomanVoice` tile from Android's tile editor. If the tile is tapped before the
floating service is enabled, it opens Accessibility settings.

The IME checks `/v1/health` whenever the keyboard opens. If RomanVoice is not
reachable, it shows `RomanVoice offline` before recording starts and offers a
keyboard switch button.

The floating mic service also sends an authenticated heartbeat to
`/v1/phone/heartbeat` while it is active. The desktop service exposes
`/v1/phone/status` so host checks can tell the difference between "RomanVoice is
running on Windows" and "the phone Quick Settings tile is actually backed by an
active floating service." Run this from the repo root when the tile looks dark,
unavailable, stale, or when you want to prove the full phone-to-PC path:

```powershell
.\scripts\check-phone-tile-health.ps1
```

The health gate requires the Pixel to be connected over USB debugging. It checks
the installed package, microphone permission, private preference permissions,
configured stream URL, token fingerprint, Tailscale/VPN state, accessibility
service, heartbeat, and an authenticated WebSocket upgrade originating on the
phone. `-RequireAdbDevice` remains accepted for compatibility. Use
`-AllowLanOnly` only for an intentional home-only diagnostic.

For command-line install after USB debugging is enabled:

```powershell
.\build-debug-apk.ps1
.\install-to-connected-phone.ps1
```

The install script reads `%APPDATA%\RomanVoice\service_token.txt`, installs the
debug APK, grants microphone permission, and preloads the IME settings without
writing token-bearing temporary files. It requires the PC's Tailscale address so
the phone can keep reaching RomanVoice on 5G and fails instead of silently
falling back to home Wi-Fi. Pass `-PreferLan` only when you intentionally want a
home-only URL. By default it preserves or restores the normal keyboard, preferring
SwiftKey when RomanVoice was already active, and enables/verifies the floating
mic service through ADB for development testing. Pass `-SetRomanVoiceKeyboard`
when you want the full RomanVoice keyboard selected instead.
The debug APK is signed with a durable local keystore at
`%APPDATA%\RomanVoice\android-ime-debug.keystore` so later local rebuilds can
update the installed app without a clean uninstall. If a mismatched older debug
build is already installed, the install script uninstalls and reinstalls the
RomanVoice IME package before reloading settings.

Android version, SDK, and build-tools metadata have one source at
`version.properties`. RomanVoice Settings displays the installed version code
and name so a stale APK can be identified without guessing.

For phone-side debugging while the Pixel is connected with USB debugging:

```powershell
$adb = "$env:LOCALAPPDATA\Android\Sdk\platform-tools\adb.exe"
& $adb logcat -s RomanVoiceIme
```

## Protocol

The IME connects to:

```text
GET /v1/transcribe/stream
Authorization: Bearer <token>
Upgrade: websocket
```

Client messages:

```json
{"type":"start","sample_rate":16000,"channel_count":1,"sample_format":"pcm_s16le","polish":"settings"}
```

Binary frames are little-endian PCM16 mono chunks. The client finishes with:

```json
{"type":"stop"}
```

RomanVoice returns `partial` replacement messages while recording and a `final`
message on stop.

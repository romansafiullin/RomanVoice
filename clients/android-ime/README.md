# RomanVoice Android IME

This is a native Android keyboard client for the RomanVoice tray app. It records
mono PCM16 audio at 16 kHz, streams it to RomanVoice over an authenticated
WebSocket, writes live partials into the active Android text field with
`InputConnection.setComposingText()`, then commits the final transcript with
`InputConnection.commitText()`.

## Service URL

RomanVoice still binds to `127.0.0.1:8799` by default. A phone cannot reach that
loopback address. For phone use, run RomanVoice with a host that is reachable
from the phone, ideally over Tailscale:

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

The IME checks `/v1/health` whenever the keyboard opens. If RomanVoice is not
reachable, it shows `RomanVoice offline` before recording starts and offers a
keyboard switch button.

For command-line install after USB debugging is enabled:

```powershell
.\build-debug-apk.ps1
.\install-to-connected-phone.ps1
```

The install script reads `%APPDATA%\RomanVoice\service_token.txt`, installs the
debug APK, grants microphone permission, and preloads the IME settings with this
PC's LAN URL. It also asks Android to switch the current keyboard to RomanVoice.

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

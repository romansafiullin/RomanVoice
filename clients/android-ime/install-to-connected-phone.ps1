param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$StreamUrl = "",
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt'),
    [string]$Polish = "settings"
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Adb = Join-Path $AndroidSdkRoot "platform-tools\adb.exe"
$Apk = Join-Path $ProjectRoot "dist\romanvoice-ime-debug.apk"

if (-not (Test-Path $Adb)) {
    throw "adb.exe not found at $Adb"
}
if (-not (Test-Path $Apk)) {
    throw "APK not found at $Apk. Run .\build-debug-apk.ps1 first."
}
if (-not (Test-Path $TokenFile)) {
    throw "RomanVoice token file not found at $TokenFile"
}
if (-not $StreamUrl) {
    $Address = Get-NetIPAddress -AddressFamily IPv4 |
        Where-Object {
            $_.IPAddress -notlike "127.*" -and
            $_.IPAddress -notlike "169.254*" -and
            $_.PrefixOrigin -ne "WellKnown"
        } |
        Select-Object -First 1 -ExpandProperty IPAddress
    if (-not $Address) {
        throw "Could not determine this PC's LAN IP. Pass -StreamUrl explicitly."
    }
    $StreamUrl = "ws://$Address`:8799/v1/transcribe/stream"
}

$Devices = & $Adb devices | Select-String "`tdevice$"
if (-not $Devices) {
    throw "No authorized Android device is connected. Plug in the Pixel, enable USB debugging, and approve the phone prompt."
}

$Token = (Get-Content -Raw -Path $TokenFile).Trim()
if (-not $Token) {
    throw "RomanVoice token file is empty: $TokenFile"
}

function Escape-Xml([string]$Value) {
    return [System.Security.SecurityElement]::Escape($Value)
}

$Prefs = @"
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="stream_url">$(Escape-Xml $StreamUrl)</string>
    <string name="token">$(Escape-Xml $Token)</string>
    <string name="polish">$(Escape-Xml $Polish)</string>
</map>
"@

$TempPrefs = Join-Path $env:TEMP "romanvoice_ime.xml"
Set-Content -Path $TempPrefs -Value $Prefs -Encoding UTF8

& $Adb install -r $Apk
if ($LASTEXITCODE -ne 0) {
    throw "adb install failed"
}

& $Adb shell pm grant app.romanvoice.ime android.permission.RECORD_AUDIO | Out-Null
& $Adb push $TempPrefs /data/local/tmp/romanvoice_ime.xml | Out-Null
& $Adb shell run-as app.romanvoice.ime mkdir -p shared_prefs | Out-Null
& $Adb shell run-as app.romanvoice.ime cp /data/local/tmp/romanvoice_ime.xml shared_prefs/romanvoice_ime.xml | Out-Null
& $Adb shell run-as app.romanvoice.ime chmod 600 shared_prefs/romanvoice_ime.xml | Out-Null
& $Adb shell am force-stop app.romanvoice.ime | Out-Null
& $Adb shell ime enable app.romanvoice.ime/.RomanVoiceImeService | Out-Null
& $Adb shell ime set app.romanvoice.ime/.RomanVoiceImeService | Out-Null
& $Adb shell am start -n app.romanvoice.ime/.SettingsActivity | Out-Null

Write-Output "Installed RomanVoice IME."
Write-Output "Stream URL: $StreamUrl"
Write-Output "RomanVoice was requested as the current keyboard. If Android blocks that, open keyboard settings and select RomanVoice."

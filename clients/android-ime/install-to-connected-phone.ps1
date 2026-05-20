param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$StreamUrl = "",
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt'),
    [string]$Polish = "settings",
    [string]$PreferredKeyboard = "",
    [bool]$EnableFloatingMic = $true,
    [switch]$SetRomanVoiceKeyboard
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

$PreviousKeyboard = (& $Adb shell settings get secure default_input_method).Trim()

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

function Install-DebugApk {
    $installOutput = & $Adb install -r $Apk 2>&1
    $installOutput | Write-Output
    if ($LASTEXITCODE -eq 0) {
        return
    }

    if (($installOutput -join "`n") -notmatch "INSTALL_FAILED_UPDATE_INCOMPATIBLE") {
        throw "adb install failed"
    }

    Write-Output "Existing RomanVoice IME uses a different debug signature; reinstalling cleanly."
    & $Adb uninstall app.romanvoice.ime | Write-Output
    if ($LASTEXITCODE -ne 0) {
        throw "adb uninstall failed after signature mismatch"
    }

    $retryOutput = & $Adb install -r $Apk 2>&1
    $retryOutput | Write-Output
    if ($LASTEXITCODE -ne 0) {
        throw "adb install failed after signature-mismatch reinstall"
    }
}

function Enable-FloatingMicService {
    $component = "app.romanvoice.ime/.RomanVoiceFloatingService"
    $expandedComponent = "app.romanvoice.ime/app.romanvoice.ime.RomanVoiceFloatingService"
    $current = (& $Adb shell settings get secure enabled_accessibility_services).Trim()
    if ($current -eq "null") {
        $current = ""
    }

    $services = @()
    if ($current) {
        $services = @($current -split ":" | Where-Object { $_ })
    }
    $services = @($services | Where-Object { $_ -ne $component -and $_ -ne $expandedComponent })
    $services += $expandedComponent

    $next = ($services -join ":")
    & $Adb shell settings put secure enabled_accessibility_services "$next" | Out-Null
    & $Adb shell settings put secure accessibility_enabled 1 | Out-Null

    Start-Sleep -Milliseconds 250
    $readback = (& $Adb shell settings get secure enabled_accessibility_services).Trim()
    if ($readback -notlike "*$expandedComponent*") {
        throw "RomanVoice Floating Mic accessibility service was not enabled by Android. Open Accessibility settings and enable RomanVoice Floating Mic manually."
    }
}

function Resolve-NormalKeyboard {
    if ($PreferredKeyboard) {
        return $PreferredKeyboard
    }

    if ($PreviousKeyboard -and $PreviousKeyboard -ne "null" -and $PreviousKeyboard -notlike "app.romanvoice.ime/*") {
        return $PreviousKeyboard
    }

    $enabledInputMethods = @(& $Adb shell ime list -s)
    foreach ($candidate in @(
        "com.touchtype.swiftkey/com.touchtype.KeyboardService",
        "com.google.android.inputmethod.latin/com.android.inputmethod.latin.LatinIME"
    )) {
        if ($enabledInputMethods -contains $candidate) {
            return $candidate
        }
    }

    return ""
}

$TempPrefs = Join-Path $env:TEMP "romanvoice_ime.xml"
Set-Content -Path $TempPrefs -Value $Prefs -Encoding UTF8

# Stop the package before updating it; an active accessibility service can keep
# the APK install transaction open until adb is killed.
& $Adb shell am force-stop app.romanvoice.ime | Out-Null
Install-DebugApk

& $Adb shell pm grant app.romanvoice.ime android.permission.RECORD_AUDIO | Out-Null
& $Adb push $TempPrefs /data/local/tmp/romanvoice_ime.xml | Out-Null
& $Adb shell run-as app.romanvoice.ime mkdir -p shared_prefs | Out-Null
& $Adb shell run-as app.romanvoice.ime cp /data/local/tmp/romanvoice_ime.xml shared_prefs/romanvoice_ime.xml | Out-Null
& $Adb shell run-as app.romanvoice.ime chmod 600 shared_prefs/romanvoice_ime.xml | Out-Null
& $Adb shell am force-stop app.romanvoice.ime | Out-Null
& $Adb shell ime enable app.romanvoice.ime/.RomanVoiceImeService | Out-Null
if ($SetRomanVoiceKeyboard) {
    & $Adb shell ime set app.romanvoice.ime/.RomanVoiceImeService | Out-Null
} else {
    $normalKeyboard = Resolve-NormalKeyboard
    if ($normalKeyboard) {
        & $Adb shell ime set $normalKeyboard | Out-Null
    }
}
if ($EnableFloatingMic) {
    Enable-FloatingMicService
}
& $Adb shell am start -n app.romanvoice.ime/.SettingsActivity | Out-Null

Write-Output "Installed RomanVoice IME."
Write-Output "Stream URL: $StreamUrl"
if ($SetRomanVoiceKeyboard) {
    Write-Output "RomanVoice was requested as the current keyboard. If Android blocks that, open keyboard settings and select RomanVoice."
} else {
    Write-Output "Normal keyboard preserved/restored for floating mic use. Pass -SetRomanVoiceKeyboard to use the full RomanVoice keyboard."
}
if ($EnableFloatingMic) {
    Write-Output "RomanVoice Floating Mic accessibility service was enabled and verified via ADB."
}

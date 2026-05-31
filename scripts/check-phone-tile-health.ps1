param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$ServiceUrl = "http://127.0.0.1:8799",
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt'),
    [switch]$RequireAdbDevice
)

$ErrorActionPreference = 'Stop'

$failures = New-Object System.Collections.Generic.List[string]

function Add-Failure {
    param([string]$Message)
    $failures.Add($Message) | Out-Null
    Write-Output "FAIL $Message"
}

function Write-Pass {
    param([string]$Message)
    Write-Output "OK $Message"
}

if (-not (Test-Path $TokenFile)) {
    throw "RomanVoice token file not found at $TokenFile"
}
$token = (Get-Content -Raw -Path $TokenFile).Trim()
if (-not $token) {
    throw "RomanVoice token file is empty at $TokenFile"
}
$headers = @{ Authorization = "Bearer $token" }

try {
    $health = Invoke-RestMethod -Uri "$ServiceUrl/health" -TimeoutSec 3
    if ($health.ok -and $health.service -eq 'RomanVoice') {
        Write-Pass "RomanVoice desktop service responds at $ServiceUrl."
    } else {
        Add-Failure "RomanVoice desktop service returned an unexpected /health payload."
    }
} catch {
    Add-Failure "RomanVoice desktop service is not reachable at $ServiceUrl."
}

$listeners = @(Get-NetTCPConnection -LocalPort 8799 -State Listen -ErrorAction SilentlyContinue)
$phoneReachableListener = @(
    $listeners | Where-Object {
        $_.LocalAddress -eq '0.0.0.0' -or
        ($_.LocalAddress -notlike '127.*' -and $_.LocalAddress -ne '::1')
    }
)
if ($phoneReachableListener.Count -gt 0) {
    Write-Pass "RomanVoice is listening on a phone-reachable interface."
} else {
    Add-Failure "RomanVoice is not listening on 0.0.0.0 or a non-loopback interface; the phone cannot reach it."
}

try {
    $phoneStatus = Invoke-RestMethod -Uri "$ServiceUrl/v1/phone/status" -Headers $headers -TimeoutSec 3
    if ($phoneStatus.phone.ok) {
        Write-Pass "Phone floating service heartbeat is fresh."
    } else {
        $status = if ($phoneStatus.phone.status) { $phoneStatus.phone.status } else { 'unknown' }
        $age = if ($null -ne $phoneStatus.phone.last_seen_age_seconds) { " age=$($phoneStatus.phone.last_seen_age_seconds)s" } else { "" }
        Add-Failure "Phone floating service heartbeat is not healthy: status=$status$age."
    }
} catch {
    Add-Failure "RomanVoice phone heartbeat endpoint is not reachable or this desktop service is too old."
}

$adb = Join-Path $AndroidSdkRoot 'platform-tools\adb.exe'
if (-not (Test-Path $adb)) {
    if ($RequireAdbDevice) {
        Add-Failure "adb.exe not found at $adb"
    } else {
        Write-Output "WARN adb.exe not found at $adb; skipped direct phone checks."
    }
} else {
    $devices = @(& $adb devices | Select-String "`tdevice$")
    if ($devices.Count -eq 0) {
        if ($RequireAdbDevice) {
            Add-Failure "No authorized Android device is connected over ADB."
        } else {
            Write-Output "WARN No authorized Android device is connected over ADB; skipped direct phone checks."
        }
    } else {
        Write-Pass "Authorized Android device is connected over ADB."

        $packagePath = (& $adb shell pm path app.romanvoice.ime).Trim()
        if ($packagePath -like 'package:*') {
            Write-Pass "RomanVoice Android package is installed."
        } else {
            Add-Failure "RomanVoice Android package is not installed."
        }

        $accessibilityEnabled = (& $adb shell settings get secure accessibility_enabled).Trim()
        $enabledServices = (& $adb shell settings get secure enabled_accessibility_services).Trim()
        $floatingComponent = 'app.romanvoice.ime/app.romanvoice.ime.RomanVoiceFloatingService'
        if ($accessibilityEnabled -eq '1' -and $enabledServices -like "*$floatingComponent*") {
            Write-Pass "RomanVoice Floating Mic accessibility service is enabled."
        } else {
            Add-Failure "RomanVoice Floating Mic accessibility service is not enabled; the Quick Settings tile will be unavailable/dark."
        }

        $packageDump = (& $adb shell dumpsys package app.romanvoice.ime)
        if (($packageDump -join "`n") -match 'android\.permission\.RECORD_AUDIO:\s+granted=true') {
            Write-Pass "RomanVoice Android microphone permission is granted."
        } else {
            Add-Failure "RomanVoice Android microphone permission is not granted."
        }

        $prefs = (& $adb shell run-as app.romanvoice.ime cat shared_prefs/romanvoice_ime.xml 2>$null)
        $prefsText = $prefs -join "`n"
        if ($prefsText -match '<string name="stream_url">([^<]+)</string>') {
            $streamUrl = [System.Net.WebUtility]::HtmlDecode($Matches[1])
            if ($streamUrl -match '^ws://(127\.|100\.x\.x\.x)' -or $streamUrl -notmatch '^wss?://') {
                Add-Failure "RomanVoice Android stream URL is not phone-reachable: $streamUrl"
            } else {
                Write-Pass "RomanVoice Android stream URL is configured: $streamUrl"
            }
        } else {
            Add-Failure "Could not read RomanVoice Android stream URL from app preferences."
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Output "RomanVoice phone tile health failed with $($failures.Count) issue(s)."
    exit 2
}

Write-Output "RomanVoice phone tile health is OK."
exit 0

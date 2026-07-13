param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$ServiceUrl = "http://127.0.0.1:8799",
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt'),
    [switch]$RequireAdbDevice,
    [switch]$AllowLanOnly,
    [ValidateRange(1, 30)][int]$PhoneProbeTimeoutSeconds = 5
)

$ErrorActionPreference = 'Stop'

$PackageName = 'app.romanvoice.ime'
$TailscalePackageName = 'com.tailscale.ipn'
$PreferencesPath = 'shared_prefs/romanvoice_ime.xml'
$RepositoryRoot = Split-Path -Parent $PSScriptRoot
$VersionPropertiesPath = Join-Path $RepositoryRoot 'clients\android-ime\version.properties'
if (-not (Test-Path -LiteralPath $VersionPropertiesPath)) {
    throw "Android version metadata is missing: $VersionPropertiesPath"
}
$VersionProperties = ConvertFrom-StringData (Get-Content -Raw -LiteralPath $VersionPropertiesPath)
$ExpectedVersionCode = [string][int]$VersionProperties.versionCode
$ExpectedVersionName = "$($VersionProperties.versionName)-debug"
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

function Write-Warning {
    param([string]$Message)
    Write-Output "WARN $Message"
}

function Test-TailscaleHost {
    param([string]$HostName)

    $normalized = $HostName.Trim().TrimEnd('.').ToLowerInvariant()
    if ($normalized -match '^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)*\.ts\.net$') {
        return $true
    }

    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($normalized, [ref]$address)) {
        return $false
    }
    if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }

    $bytes = $address.GetAddressBytes()
    return $bytes[0] -eq 100 -and $bytes[1] -ge 64 -and $bytes[1] -le 127
}

function Test-PrivateLanHost {
    param([string]$HostName)

    $address = $null
    if (-not [System.Net.IPAddress]::TryParse($HostName, [ref]$address)) {
        return $false
    }
    if ($address.AddressFamily -ne [System.Net.Sockets.AddressFamily]::InterNetwork) {
        return $false
    }

    $bytes = $address.GetAddressBytes()
    return (
        $bytes[0] -eq 10 -or
        ($bytes[0] -eq 172 -and $bytes[1] -ge 16 -and $bytes[1] -le 31) -or
        ($bytes[0] -eq 192 -and $bytes[1] -eq 168)
    )
}

function ConvertTo-RomanVoiceStreamUri {
    param([string]$Value)

    $uri = $null
    if (-not [System.Uri]::TryCreate($Value, [System.UriKind]::Absolute, [ref]$uri)) {
        return $null
    }
    if ($uri.Scheme -notin @('ws', 'wss')) {
        return $null
    }
    if ($uri.AbsolutePath.TrimEnd('/') -ne '/v1/transcribe/stream') {
        return $null
    }
    if ($uri.UserInfo -or $uri.Query -or $uri.Fragment) {
        return $null
    }
    return $uri
}

function Get-Sha256Fingerprint {
    param([string]$Value)

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [System.Text.Encoding]::UTF8.GetBytes($Value)
        $digest = $sha256.ComputeHash($bytes)
        return ([System.BitConverter]::ToString($digest) -replace '-', '').ToLowerInvariant().Substring(0, 12)
    } finally {
        $sha256.Dispose()
    }
}

function Get-PreferenceValue {
    param(
        [string]$XmlText,
        [string]$Name
    )

    if (-not $XmlText) {
        return ""
    }
    try {
        [xml]$document = $XmlText
        $node = @($document.map.string) |
            Where-Object { $_.name -eq $Name } |
            Select-Object -First 1
        if ($node) {
            return [string]$node.InnerText
        }
    } catch {
        return ""
    }
    return ""
}

function Get-PreferenceBooleanValue {
    param(
        [string]$XmlText,
        [string]$Name
    )

    if (-not $XmlText) {
        return ""
    }
    try {
        [xml]$document = $XmlText
        $node = @($document.map.boolean) |
            Where-Object { $_.name -eq $Name } |
            Select-Object -First 1
        if ($node) {
            return ([string]$node.value).ToLowerInvariant()
        }
    } catch {
        return ""
    }
    return ""
}

function Invoke-PhoneWebSocketProbe {
    param(
        [string]$AdbPath,
        [System.Uri]$StreamUri,
        [string]$PhoneToken
    )

    if ($StreamUri.Scheme -ne 'ws') {
        return [pscustomobject]@{
            Ok = $false
            Detail = 'The ADB health probe supports the expected ws:// private transport, not wss://.'
        }
    }

    $hostName = $StreamUri.DnsSafeHost
    if ($hostName -notmatch '^[A-Za-z0-9.-]+$') {
        return [pscustomobject]@{
            Ok = $false
            Detail = 'The configured stream host contains unsupported characters.'
        }
    }
    $port = $StreamUri.Port

    $keyBytes = New-Object byte[] 16
    $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try {
        $random.GetBytes($keyBytes)
    } finally {
        $random.Dispose()
    }
    $webSocketKey = [Convert]::ToBase64String($keyBytes)
    $hostHeader = "$hostName`:$port"
    $request = @(
        "GET $($StreamUri.PathAndQuery) HTTP/1.1",
        "Host: $hostHeader",
        "Upgrade: websocket",
        "Connection: Upgrade",
        "Sec-WebSocket-Key: $webSocketKey",
        "Sec-WebSocket-Version: 13",
        "Authorization: Bearer $PhoneToken",
        "",
        ""
    ) -join "`r`n"

    # Windows PowerShell 5 prefixes native-process stdin with a UTF-8 BOM. The
    # remote filter strips only that preamble. The token stays on stdin and is
    # never placed in an adb argument, console line, or temporary file.
    $stdinFilter = if ($PSVersionTable.PSVersion.Major -le 5) {
        "dd bs=1 skip=3 2>/dev/null"
    } else {
        "cat"
    }
    $remoteCommand = "$stdinFilter | toybox nc -w $PhoneProbeTimeoutSeconds -q 1 $hostName $port"
    $response = @($request | & $AdbPath shell $remoteCommand 2>&1)
    $exitCode = $LASTEXITCODE
    $responseText = $response -join "`n"

    if ($responseText -match 'HTTP/1\.[01]\s+101\s+Switching Protocols') {
        return [pscustomobject]@{
            Ok = $true
            Detail = 'Authenticated WebSocket upgrade succeeded from the phone.'
        }
    }
    if ($responseText -match 'HTTP/1\.[01]\s+(401|403)') {
        return [pscustomobject]@{
            Ok = $false
            Detail = 'The phone reached RomanVoice, but the authenticated WebSocket upgrade was rejected.'
        }
    }
    if ($exitCode -ne 0 -or $responseText -match '(?i)timed?\s*out|no route|refused|unreachable') {
        return [pscustomobject]@{
            Ok = $false
            Detail = "The phone could not reach $hostHeader within $PhoneProbeTimeoutSeconds seconds."
        }
    }
    return [pscustomobject]@{
        Ok = $false
        Detail = 'The phone reached an endpoint, but it did not accept the RomanVoice WebSocket upgrade.'
    }
}

if (-not (Test-Path $TokenFile)) {
    throw "RomanVoice token file not found at $TokenFile"
}
$token = (Get-Content -Raw -Path $TokenFile).Trim()
if (-not $token) {
    throw "RomanVoice token file is empty at $TokenFile"
}
$desktopTokenFingerprint = Get-Sha256Fingerprint $token
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
    Add-Failure "adb.exe not found at $adb; direct phone-to-service health could not be proven."
} else {
    $devices = @(& $adb devices | Select-String "`tdevice$")
    if ($devices.Count -eq 0) {
        Add-Failure "No authorized Android device is connected over ADB; direct phone-to-service health could not be proven."
    } else {
        Write-Pass "Authorized Android device is connected over ADB."

        $packagePath = (& $adb shell pm path $PackageName).Trim()
        $packageDump = @()
        if ($packagePath -like 'package:*') {
            Write-Pass "RomanVoice Android package is installed."
            $packageDump = @(& $adb shell dumpsys package $PackageName)
            $packageDumpText = $packageDump -join "`n"
            $versionCodeMatch = [regex]::Match($packageDumpText, '(?m)^\s*versionCode=(\d+)\b')
            $versionNameMatch = [regex]::Match($packageDumpText, '(?m)^\s*versionName=([^\s]+)\s*$')
            $installedVersionCode = if ($versionCodeMatch.Success) { $versionCodeMatch.Groups[1].Value } else { '' }
            $installedVersionName = if ($versionNameMatch.Success) { $versionNameMatch.Groups[1].Value } else { '' }
            if (
                $installedVersionCode -eq $ExpectedVersionCode -and
                $installedVersionName -eq $ExpectedVersionName
            ) {
                Write-Pass "RomanVoice Android build identity matches $ExpectedVersionName ($ExpectedVersionCode)."
            } else {
                Add-Failure "RomanVoice Android build is stale or unexpected: installed=$installedVersionName ($installedVersionCode), expected=$ExpectedVersionName ($ExpectedVersionCode)."
            }
        } else {
            Add-Failure "RomanVoice Android package is not installed."
        }

        $accessibilityEnabled = (& $adb shell settings get secure accessibility_enabled).Trim()
        $enabledServices = (& $adb shell settings get secure enabled_accessibility_services).Trim()
        $floatingComponent = "$PackageName/$PackageName.RomanVoiceFloatingService"
        if ($accessibilityEnabled -eq '1' -and $enabledServices -like "*$floatingComponent*") {
            Write-Pass "RomanVoice Floating Mic accessibility service is enabled."
        } else {
            Add-Failure "RomanVoice Floating Mic accessibility service is not enabled; the Quick Settings tile will be unavailable/dark."
        }

        if (($packageDump -join "`n") -match 'android\.permission\.RECORD_AUDIO:\s+granted=true') {
            Write-Pass "RomanVoice Android microphone permission is granted."
        } else {
            Add-Failure "RomanVoice Android microphone permission is not granted."
        }

        $modeOutput = @(& $adb shell run-as $PackageName stat -c '%a' shared_prefs $PreferencesPath 2>$null)
        $modes = @($modeOutput | ForEach-Object { $_.Trim() } | Where-Object { $_ })
        if ($LASTEXITCODE -eq 0 -and $modes.Count -ge 2 -and $modes[0] -eq '700' -and $modes[1] -eq '600') {
            Write-Pass "RomanVoice app preferences use private directory/file permissions (700/600)."
        } else {
            Add-Failure "RomanVoice app preference permissions are missing or unsafe; reinstall with the current provisioning script."
        }

        $prefs = @(& $adb shell run-as $PackageName cat $PreferencesPath 2>$null)
        $prefsText = ($prefs -join "`n").Trim()
        $streamUrl = Get-PreferenceValue $prefsText 'stream_url'
        $phoneToken = Get-PreferenceValue $prefsText 'token'
        $allowLanStream = Get-PreferenceBooleanValue $prefsText 'allow_lan_stream'
        $streamUri = ConvertTo-RomanVoiceStreamUri $streamUrl

        if ($null -eq $streamUri) {
            Add-Failure "Could not read a valid RomanVoice Android stream URL from app preferences."
        } elseif (Test-TailscaleHost $streamUri.DnsSafeHost) {
            Write-Pass "RomanVoice Android uses a Tailscale stream endpoint: $streamUrl"
            if ($allowLanStream -ne 'false') {
                Add-Failure "RomanVoice Android LAN developer override is not explicitly disabled; reinstall with the current Tailscale provisioning path."
            }
        } elseif ((Test-PrivateLanHost $streamUri.DnsSafeHost) -and $AllowLanOnly) {
            if ($allowLanStream -eq 'true') {
                Write-Warning "RomanVoice Android is intentionally in LAN-only mode: $streamUrl"
            } else {
                Add-Failure "RomanVoice Android has a LAN URL without the installer-only LAN override; the app will reject this endpoint."
            }
        } elseif (Test-PrivateLanHost $streamUri.DnsSafeHost) {
            Add-Failure "RomanVoice Android is configured for LAN only ($streamUrl). Reinstall with Tailscale, or pass -AllowLanOnly for an intentional home-only check."
        } else {
            Add-Failure "RomanVoice Android stream URL is not an approved Tailscale/private-LAN endpoint: $streamUrl"
        }

        if (-not $phoneToken) {
            Add-Failure "Could not read the RomanVoice Android bearer-token fingerprint."
        } else {
            $phoneTokenFingerprint = Get-Sha256Fingerprint $phoneToken
            if ($phoneTokenFingerprint -eq $desktopTokenFingerprint) {
                Write-Pass "Phone and desktop token fingerprints match (sha256:$desktopTokenFingerprint)."
            } else {
                Add-Failure "Phone/desktop token fingerprints differ (phone sha256:$phoneTokenFingerprint, desktop sha256:$desktopTokenFingerprint)."
            }
        }

        $tailscalePackagePath = @(& $adb shell pm path $TailscalePackageName 2>$null)
        $tailscaleInstalled = $LASTEXITCODE -eq 0 -and ($tailscalePackagePath -join "`n") -like 'package:*'
        $tailscalePid = (@(& $adb shell pidof $TailscalePackageName 2>$null) -join "`n").Trim()
        $connectivityLines = @(& $adb shell dumpsys connectivity 2>$null)
        $activeVpnNetworks = @(
            $connectivityLines | Where-Object {
                $_ -match '^\s*NetworkAgentInfo\{' -and $_ -match 'Transports:\s+.*\bVPN\b'
            }
        )
        $activeVpn = $activeVpnNetworks.Count -gt 0
        $alwaysOnVpn = (& $adb shell settings get secure always_on_vpn_app 2>$null).Trim()

        $tailscaleRequired = (
            $null -ne $streamUri -and
            (
                (Test-TailscaleHost $streamUri.DnsSafeHost) -or
                ((Test-PrivateLanHost $streamUri.DnsSafeHost) -and -not $AllowLanOnly)
            )
        )
        if ($tailscaleRequired) {
            if ($tailscaleInstalled) {
                Write-Pass "Tailscale is installed on the phone."
            } else {
                Add-Failure "Tailscale is not installed on the phone."
            }
            if ($tailscalePid -and $activeVpn) {
                Write-Pass "Tailscale has a running phone process and Android reports an active VPN transport."
            } elseif (-not $tailscalePid -and -not $activeVpn) {
                Add-Failure "Tailscale is not running and Android has no active VPN transport. Connect Tailscale before relying on RomanVoice away from Wi-Fi."
            } elseif (-not $tailscalePid) {
                Add-Failure "Android reports a VPN transport, but the Tailscale process is not running."
            } else {
                Add-Failure "The Tailscale process is running, but Android does not report an active VPN transport."
            }
            if ($alwaysOnVpn -eq $TailscalePackageName) {
                Write-Pass "Tailscale is configured as Android's always-on VPN."
            } else {
                Write-Warning "Tailscale is not configured as Android's always-on VPN."
            }
        } elseif ($tailscaleInstalled -and $AllowLanOnly) {
            Write-Warning "Tailscale is installed, but this explicit LAN-only check does not require it."
        }

        if ($null -ne $streamUri -and $phoneToken) {
            $probe = Invoke-PhoneWebSocketProbe -AdbPath $adb -StreamUri $streamUri -PhoneToken $phoneToken
            if ($probe.Ok) {
                Write-Pass $probe.Detail
            } else {
                Add-Failure $probe.Detail
            }
        } else {
            Add-Failure "Skipped the authenticated phone-to-service probe because phone configuration is incomplete."
        }
    }
}

if ($failures.Count -gt 0) {
    Write-Output "RomanVoice phone tile health failed with $($failures.Count) issue(s)."
    exit 2
}

Write-Output "RomanVoice phone tile health is OK."
exit 0

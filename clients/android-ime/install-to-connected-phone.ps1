param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$StreamUrl = "",
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt'),
    [string]$Polish = "settings",
    [string]$PreferredKeyboard = "",
    [bool]$EnableFloatingMic = $true,
    [switch]$PreferLan,
    [switch]$SetRomanVoiceKeyboard
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Adb = Join-Path $AndroidSdkRoot "platform-tools\adb.exe"
$Apk = Join-Path $ProjectRoot "dist\romanvoice-ime-debug.apk"
$PackageName = "app.romanvoice.ime"
$PreferencesPath = "shared_prefs/romanvoice_ime.xml"
$LegacyWindowsPreferencesPath = Join-Path $env:TEMP "romanvoice_ime.xml"
$LegacyAndroidPreferencesPath = "/data/local/tmp/romanvoice_ime.xml"

# Remove the known legacy Windows credential artifact even if later preflight
# checks fail before an Android device is available.
if (Test-Path -LiteralPath $LegacyWindowsPreferencesPath) {
    Remove-Item -LiteralPath $LegacyWindowsPreferencesPath -Force
}

if (-not (Test-Path $Adb)) {
    throw "adb.exe not found at $Adb"
}
if (-not (Test-Path $Apk)) {
    throw "APK not found at $Apk. Run .\build-debug-apk.ps1 first."
}
if (-not (Test-Path $TokenFile)) {
    throw "RomanVoice token file not found at $TokenFile"
}

function Resolve-TailscaleExe {
    $command = Get-Command "tailscale" -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }

    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles "Tailscale\tailscale.exe"),
        (Join-Path ${env:ProgramFiles(x86)} "Tailscale\tailscale.exe")
    )) {
        if ($candidate -and (Test-Path -LiteralPath $candidate)) {
            return $candidate
        }
    }

    return ""
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

function Test-TailscaleStreamUrl {
    param([string]$Value)

    $uri = ConvertTo-RomanVoiceStreamUri $Value
    return $null -ne $uri -and (Test-TailscaleHost $uri.DnsSafeHost)
}

function Assert-ApprovedStreamUrl {
    param([string]$Value)

    $uri = ConvertTo-RomanVoiceStreamUri $Value
    if ($null -eq $uri) {
        throw "Invalid RomanVoice stream URL. Use ws://HOST:PORT/v1/transcribe/stream."
    }
    if (Test-TailscaleHost $uri.DnsSafeHost) {
        return
    }
    if ($PreferLan -and (Test-PrivateLanHost $uri.DnsSafeHost)) {
        return
    }

    throw "Refusing non-Tailscale stream URL '$Value'. Keep the PC and phone connected to Tailscale, or pass -PreferLan for an intentional home-LAN-only install."
}

function Resolve-TailscaleIp {
    $tailscale = Resolve-TailscaleExe
    if ($tailscale) {
        $ipOutput = @(& $tailscale ip -4 2>$null)
        $ip = $ipOutput |
            Where-Object { Test-TailscaleHost $_ } |
            Select-Object -First 1
        if ($ip) {
            return $ip
        }
    }

    return Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object {
            $_.InterfaceAlias -match "Tailscale" -and
            (Test-TailscaleHost $_.IPAddress)
        } |
        Select-Object -First 1 -ExpandProperty IPAddress
}

function Resolve-LanIp {
    return Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { Test-PrivateLanHost $_.IPAddress } |
        Select-Object -First 1 -ExpandProperty IPAddress
}

function Get-AppPreferencesXml {
    $output = @(& $Adb shell run-as $PackageName cat $PreferencesPath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $output.Count -eq 0) {
        return ""
    }
    return ($output -join "`n").Trim()
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

function Escape-Xml {
    param([string]$Value)
    return [System.Security.SecurityElement]::Escape($Value)
}

function Remove-LegacyCredentialArtifacts {
    if (Test-Path -LiteralPath $LegacyWindowsPreferencesPath) {
        Remove-Item -LiteralPath $LegacyWindowsPreferencesPath -Force
    }

    & $Adb shell rm -f $LegacyAndroidPreferencesPath | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove legacy RomanVoice preferences from $LegacyAndroidPreferencesPath"
    }
}

function Write-AppPreferencesFromStdin {
    param([string]$Content)

    # Windows PowerShell 5 prefixes native-process stdin with a UTF-8 BOM. Strip
    # exactly that preamble on-device, then stream directly into app-private
    # storage. The bearer token never enters a Windows or Android temporary file.
    $stdinFilter = if ($PSVersionTable.PSVersion.Major -le 5) {
        "dd bs=1 skip=3 2>/dev/null"
    } else {
        "cat"
    }
    $writeCommand = "${stdinFilter} | run-as $PackageName sh -c 'umask 077; mkdir -p shared_prefs; chmod 700 shared_prefs; cat > $PreferencesPath; chmod 600 $PreferencesPath'"
    $unusedOutput = @($Content | & $Adb shell $writeCommand 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not write RomanVoice app preferences through ADB stdin."
    }

    $modeOutput = @(& $Adb shell run-as $PackageName stat -c '%a' shared_prefs $PreferencesPath 2>$null)
    if ($LASTEXITCODE -ne 0 -or $modeOutput.Count -lt 2) {
        throw "Could not verify RomanVoice app preference permissions."
    }
    $modes = @($modeOutput | ForEach-Object { $_.Trim() } | Where-Object { $_ })
    if ($modes.Count -lt 2 -or $modes[0] -ne '700' -or $modes[1] -ne '600') {
        throw "Unsafe RomanVoice app preference permissions. Expected shared_prefs=700 and romanvoice_ime.xml=600."
    }
}

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
    & $Adb uninstall $PackageName | Write-Output
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
    $component = "$PackageName/.RomanVoiceFloatingService"
    $expandedComponent = "$PackageName/$PackageName.RomanVoiceFloatingService"
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

    if ($PreviousKeyboard -and $PreviousKeyboard -ne "null" -and $PreviousKeyboard -notlike "$PackageName/*") {
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

$Devices = & $Adb devices | Select-String "`tdevice$"
if (-not $Devices) {
    throw "No authorized Android device is connected. Plug in the Pixel, enable USB debugging, and approve the phone prompt."
}

$PreviousKeyboard = (& $Adb shell settings get secure default_input_method).Trim()
$ExistingPreferencesXml = Get-AppPreferencesXml
$ExistingStreamUrl = Get-PreferenceValue $ExistingPreferencesXml "stream_url"

if (-not $StreamUrl) {
    if ($PreferLan) {
        $Address = Resolve-LanIp
        if (-not $Address) {
            throw "Could not determine this PC's private LAN IP. Pass -StreamUrl with -PreferLan explicitly."
        }
        $StreamUrl = "ws://$Address`:8799/v1/transcribe/stream"
    } elseif ($ExistingStreamUrl -and (Test-TailscaleStreamUrl $ExistingStreamUrl)) {
        $StreamUrl = $ExistingStreamUrl
        Write-Output "Preserving the existing Tailscale stream endpoint."
    } else {
        $Address = Resolve-TailscaleIp
        if (-not $Address) {
            throw "Could not determine this PC's Tailscale IP. RomanVoice will not silently fall back to LAN. Connect Tailscale, pass a Tailscale -StreamUrl, or deliberately use -PreferLan."
        }
        $StreamUrl = "ws://$Address`:8799/v1/transcribe/stream"
    }
}
Assert-ApprovedStreamUrl $StreamUrl

$Token = (Get-Content -Raw -Path $TokenFile).Trim()
if (-not $Token) {
    throw "RomanVoice token file is empty: $TokenFile"
}
$TokenFingerprint = Get-Sha256Fingerprint $Token
$AllowLanStream = if ($PreferLan) { "true" } else { "false" }

$Prefs = @"
<?xml version='1.0' encoding='utf-8' standalone='yes' ?>
<map>
    <string name="stream_url">$(Escape-Xml $StreamUrl)</string>
    <string name="token">$(Escape-Xml $Token)</string>
    <string name="polish">$(Escape-Xml $Polish)</string>
    <boolean name="allow_lan_stream" value="$AllowLanStream" />
</map>
"@

try {
    Remove-LegacyCredentialArtifacts

    # Stop the package before updating it; an active accessibility service can
    # keep the APK install transaction open until adb is killed.
    & $Adb shell am force-stop $PackageName | Out-Null
    Install-DebugApk

    & $Adb shell pm grant $PackageName android.permission.RECORD_AUDIO | Out-Null
    Write-AppPreferencesFromStdin $Prefs

    $ReadbackXml = Get-AppPreferencesXml
    $ReadbackStreamUrl = Get-PreferenceValue $ReadbackXml "stream_url"
    $ReadbackPolish = Get-PreferenceValue $ReadbackXml "polish"
    $ReadbackToken = Get-PreferenceValue $ReadbackXml "token"
    $ReadbackAllowLanStream = Get-PreferenceBooleanValue $ReadbackXml "allow_lan_stream"
    if (
        $ReadbackStreamUrl -ne $StreamUrl -or
        $ReadbackPolish -ne $Polish -or
        $ReadbackAllowLanStream -ne $AllowLanStream -or
        -not $ReadbackToken
    ) {
        throw "RomanVoice app preference readback did not match the requested non-secret configuration."
    }
    $ReadbackTokenFingerprint = Get-Sha256Fingerprint $ReadbackToken
    if ($ReadbackTokenFingerprint -ne $TokenFingerprint) {
        throw "RomanVoice app token fingerprint does not match the desktop token fingerprint."
    }

    & $Adb shell am force-stop $PackageName | Out-Null
    & $Adb shell ime enable $PackageName/.RomanVoiceImeService | Out-Null
    if ($SetRomanVoiceKeyboard) {
        & $Adb shell ime set $PackageName/.RomanVoiceImeService | Out-Null
    } else {
        $normalKeyboard = Resolve-NormalKeyboard
        if ($normalKeyboard) {
            & $Adb shell ime set $normalKeyboard | Out-Null
        }
    }
    if ($EnableFloatingMic) {
        Enable-FloatingMicService
    }
    & $Adb shell am start -n $PackageName/.SettingsActivity | Out-Null
    if ($EnableFloatingMic) {
        # Android can drop the enabled accessibility service during an APK update
        # after our first write appears to succeed. Re-apply after launch so the
        # phone is left in the usable floating-mic state.
        Start-Sleep -Milliseconds 750
        Enable-FloatingMicService
    }
} finally {
    Remove-LegacyCredentialArtifacts
}

Write-Output "Installed RomanVoice IME."
Write-Output "Stream URL: $StreamUrl"
Write-Output "Token fingerprint verified: sha256:$TokenFingerprint"
Write-Output "No bearer-token preference file was staged in Windows TEMP or /data/local/tmp."
if ($SetRomanVoiceKeyboard) {
    Write-Output "RomanVoice was requested as the current keyboard. If Android blocks that, open keyboard settings and select RomanVoice."
} else {
    Write-Output "Normal keyboard preserved/restored for floating mic use. Pass -SetRomanVoiceKeyboard to use the full RomanVoice keyboard."
}
if ($EnableFloatingMic) {
    Write-Output "RomanVoice Floating Mic accessibility service was enabled and verified via ADB."
}

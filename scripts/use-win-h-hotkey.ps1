param(
    [switch]$Force
)

$ErrorActionPreference = "Stop"

if (-not $Force) {
    Write-Error "Win+H is intentionally disabled for RomanVoice because it conflicts with Windows dictation and shell shortcuts. Re-run with -Force only if you explicitly want to test it."
}

$configDir = Join-Path $env:APPDATA "RomanVoice"
$configPath = Join-Path $configDir "config.json"
$backupPath = Join-Path $configDir ("config.before-win-h.{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

New-Item -ItemType Directory -Force -Path $configDir | Out-Null

function ConvertTo-Hashtable {
    param($Value)

    if ($null -eq $Value) {
        return $null
    }

    if ($Value -is [System.Collections.IDictionary]) {
        $hash = @{}
        foreach ($key in $Value.Keys) {
            $hash[$key] = ConvertTo-Hashtable $Value[$key]
        }
        return $hash
    }

    if ($Value -is [System.Management.Automation.PSCustomObject]) {
        $hash = @{}
        foreach ($property in $Value.PSObject.Properties) {
            $hash[$property.Name] = ConvertTo-Hashtable $property.Value
        }
        return $hash
    }

    if ($Value -is [object[]]) {
        return @($Value | ForEach-Object { ConvertTo-Hashtable $_ })
    }

    return $Value
}

if (Test-Path $configPath) {
    Copy-Item -LiteralPath $configPath -Destination $backupPath
    $settings = ConvertTo-Hashtable (Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json)
} else {
    $settings = @{}
}

if (-not $settings.ContainsKey("hotkeys") -or -not ($settings["hotkeys"] -is [hashtable])) {
    $settings["hotkeys"] = @{}
}

$settings["hotkeys"]["record_toggle"] = "win+h"
if (-not $settings["hotkeys"].ContainsKey("cancel")) {
    $settings["hotkeys"]["cancel"] = "ctrl+alt+backspace"
}
if (-not $settings["hotkeys"].ContainsKey("enable_disable")) {
    $settings["hotkeys"]["enable_disable"] = "ctrl+alt+shift+space"
}

$json = $settings | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)

Write-Host "RomanVoice record hotkey set to win+h."
if (Test-Path $backupPath) {
    Write-Host "Backup written to $backupPath"
}

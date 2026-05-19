$ErrorActionPreference = "Stop"

$configDir = Join-Path $env:APPDATA "RomanVoice"
$configPath = Join-Path $configDir "config.json"
$backupPath = Join-Path $configDir ("config.before-safe-hotkeys.{0}.json" -f (Get-Date -Format "yyyyMMdd-HHmmss"))

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

$settings["hotkeys"]["record_toggle"] = "ctrl+alt+space"
$settings["hotkeys"]["cancel"] = "ctrl+alt+backspace"
$settings["hotkeys"]["enable_disable"] = "ctrl+alt+shift+space"

$json = $settings | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)

Write-Host "RomanVoice hotkeys set to ctrl+alt+space / ctrl+alt+backspace / ctrl+alt+shift+space."
if (Test-Path $backupPath) {
    Write-Host "Backup written to $backupPath"
}

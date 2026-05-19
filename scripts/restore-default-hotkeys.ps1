$ErrorActionPreference = "Stop"

$configPath = Join-Path $env:APPDATA "RomanVoice\config.json"

if (-not (Test-Path $configPath)) {
    Write-Host "No RomanVoice config exists; code defaults will be used."
    exit 0
}

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

$settings = ConvertTo-Hashtable (Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json)
if (-not $settings.ContainsKey("hotkeys") -or -not ($settings["hotkeys"] -is [hashtable])) {
    $settings["hotkeys"] = @{}
}

$settings["hotkeys"]["record_toggle"] = "ctrl+space"
$settings["hotkeys"]["cancel"] = "ctrl+alt+backspace"
$settings["hotkeys"]["enable_disable"] = "ctrl+alt+shift+space"

$json = $settings | ConvertTo-Json -Depth 8
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($configPath, $json, $utf8NoBom)

Write-Host "RomanVoice hotkeys restored to ctrl+space / ctrl+alt+backspace / ctrl+alt+shift+space."

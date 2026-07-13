$script:RomanVoiceWatchdogLogMaxBytes = 2MB
$script:RomanVoiceWatchdogLogBackupCount = 3

function Rotate-RomanVoiceWatchdogLog {
    param([Parameter(Mandatory=$true)][string]$Path)

    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -lt $script:RomanVoiceWatchdogLogMaxBytes) {
        return
    }

    for ($index = $script:RomanVoiceWatchdogLogBackupCount; $index -ge 1; $index--) {
        $source = if ($index -eq 1) { $Path } else { "$Path.$($index - 1)" }
        $destination = "$Path.$index"
        if (Test-Path -LiteralPath $source) {
            if ($index -eq $script:RomanVoiceWatchdogLogBackupCount) {
                Remove-Item -LiteralPath $destination -Force -ErrorAction SilentlyContinue
            }
            Move-Item -LiteralPath $source -Destination $destination -Force
        }
    }
}

function Write-RomanVoiceWatchdogLog {
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [Parameter(Mandatory=$true)][string]$Message,
        [switch]$Quiet
    )

    $directory = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $directory -Force | Out-Null
    try {
        Rotate-RomanVoiceWatchdogLog -Path $Path
    } catch {
        # Logging must never prevent the watchdog from checking the app.
    }

    $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $Message"
    Add-Content -LiteralPath $Path -Value $line -Encoding UTF8
    if (-not $Quiet) {
        Write-Host $line
    }
}

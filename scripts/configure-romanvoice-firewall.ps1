param(
    [ValidateSet('Audit', 'Apply')]
    [string]$Mode = 'Audit',
    [string]$RuleName = 'RomanVoice-Tailscale-TCP-8799',
    [string]$RuleDisplayName = 'RomanVoice Tailscale TCP 8799',
    [string]$PythonwPath = 'C:\Users\Roman\AppData\Local\Programs\Python\Python312\pythonw.exe',
    [string]$TailscaleInterfaceAlias = 'Tailscale'
)

$ErrorActionPreference = 'Stop'
$legacyRomanVoiceRule = 'RomanVoice Dictation Service (TCP 8799)'

function Test-IsAdministrator {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Get-TailscaleLocalAddress {
    $address = Get-NetIPAddress `
        -InterfaceAlias $TailscaleInterfaceAlias `
        -AddressFamily IPv4 `
        -ErrorAction Stop |
        Where-Object { $_.IPAddress -match '^100\.' } |
        Select-Object -ExpandProperty IPAddress -First 1
    if (-not $address) {
        throw "No Tailscale IPv4 address is active on interface '$TailscaleInterfaceAlias'."
    }
    return $address
}

function Get-BroadPythonPublicAllowRules {
    Get-NetFirewallRule -PolicyStore ActiveStore |
        Where-Object {
            $_.Enabled -eq 'True' -and
            $_.Direction -eq 'Inbound' -and
            $_.Action -eq 'Allow' -and
            $_.Profile -match 'Public'
        } |
        Where-Object {
            $application = $_ | Get-NetFirewallApplicationFilter
            $port = $_ | Get-NetFirewallPortFilter
            $application.Program -ieq $PythonwPath -and
            $port.LocalPort -eq 'Any'
        }
}

function Write-RuleSummary {
    param([Parameter(Mandatory=$true)]$Rule)

    $application = $Rule | Get-NetFirewallApplicationFilter
    $port = $Rule | Get-NetFirewallPortFilter
    $address = $Rule | Get-NetFirewallAddressFilter
    Write-Output (
        "{0} enabled={1} profile={2} action={3} protocol={4} localPort={5} remote={6} program={7}" -f
        $Rule.DisplayName,
        $Rule.Enabled,
        $Rule.Profile,
        $Rule.Action,
        $port.Protocol,
        $port.LocalPort,
        ($address.RemoteAddress -join ','),
        $application.Program
    )
}

function Disable-PersistentFirewallRules {
    param([Parameter(Mandatory=$true)][object[]]$Rules)

    foreach ($rule in $Rules) {
        # Objects returned from ActiveStore cannot always be piped back into a
        # mutating cmdlet on Windows 11. Address the durable local rule by its
        # stable Name in PersistentStore instead.
        Set-NetFirewallRule `
            -PolicyStore PersistentStore `
            -Name $rule.Name `
            -Enabled False `
            -ErrorAction Stop

        $readback = Get-NetFirewallRule `
            -PolicyStore PersistentStore `
            -Name $rule.Name `
            -ErrorAction Stop
        if ($readback.Enabled -ne 'False') {
            throw "Firewall rule '$($rule.Name)' did not disable successfully."
        }
    }
}

$tailscaleAddress = Get-TailscaleLocalAddress
$broadRules = @(Get-BroadPythonPublicAllowRules)
$genericRules = @(Get-NetFirewallRule -DisplayName $legacyRomanVoiceRule -ErrorAction SilentlyContinue)
$scopedRules = @(Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue)

Write-Output "Tailscale local address: $tailscaleAddress"
Write-Output "Broad Python312 Public allow rules: $($broadRules.Count)"
$broadRules | ForEach-Object { Write-RuleSummary $_ }
Write-Output "Generic RomanVoice Private-any rules: $($genericRules.Count)"
$genericRules | ForEach-Object { Write-RuleSummary $_ }
Write-Output "Scoped RomanVoice Tailscale rules: $($scopedRules.Count)"
$scopedRules | ForEach-Object { Write-RuleSummary $_ }

if ($Mode -eq 'Audit') {
    exit 0
}
if (-not (Test-IsAdministrator)) {
    throw 'Firewall apply requires an elevated PowerShell window.'
}
if (-not (Test-Path -LiteralPath $PythonwPath)) {
    throw "RomanVoice Python executable not found: $PythonwPath"
}

$backupDirectory = Join-Path $env:LOCALAPPDATA 'RomanVoice\firewall-backups'
New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupPath = Join-Path $backupDirectory "romanvoice-before-$timestamp.wfw"
& netsh.exe advfirewall export $backupPath | Out-Null
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $backupPath)) {
    throw 'Windows Firewall policy backup failed. No rules were changed.'
}

Get-NetFirewallRule -Name $RuleName -ErrorAction SilentlyContinue |
    Remove-NetFirewallRule
New-NetFirewallRule `
    -Name $RuleName `
    -DisplayName $RuleDisplayName `
    -Description 'RomanVoice phone dictation through the private Tailscale interface only.' `
    -Enabled True `
    -Profile Private `
    -Direction Inbound `
    -Action Allow `
    -Protocol TCP `
    -LocalPort 8799 `
    -LocalAddress $tailscaleAddress `
    -RemoteAddress '100.64.0.0/10' `
    -InterfaceAlias $TailscaleInterfaceAlias `
    -Program $PythonwPath | Out-Null

Disable-PersistentFirewallRules -Rules $broadRules
Disable-PersistentFirewallRules -Rules $genericRules

$scoped = Get-NetFirewallRule -Name $RuleName -ErrorAction Stop
if ($scoped.Enabled -ne 'True') {
    throw 'Scoped RomanVoice firewall rule did not enable successfully.'
}
Write-Output "Firewall backup: $backupPath"
Write-Output 'Applied scoped Tailscale TCP 8799 rule and disabled the superseded Python312/RomanVoice allows.'
Write-Output "Rollback command: netsh advfirewall import `"$backupPath`""

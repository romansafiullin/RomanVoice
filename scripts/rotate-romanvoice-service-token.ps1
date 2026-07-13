param(
    [string]$TokenFile = $(Join-Path $env:APPDATA 'RomanVoice\service_token.txt')
)

$ErrorActionPreference = 'Stop'

function New-RomanVoiceToken {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}

function Get-TokenFingerprint {
    param([Parameter(Mandatory=$true)][string]$Token)

    $bytes = [Text.Encoding]::UTF8.GetBytes($Token)
    $hash = [Security.Cryptography.SHA256]::Create().ComputeHash($bytes)
    return ([BitConverter]::ToString($hash) -replace '-', '').Substring(0, 12)
}

function Test-FixedTimeBytesEqual {
    param(
        [Parameter(Mandatory=$true)][byte[]]$Left,
        [Parameter(Mandatory=$true)][byte[]]$Right
    )

    $difference = $Left.Length -bxor $Right.Length
    $length = [Math]::Max($Left.Length, $Right.Length)
    for ($index = 0; $index -lt $length; $index++) {
        $leftByte = if ($index -lt $Left.Length) { $Left[$index] } else { 0 }
        $rightByte = if ($index -lt $Right.Length) { $Right[$index] } else { 0 }
        $difference = $difference -bor ($leftByte -bxor $rightByte)
    }
    return $difference -eq 0
}

function Move-AtomicReplace {
    param(
        [Parameter(Mandatory=$true)][string]$Source,
        [Parameter(Mandatory=$true)][string]$Destination
    )

    if (-not ('RomanVoice.NativeFile' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

namespace RomanVoice {
    public static class NativeFile {
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
        public static extern bool MoveFileEx(
            string existingFile,
            string newFile,
            int flags
        );
    }
}
'@
    }

    $moveFileReplaceExisting = 0x1
    $moveFileWriteThrough = 0x8
    $moved = [RomanVoice.NativeFile]::MoveFileEx(
        $Source,
        $Destination,
        $moveFileReplaceExisting -bor $moveFileWriteThrough
    )
    if (-not $moved) {
        $errorCode = [Runtime.InteropServices.Marshal]::GetLastWin32Error()
        throw [ComponentModel.Win32Exception]::new(
            $errorCode,
            'Atomic RomanVoice token replacement failed'
        )
    }
}

function Set-RomanVoiceTokenAcl {
    param([Parameter(Mandatory=$true)][string]$Path)

    $currentUser = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $allowedSids = @(
        $currentUser,
        [Security.Principal.SecurityIdentifier]::new('S-1-5-18'),
        [Security.Principal.SecurityIdentifier]::new('S-1-5-32-544')
    )

    $grantArguments = @(
        "*$($currentUser.Value):(F)",
        '*S-1-5-18:(F)',
        '*S-1-5-32-544:(F)'
    )
    & icacls.exe $Path /inheritance:r /grant:r $grantArguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed while protecting the RomanVoice token (exit $LASTEXITCODE)."
    }

    $readback = Get-Acl -LiteralPath $Path
    $allowedValues = @($allowedSids | ForEach-Object { $_.Value })
    $unexpected = @(
        $readback.Access | Where-Object {
            $sid = $_.IdentityReference.Translate(
                [Security.Principal.SecurityIdentifier]
            ).Value
            $sid -notin $allowedValues -or $_.AccessControlType -ne 'Allow'
        }
    )
    if (-not $readback.AreAccessRulesProtected -or $unexpected.Count -gt 0) {
        throw 'RomanVoice token ACL verification failed.'
    }
}

$directory = Split-Path -Parent $TokenFile
New-Item -ItemType Directory -Path $directory -Force | Out-Null
$temporaryFile = "$TokenFile.$PID.$([Guid]::NewGuid().ToString('N')).tmp"
$token = New-RomanVoiceToken

try {
    [IO.File]::WriteAllText(
        $temporaryFile,
        "$token`n",
        [Text.UTF8Encoding]::new($false)
    )
    Set-RomanVoiceTokenAcl -Path $temporaryFile

    Move-AtomicReplace -Source $temporaryFile -Destination $TokenFile
    Set-RomanVoiceTokenAcl -Path $TokenFile

    $readback = (Get-Content -Raw -LiteralPath $TokenFile).Trim()
    if (-not (Test-FixedTimeBytesEqual `
        -Left ([Text.Encoding]::UTF8.GetBytes($token)) `
        -Right ([Text.Encoding]::UTF8.GetBytes($readback)))) {
        throw 'RomanVoice token readback verification failed.'
    }

    Write-Output "RomanVoice service token rotated. Fingerprint: sha256:$(Get-TokenFingerprint -Token $token)"
    Write-Output "ACL: current user, SYSTEM, and Administrators only. Restart and reprovision every consumer now."
} finally {
    Remove-Item -LiteralPath $temporaryFile -Force -ErrorAction SilentlyContinue
    $token = $null
    $readback = $null
}

param(
    [string]$AndroidSdkRoot = $(if ($env:ANDROID_HOME) { $env:ANDROID_HOME } else { Join-Path $env:LOCALAPPDATA 'Android\Sdk' }),
    [string]$DebugKeystore = $(Join-Path $env:APPDATA 'RomanVoice\android-ime-debug.keystore')
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppRoot = Join-Path $ProjectRoot "app"
$BuildRoot = Join-Path $ProjectRoot "build\manual"
$ToolsRoot = Join-Path $AndroidSdkRoot "build-tools\35.0.0"
$PlatformJar = Join-Path $AndroidSdkRoot "platforms\android-35\android.jar"

$Aapt = Join-Path $ToolsRoot "aapt.exe"
$Aapt2 = Join-Path $ToolsRoot "aapt2.exe"
$D8 = Join-Path $ToolsRoot "d8.bat"
$Zipalign = Join-Path $ToolsRoot "zipalign.exe"
$ApkSigner = Join-Path $ToolsRoot "apksigner.bat"

function Invoke-Checked {
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [string[]]$Arguments = @()
    )

    & $FilePath @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $FilePath $($Arguments -join ' ')"
    }
}

foreach ($Path in @($Aapt, $Aapt2, $D8, $Zipalign, $ApkSigner, $PlatformJar)) {
    if (-not (Test-Path $Path)) {
        throw "Required Android SDK file is missing: $Path"
    }
}

$Keystore = $DebugKeystore
$PreviousBuildKeystore = Join-Path $BuildRoot "debug.keystore"
if (-not (Test-Path $Keystore) -and (Test-Path $PreviousBuildKeystore)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Keystore) | Out-Null
    Copy-Item -LiteralPath $PreviousBuildKeystore -Destination $Keystore
}

Remove-Item -Recurse -Force -LiteralPath $BuildRoot -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force -Path `
    (Join-Path $BuildRoot "compiled-res"), `
    (Join-Path $BuildRoot "generated"), `
    (Join-Path $BuildRoot "classes"), `
    (Join-Path $BuildRoot "dex"), `
    (Join-Path $ProjectRoot "dist") | Out-Null

$CompiledRes = Join-Path $BuildRoot "compiled-res\res.zip"
$UnsignedApk = Join-Path $BuildRoot "romanvoice-ime-unsigned.apk"
$DexRoot = Join-Path $BuildRoot "dex"
$ClassesJar = Join-Path $BuildRoot "classes.jar"
$AlignedApk = Join-Path $BuildRoot "romanvoice-ime-aligned.apk"
$SignedApk = Join-Path $ProjectRoot "dist\romanvoice-ime-debug.apk"

Invoke-Checked $Aapt2 @("compile", "--dir", (Join-Path $AppRoot "src\main\res"), "-o", $CompiledRes)
Invoke-Checked $Aapt2 @(
    "link",
    "-o", $UnsignedApk,
    "-I", $PlatformJar,
    "--manifest", (Join-Path $AppRoot "src\main\AndroidManifest.xml"),
    "--java", (Join-Path $BuildRoot "generated"),
    "--min-sdk-version", "26",
    "--target-sdk-version", "35",
    "--version-code", "1",
    "--version-name", "0.1.0",
    "-R", $CompiledRes,
    "--auto-add-overlay"
)

$JavaSources = @()
$JavaSources += Get-ChildItem -Recurse -Filter "*.java" (Join-Path $AppRoot "src\main\java") | ForEach-Object { $_.FullName }
$JavaSources += Get-ChildItem -Recurse -Filter "*.java" (Join-Path $BuildRoot "generated") | ForEach-Object { $_.FullName }

Invoke-Checked "javac" (@(
    "--release", "17",
    "-encoding", "UTF-8",
    "-classpath", $PlatformJar,
    "-d", (Join-Path $BuildRoot "classes")
) + $JavaSources)

Push-Location (Join-Path $BuildRoot "classes")
try {
    Invoke-Checked "jar" @("cf", $ClassesJar, ".")
}
finally {
    Pop-Location
}

Invoke-Checked $D8 @(
    "--lib", $PlatformJar,
    "--min-api", "26",
    "--output", $DexRoot,
    $ClassesJar
)

Push-Location $DexRoot
try {
    Invoke-Checked $Aapt @("add", $UnsignedApk, "classes.dex") | Out-Null
}
finally {
    Pop-Location
}

Invoke-Checked $Zipalign @("-f", "4", $UnsignedApk, $AlignedApk)

if (-not (Test-Path $Keystore)) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Keystore) | Out-Null
    Invoke-Checked "keytool" @(
        "-genkeypair",
        "-keystore", $Keystore,
        "-storepass", "android",
        "-keypass", "android",
        "-alias", "androiddebugkey",
        "-keyalg", "RSA",
        "-keysize", "2048",
        "-validity", "10000",
        "-dname", "CN=Android Debug,O=Android,C=US"
    ) | Out-Null
}

Invoke-Checked $ApkSigner @(
    "sign",
    "--ks", $Keystore,
    "--ks-pass", "pass:android",
    "--key-pass", "pass:android",
    "--out", $SignedApk,
    $AlignedApk
)

Invoke-Checked $ApkSigner @("verify", "--verbose", $SignedApk)

Write-Output $SignedApk

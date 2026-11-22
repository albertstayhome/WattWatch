$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LibDll = Join-Path $ScriptDir 'LibreHardwareMonitorLib.dll'
$LhmUrl = 'https://github.com/LibreHardwareMonitor/LibreHardwareMonitor/releases/download/v0.9.6/LibreHardwareMonitor.zip'

if (-not (Test-Path $LibDll)) {
    Write-Host 'Downloading LibreHardwareMonitor v0.9.6...'
    $ZipPath = Join-Path ([System.IO.Path]::GetTempPath()) 'LibreHardwareMonitor_v0.9.6.zip'
    Invoke-WebRequest -Uri $LhmUrl -OutFile $ZipPath -UseBasicParsing
    Write-Host 'Extracting files into vendor/lhm...'
    Expand-Archive -Path $ZipPath -DestinationPath $ScriptDir -Force
    Remove-Item $ZipPath -Force -ErrorAction SilentlyContinue
} else {
    Write-Host 'LibreHardwareMonitorLib.dll already present, skipping download.'
}

$LhmConfig = Join-Path $ScriptDir 'LibreHardwareMonitor.exe.config'
$BridgeConfig = Join-Path $ScriptDir 'LhmBridge.exe.config'
if (Test-Path $LhmConfig) {
    Copy-Item -Path $LhmConfig -Destination $BridgeConfig -Force
    Write-Host 'Copied LibreHardwareMonitor.exe.config -> LhmBridge.exe.config'
}

$CscPath = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path $CscPath)) {
    throw "csc.exe not found at $CscPath"
}

$SourceCs = Join-Path $ScriptDir 'LhmBridge.cs'
$OutExe = Join-Path $ScriptDir 'LhmBridge.exe'

Write-Host 'Compiling LhmBridge.cs...'
& $CscPath /nologo /target:exe /platform:x64 /optimize+ "/out:$OutExe" "/reference:$LibDll" /reference:System.Web.Extensions.dll $SourceCs
if ($LASTEXITCODE -ne 0) {
    throw "csc.exe failed with exit code $LASTEXITCODE"
}

Write-Host "Build succeeded: $OutExe"

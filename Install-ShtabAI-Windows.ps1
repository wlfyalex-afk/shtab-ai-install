#Requires -Version 5.1
<#
Shtab.AI Windows installer entry point.
Save this file, then choose Run with PowerShell from its context menu.
The script requests administrator access automatically.
Windows Pro / Windows Server 2019+, x64, Hyper-V.
A clean Hyper-V acceptance run is still required for this installer revision.
#>
$ErrorActionPreference = 'Stop'
$exitCode = 0
try {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if (-not $PSCommandPath) { throw 'Save this script to a file before running it.' }
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '"'
        $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    $vmName = 'shtab-ai'
    $vmRoot = Join-Path $env:SystemDrive 'ShtabAI\shtab-ai'
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $revision = '91927f9e98ba61fb331bbfe49c36a6a7ae37d1c7'
    $expectedHash = '79be6f84273d995ccf80589998b17b7808ef0eb65a8be7249e41d60b6f964515'
    $workingDirectory = Join-Path $env:TEMP ('ShtabAI-Launcher-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $workingDirectory | Out-Null
    try {
        $installer = Join-Path $workingDirectory 'Install-ShtabAI.ps1'
        Write-Host 'Downloading the pinned Shtab.AI installer...'
        $uri = "https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/$revision/Install-ShtabAI.ps1"
        Invoke-WebRequest -UseBasicParsing -Uri $uri -OutFile $installer
        $actualHash = (Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expectedHash) { throw 'Installer checksum mismatch. Installation stopped.' }
        & $installer -Revision $revision -VMName $vmName -VMRoot $vmRoot -HTTPSPort 8445
    } finally {
        Remove-Item -LiteralPath $workingDirectory -Recurse -Force -ErrorAction SilentlyContinue
    }
} catch {
    Write-Host ('Installation stopped: ' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
[void](Read-Host 'Press Enter to close this window')
exit $exitCode

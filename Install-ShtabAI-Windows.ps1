#Requires -Version 5.1
[CmdletBinding()]
param(
    [ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName='ShtabAI-021',
    [ValidateSet('ask','cpu','nvidia','amd')][string]$Acceleration='ask',
    [ValidateSet('ask','local','lan')][string]$Access='ask',
    [ValidateRange(1024,65535)][int]$HTTPSPort=8445,
    [ValidatePattern('^(main|[a-f0-9]{40})$')][string]$Revision='main',
    [string]$InstallDir='',
    [switch]$KeepWindowOpen
)
$ErrorActionPreference='Stop'
try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -DistroName '+$DistroName+' -Acceleration '+$Acceleration+' -Access '+$Access+' -HTTPSPort '+$HTTPSPort+' -Revision '+$Revision+' -KeepWindowOpen'
        if ($InstallDir) {
            if ($InstallDir -match '["\r\n]' -or $InstallDir -notmatch '^[A-Za-z]:\\') { throw 'Use an absolute local installation path without quotes or newlines.' }
            $InstallDir=[IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
            $arguments+=' -InstallDir "'+$InstallDir+'"'
        }
        $process=Start-Process $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
    if ($Revision -eq 'main') {
        $Revision=(Invoke-RestMethod -Uri 'https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main' -Headers @{'User-Agent'='ShtabAI-Installer'}).sha
    }
    if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'Cannot resolve application revision.' }
    $work=Join-Path $env:TEMP ('shtab-launcher-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory $work | Out-Null
    try {
        $base="https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/$Revision/"
        $sums=Join-Path $work 'SHA256SUMS'
        $installer=Join-Path $work 'Install-WSL.ps1'
        Invoke-WebRequest -UseBasicParsing -Uri ($base+'SHA256SUMS') -OutFile $sums
        $entry=@(Get-Content $sums -Encoding UTF8 | Where-Object { $_ -match '^[a-f0-9]{64}  windows/Install-WSL\.ps1$' })
        if ($entry.Count -ne 1) { throw 'Installer checksum missing.' }
        $expected=($entry[0] -split '  ',2)[0]
        Invoke-WebRequest -UseBasicParsing -Uri ($base+'windows/Install-WSL.ps1') -OutFile $installer
        if ((Get-FileHash $installer -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Installer checksum mismatch.' }
        & $installer -DistroName $DistroName -Acceleration $Acceleration -Access $Access -HTTPSPort $HTTPSPort -Revision $Revision -InstallDir $InstallDir
    } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($KeepWindowOpen) { [void](Read-Host 'Press Enter to close this installation window') }
} catch {
    Write-Host ('Installation stopped: '+$_.Exception.Message) -ForegroundColor Red
    if ($_.InvocationInfo.PositionMessage) { Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor Yellow }
    if ($KeepWindowOpen) { [void](Read-Host 'Installation stopped. Copy the error above; press Enter to close') }
    exit 1
}

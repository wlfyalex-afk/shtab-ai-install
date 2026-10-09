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
function Receive-ShtabBootstrap {
    param([string]$BaseUri, [string]$Work, [string]$Checksums)
    # Keep the repository layout: Install-WSL dot-sources its GPU helper.
    $required=@('windows/Install-WSL.ps1','windows/Test-ShtabGPU.ps1','windows/Install-Progress.ps1')
    $lines=@(Get-Content -LiteralPath $Checksums -Encoding UTF8)
    foreach ($relative in $required) {
        $pattern='^[a-f0-9]{64}  '+[regex]::Escape($relative)+'$'
        $entry=@($lines | Where-Object { $_ -match $pattern })
        if ($entry.Count -ne 1) { throw ('Контрольная сумма отсутствует или повторяется: '+$relative) }
        $expected=($entry[0] -split '  ',2)[0]
        $destination=Join-Path $Work $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Write-Host ('Скачиваем и проверяем '+$relative+' (тайм-аут: 60 секунд)...') -ForegroundColor Cyan
        Invoke-WebRequest -UseBasicParsing -Uri ($BaseUri+$relative) -OutFile $destination -TimeoutSec 60
        if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
            throw ('Не совпала контрольная сумма: '+$relative)
        }
    }
    return (Join-Path $Work 'windows/Install-WSL.ps1')
}
try {
    Write-Host 'Установщик Штаб.AI запущен.' -ForegroundColor Cyan
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -DistroName '+$DistroName+' -Acceleration '+$Acceleration+' -Access '+$Access+' -HTTPSPort '+$HTTPSPort+' -Revision '+$Revision+' -KeepWindowOpen'
        if ($InstallDir) {
            if ($InstallDir -match '["\r\n]' -or $InstallDir -notmatch '^[A-Za-z]:\\') { throw 'Укажите полный путь на локальном диске без кавычек и переносов строки.' }
            $InstallDir=[IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
            $arguments+=' -InstallDir "'+$InstallDir+'"'
        }
        Write-Host 'Запрашиваем права администратора. Продолжайте в новом окне установки.' -ForegroundColor Yellow
        $process=Start-Process $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
    if ($Revision -eq 'main') {
        Write-Host '[1/4] Проверяем актуальную версию на api.github.com (тайм-аут: 60 секунд)...' -ForegroundColor Cyan
        $Revision=(Invoke-RestMethod -Uri 'https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main' -Headers @{'User-Agent'='ShtabAI-Installer'} -TimeoutSec 60).sha
    }
    if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'Не удалось определить версию приложения.' }
    $work=Join-Path $env:TEMP ('shtab-launcher-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory $work | Out-Null
    try {
        $base="https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/$Revision/"
        $sums=Join-Path $work 'SHA256SUMS'
        Write-Host '[2/4] Скачиваем контрольные суммы с raw.githubusercontent.com (тайм-аут: 60 секунд)...' -ForegroundColor Cyan
        Invoke-WebRequest -UseBasicParsing -Uri ($base+'SHA256SUMS') -OutFile $sums -TimeoutSec 60
        Write-Host '[3/4] Скачиваем установщик Windows и необходимые служебные скрипты...' -ForegroundColor Cyan
        $installer=Receive-ShtabBootstrap -BaseUri $base -Work $work -Checksums $sums
        Write-Host '[4/4] Файлы проверены. Проверяем Windows и подготавливаем WSL...' -ForegroundColor Cyan
        & $installer -DistroName $DistroName -Acceleration $Acceleration -Access $Access -HTTPSPort $HTTPSPort -Revision $Revision -InstallDir $InstallDir
    } finally { Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue }
    if ($KeepWindowOpen) { [void](Read-Host 'Нажмите Enter, чтобы закрыть окно установки') }
} catch {
    Write-Host ('Установка остановлена: '+$_.Exception.Message) -ForegroundColor Red
    if ($_.InvocationInfo.PositionMessage) { Write-Host $_.InvocationInfo.PositionMessage -ForegroundColor Yellow }
    if ($KeepWindowOpen) { [void](Read-Host 'Установка остановлена. Скопируйте ошибку выше; нажмите Enter, чтобы закрыть окно') }
    exit 1
}

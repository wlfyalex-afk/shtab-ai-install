#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName = 'ShtabAI-021',
    [ValidateSet('ask','auto','cpu','nvidia','amd')][string]$Acceleration = 'ask',
    [ValidateSet('ask','local','lan')][string]$Access = 'ask',
    [ValidateRange(1024,65535)][int]$HTTPSPort = 8445,
    [ValidatePattern('^(main|[a-f0-9]{40})$')][string]$Revision = 'main',
    [string]$InstallDir = '',
    [string]$CacheDir = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
. (Join-Path $PSScriptRoot 'Install-Progress.ps1')
. (Join-Path $PSScriptRoot 'Test-ShtabGPU.ps1')
. (Join-Path $PSScriptRoot 'Download-Cache.ps1')
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Invoke-WSL {
    & $script:wsl @args
    if ($LASTEXITCODE -ne 0) { throw "Команда WSL завершилась с ошибкой (код $LASTEXITCODE). Данные установки сохранены." }
}
function Invoke-Guest {
    $guestArguments = @($args)
    if ($guestArguments.Count -eq 3 -and $guestArguments[0] -eq '/bin/bash' -and $guestArguments[1] -eq '-c') {
        $shellFile = Join-Path $work ('command-' + [guid]::NewGuid().ToString('N') + '.sh')
        try {
            [IO.File]::WriteAllText($shellFile, ($guestArguments[2] + "`n").Replace("`r`n","`n"), (New-Object Text.UTF8Encoding($false)))
            $shellPath = (Invoke-WSL --distribution $DistroName --user root --exec /usr/bin/wslpath -u $shellFile | Out-String).Trim()
            if (-not $shellPath.StartsWith('/')) { throw 'Не удалось определить Linux-путь служебного файла.' }
            Invoke-WSL --distribution $DistroName --user root --exec /bin/bash $shellPath
        } finally {
            Remove-Item -LiteralPath $shellFile -Force -ErrorAction SilentlyContinue
        }
    } else {
        Invoke-WSL --distribution $DistroName --user root --exec @guestArguments
    }
}
function Test-WSLInstalled {
    # Windows PowerShell 5.1 turns redirected native stderr into errors.
    # Missing WSL is an expected probe result; use its exit code instead.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & $script:wsl --version 2>$null
        $versionExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    return ($versionExitCode -eq 0)
}
function Install-ShtabWSLRuntime {
    # The Windows inbox WSL can lack --no-distribution and --version.
    # Install Microsoft's signed modern runtime without a default distro.
    Write-Host 'Скачиваем официальный пакет WSL от Microsoft (MSI, без дистрибутива Linux)...' -ForegroundColor Cyan
    $release=Invoke-RestMethod -Uri 'https://api.github.com/repos/microsoft/WSL/releases/latest' -Headers @{'User-Agent'='ShtabAI-Installer'} -TimeoutSec 60
    if ($release.prerelease -or $release.draft) { throw 'Стабильная версия Microsoft WSL недоступна.' }
    $assets=@($release.assets | Where-Object { $_.name -match '^wsl\.[0-9.]+\.x64\.msi$' })
    if ($assets.Count -ne 1) { throw 'Официальный установочный пакет WSL x64 MSI недоступен.' }
    $uri=[string]$assets[0].browser_download_url
    if ($uri -notmatch '^https://github\.com/microsoft/WSL/releases/download/[^/]+/wsl\.[0-9.]+\.x64\.msi$') { throw 'Неожиданный адрес скачивания WSL.' }
    $directory=Join-Path $env:TEMP ('shtab-wsl-runtime-'+[guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $directory | Out-Null
    $package=Join-Path $directory 'wsl.x64.msi'
    $log=Join-Path $env:TEMP ('ShtabAI-WSL-MSI-'+[guid]::NewGuid().ToString('N')+'.log')
    try {
        Receive-File -Uri $uri -OutFile $package
        $signature=Get-AuthenticodeSignature -LiteralPath $package
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,\s*)O=Microsoft Corporation(,|$)') {
            throw 'Не удалось проверить подпись Microsoft у пакета WSL MSI.'
        }
        Write-Host 'Подпись Microsoft проверена. Устанавливаем WSL; это может занять несколько минут...' -ForegroundColor Cyan
        $msiexec=Join-Path $env:SystemRoot 'System32\msiexec.exe'
        if (-not [Environment]::Is64BitProcess) { $msiexec=Join-Path $env:SystemRoot 'Sysnative\msiexec.exe' }
        $process=Start-Process -FilePath $msiexec -ArgumentList ('/i "'+$package+'" /qn /norestart /L*v "'+$log+'"') -Wait -PassThru
        if ($process.ExitCode -notin @(0,3010)) { throw ("Установка Microsoft WSL завершилась с ошибкой (код $($process.ExitCode)). Журнал MSI: $log") }
        Write-Host ("Установка WSL завершена. Журнал MSI: $log") -ForegroundColor Green
    } finally {
        Remove-Item -LiteralPath $directory -Recurse -Force -ErrorAction SilentlyContinue
    }
}
function Invoke-ShtabBootConfiguration {
    param([string[]]$Arguments)
    $bcdedit=Join-Path $env:SystemRoot 'System32\bcdedit.exe'
    if (-not [Environment]::Is64BitProcess) { $bcdedit=Join-Path $env:SystemRoot 'Sysnative\bcdedit.exe' }
    $savedPreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $output=(& $bcdedit @Arguments 2>&1 | Out-String)
        $code=$LASTEXITCODE
    } finally { $ErrorActionPreference=$savedPreference }
    if ($code -ne 0) { throw "Не удалось проверить или изменить запуск гипервизора (код $code): $output" }
    return $output
}
function Test-ShtabVirtualizationReady {
    Write-Host 'Проверяем аппаратную виртуализацию и запуск гипервизора Windows...' -ForegroundColor Cyan
    $computer=Get-CimInstance Win32_ComputerSystem
    if ($computer.HypervisorPresent) {
        Write-Host 'Гипервизор Windows запущен.' -ForegroundColor Green
        return $true
    }
    $processors=@(Get-CimInstance Win32_Processor)
    if (-not $processors.Count) { throw 'Не удалось получить сведения о виртуализации процессора.' }
    foreach ($processor in $processors) {
        if ($processor.SecondLevelAddressTranslationExtensions -eq $false) {
            throw 'Процессор не предоставляет SLAT, необходимый для WSL2. В виртуальной машине включите вложенную виртуализацию на её хосте.'
        }
        if ($processor.VirtualizationFirmwareEnabled -eq $false) {
            throw 'Виртуализация выключена в BIOS/UEFI или не предоставлена виртуальной машине. Включите SVM / AMD-V для AMD либо Intel Virtualization Technology / VT-x для Intel, сохраните настройки и перезагрузите компьютер. Проверка: Диспетчер задач → Производительность → ЦП → Виртуализация: включена. После этого запустите установщик снова.'
        }
        if ($null -eq $processor.VirtualizationFirmwareEnabled) {
            throw 'Windows не сообщила состояние виртуализации. Проверьте Диспетчер задач → Производительность → ЦП и настройки BIOS/UEFI перед установкой WSL2.'
        }
    }
    $boot=Invoke-ShtabBootConfiguration -Arguments @('/enum','{current}')
    if ($boot -match '(?im)^\s*hypervisorlaunchtype\s+Off\s*$') {
        [void](Invoke-ShtabBootConfiguration -Arguments @('/set','{current}','hypervisorlaunchtype','Auto'))
        Write-Host 'Автоматический запуск гипервизора включён. Перезагрузите Windows и снова запустите установщик.' -ForegroundColor Yellow
        return $false
    }
    Write-Host 'Виртуализация включена, но гипервизор ещё не запущен. Перезагрузите Windows и снова запустите установщик. Если сообщение повторяется после перезагрузки, проверьте BIOS/UEFI и вложенную виртуализацию; Ubuntu пока не скачивается.' -ForegroundColor Yellow
    return $false
}
function Quote-Shell([string]$Value) {
    $q = [string][char]39
    return $q + $Value.Replace($q,($q + [char]34 + $q + [char]34 + $q)) + $q
}
function Write-UTF8([string]$Path,[string]$Value) {
    [IO.File]::WriteAllText($Path,$Value.Replace("`r`n","`n"),(New-Object Text.UTF8Encoding($false)))
}
function Read-Distros {
    $result = & $script:wsl --list --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить список дистрибутивов WSL.' }
    return @($result | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
}
function Verify-Package([string]$Root) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'SHA256SUMS') -Encoding UTF8) {
        if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Некорректный список контрольных сумм.' }
        $expected = $Matches[1]; $relative = $Matches[2]
        if ($relative -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Недопустимый путь файла в установочном комплекте.' }
        $actual = (Get-FileHash -LiteralPath (Join-Path $Root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Не совпала контрольная сумма: $relative" }
    }
}
function Complete-ShtabGuestInstallation {
    $started = Get-Date
    $deadline = $started.AddHours(3)
    do {
        $snapshot = (Invoke-Guest /usr/bin/python3 /opt/shtab-ai-021/scripts/install-progress.py --json | Out-String) | ConvertFrom-Json
        $status = [string]$snapshot.status
        Show-AppStage $status
        if ($status -in @('DOWNLOADING_QWEN','DOWNLOADING_WHISPER')) { Show-ModelProgress $snapshot.progress } else { Show-OperationProgress $snapshot.operation }
        if ($status -eq 'READY_FOR_ADMIN') { break }
        if ($status -like 'FAILED*' -or (Get-Date) -gt $deadline) {
            Invoke-Guest /bin/journalctl -u shtab-ai-install -n 80 --no-pager
            throw "Установка не завершена: $status. Запустите тот же установщик для докачки; данные сохранены."
        }
        Start-Sleep -Seconds 10
    } while ($true)
    Write-Progress -Id 3 -Activity 'Текущая операция' -Completed
    Show-Stage 15 'Настройка сертификата HTTPS'
    Invoke-Guest /opt/shtab-ai-021/shtabctl certificate
    $cert = Join-Path $root 'shtab-ai-root.crt'
    $guestCert = (Invoke-Guest wslpath -u $cert | Out-String).Trim()
    Invoke-Guest /bin/cp /opt/shtab-ai-021/shtab-ai-root.crt $guestCert
    $imported = Import-Certificate -FilePath $cert -CertStoreLocation Cert:\CurrentUser\Root
    # The background runtime can update the LAN address while models download.
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifest.CertificateThumbprint = $imported.Thumbprint
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    Show-Stage 16 'Создание администратора'
    Write-Host 'Создайте первого администратора (пароль не отображается при вводе):'
    $userCount = (Invoke-Guest /bin/bash -c "cd /opt/shtab-ai-021; bash scripts/dc.sh exec -T db psql -U shtab_ai -d shtab_ai -Atc 'SELECT count(*) FROM secretary_users'" | Out-String).Trim()
    if ($userCount -notmatch '^[0-9]+$') { throw 'Не удалось проверить наличие администратора.' }
    if ([long]$userCount -eq 0) { Invoke-Guest /opt/shtab-ai-021/shtabctl bootstrap }
    else { Write-Host 'Администратор уже создан; пользователи сохранены.' -ForegroundColor Green }
    $url = "https://localhost:$HTTPSPort/login"
    Write-UTF8 $shortcut ("[InternetShortcut]`nURL=$url`n")
    $shell=New-Object -ComObject WScript.Shell
    $managerLink=$shell.CreateShortcut((Join-Path $desktop ($DistroName+'-Manager.lnk')))
    $managerLink.TargetPath=$powershell
    $managerLink.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $root 'Manage-ShtabAI.ps1')+'" -ManifestPath "'+$manifestPath+'"'
    $managerLink.WorkingDirectory=$root
    $managerLink.Description='Shtab.AI: backups, restore, status and service control'
    $managerLink.Save()
    # Check the Windows-to-WSL path with normal certificate validation.
    Show-Stage 17 'Проверка страницы входа и сетевого доступа'
    $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15
    if ($response.StatusCode -ne 200) { throw 'Проверка страницы входа из Windows не пройдена.' }
    Write-Host "Штаб.AI готова: $url | режим ускорения: $Acceleration" -ForegroundColor Green
    $currentManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $lanAddress = [string]$currentManifest.LANAddress
    if ($manifest.Network -and $lanAddress) {
        $networkURL = "https://${lanAddress}:$HTTPSPort/login"
        $networkResponse = Invoke-WebRequest -UseBasicParsing -Uri $networkURL -TimeoutSec 15
        if ($networkResponse.StatusCode -ne 200) { throw 'Проверка адреса в локальной сети не пройдена.' }
        Write-Host "Адрес в сети: $networkURL. На других компьютерах добавьте сертификат в доверенные: $cert"
        Write-Host 'Проверьте вход и загрузку записи с другого компьютера: локальная проверка не проверяет его браузер и брандмауэр.'
    }
    Complete-Stages
    Start-Process $url
}

function Resume-ShtabInstallation {
    param([string]$IndexPath)
    $index = Get-Content -LiteralPath $IndexPath -Raw | ConvertFrom-Json
    if ($index.Product -ne 'ShtabAI' -or $index.Backend -ne 'WSL2' -or $index.DistroName -ne $DistroName -or $index.Root -notmatch '^[A-Za-z]:\\' -or $index.Root -match '["\r\n]') { throw 'Некорректная регистрация установки.' }
    $root = [IO.Path]::GetFullPath($index.Root).TrimEnd('\')
    if ($InstallDir -and [IO.Path]::GetFullPath($InstallDir).TrimEnd('\') -ine $root) { throw 'Для докачки используйте прежнюю папку установки.' }
    $manifestPath = Join-Path $root 'installation.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    if ($manifest.Product -ne 'ShtabAI' -or $manifest.Backend -ne 'WSL2' -or $manifest.DistroName -ne $DistroName -or [IO.Path]::GetFullPath($manifest.Root).TrimEnd('\') -ine $root) { throw 'Описание установки не соответствует регистрации.' }
    if ($DistroName -notin @(Read-Distros)) { throw 'Зарегистрированная среда WSL отсутствует. Данные сохранены.' }
    foreach ($relative in @('Start-ShtabRuntime.ps1','ollama\ollama.exe')) {
        if (-not (Test-Path -LiteralPath (Join-Path $root $relative))) { throw ('Подготовка Windows не завершена: ' + $relative + '. Докачка моделей пока недоступна; данные сохранены.') }
    }
    $HTTPSPort = [int]$manifest.HTTPSPort
    $Acceleration = [string]$manifest.Acceleration
    $desktop = [Environment]::GetFolderPath('Desktop')
    $shortcut = [string]$manifest.Shortcut
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $work = Join-Path $env:TEMP ('shtab-resume-' + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $work | Out-Null
    try {
        Write-Host ('Продолжаем Штаб.AI в ' + $root + '. Модели, база и настройки сохраняются.') -ForegroundColor Cyan
        $task = Get-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' -ErrorAction Stop
        if ($task.State -ne 'Running') { Start-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' }
        $deadline = (Get-Date).AddMinutes(3)
        $stateFile = Join-Path $root 'runtime-state.json'
        do {
            if (Test-Path -LiteralPath $stateFile) {
                $runtimeState = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
                # Do not reuse an old gateway after a reboot.
                $route = (Invoke-Guest /sbin/ip -4 route show default | Out-String).Trim()
                if ($route -match '^default via (\d+\.\d+\.\d+\.\d+)' -and $runtimeState.Endpoint -eq ('http://' + $Matches[1] + ':11435') -and $runtimeState.Ready) { break }
            }
            if ((Get-Date) -gt $deadline) { throw 'Ollama не запустилась. Проверьте задачу автозапуска и ollama-error.log; файлы сохранены.' }
            Start-Sleep -Seconds 3
        } while ($true)
        $httpsHost = if ($runtimeState.LANAddress) { [string]$runtimeState.LANAddress } else { 'localhost' }
        $resumeFile = Join-Path $work 'resume.sh'
        $resumeText = @'
#!/bin/bash
# Continue the installed version with its original secrets, volumes and cache.
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo 'Запустите с sudo.'; exit 1; }
cd /opt/shtab-ai-021
for file in installation-created .env compose.yaml secrets/db_password secrets/flask_secret scripts/provision.sh; do
    [[ -f $file ]] || { echo "Неполная конфигурация установки: $file. Данные сохранены."; exit 1; }
done
if [[ -f storage.json ]]; then python3 scripts/configure-storage.py verify; fi
unit=shtab-ai-install.service
systemctl cat "$unit" >/dev/null
active=$(systemctl show "$unit" -p ActiveState --value)
if [[ $active == activating || $active == active ]]; then
    echo 'Установка уже идёт; подключаемся к её прогрессу.'
    exit 0
fi
status=$(cat /var/lib/shtab-ai-021/status 2>/dev/null || true)
if [[ $status == READY_FOR_ADMIN ]]; then
    echo 'Компоненты установлены; завершаем настройку.'
    exit 0
fi
if [[ -n ${1:-} ]]; then
    # WSL's NAT gateway may have changed after a reboot. Do this only while idle.
    python3 - "$1" "${2:-localhost}" <<'PY'
import importlib.util, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('runtime_config', 'scripts/runtime-config.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.reconcile(Path('/opt/shtab-ai-021'), sys.argv[1], sys.argv[2], 'READY_FOR_ADMIN')
PY
fi
echo 'Продолжаем установку. Полученные файлы моделей и данные сохраняются.'
systemctl reset-failed "$unit"
# Avoid reading a stale FAILED status before the background process starts.
printf 'RESUMING\n' > /var/lib/shtab-ai-021/status
systemctl start --no-block "$unit"
'@
        Write-UTF8 $resumeFile $resumeText
        $guestResume = (Invoke-Guest wslpath -u $resumeFile | Out-String).Trim()
        Invoke-Guest /bin/bash $guestResume ([string]$runtimeState.Endpoint) $httpsHost
        Complete-ShtabGuestInstallation
    } finally {
        Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed
        Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
        Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Show-Stage 1 'Проверка компьютера и подготовка WSL'
Write-Host 'Проверяем версию Windows, оперативную память и процессор...' -ForegroundColor Cyan
$os = Get-CimInstance Win32_OperatingSystem
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'Требуется 64-разрядная Windows x64.' }
if ($os.ProductType -eq 1 -and [int]$os.BuildNumber -lt 19045) { throw 'Требуется Windows 10 22H2 или Windows 11 (Home/Pro/Enterprise/Education).' }
if ($os.ProductType -ne 1) { throw 'Поддерживается Windows 10/11 Home/Pro. Windows Server не поддерживается этим установщиком.' }
if ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory -lt 11811160064) { throw 'Требуется не менее 12 ГБ оперативной памяти; рекомендуется 16–24 ГБ и более.' }
if ([Environment]::ProcessorCount -lt 4) { throw 'Требуется не менее 4 логических ядер процессора.' }
$script:wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not [Environment]::Is64BitProcess) { $script:wsl = Join-Path $env:SystemRoot 'Sysnative\wsl.exe' }
$restart = $false
foreach ($name in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform')) {
    Write-Host ("Проверяем компонент Windows: $name (это может занять несколько минут)...") -ForegroundColor Cyan
    $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
    if ($feature.State -ne 'Enabled') {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart
        $restart = $restart -or $result.RestartNeeded
    }
}
if ($restart) { Write-Host 'Компоненты WSL включены. Перезагрузите Windows и снова запустите этот установщик.' -ForegroundColor Yellow; return }
if (-not (Test-Path $script:wsl)) { throw 'Программа WSL недоступна. Перезагрузите Windows и повторите установку.' }
if (-not (Test-ShtabVirtualizationReady)) { return }
Write-Host 'Проверяем WSL...' -ForegroundColor Cyan
if (-not (Test-WSLInstalled)) {
    Write-Host 'Устанавливаем WSL без стандартного дистрибутива Linux...' -ForegroundColor Cyan
    Install-ShtabWSLRuntime
    Write-Host 'WSL установлена. Перезагрузите Windows и снова запустите установщик.' -ForegroundColor Yellow
    return
}
$resumeIndex = Join-Path (Join-Path $env:LOCALAPPDATA 'ShtabAI') ($DistroName + '.json')
$recoverManifest = $null
if (Test-Path -LiteralPath $resumeIndex) {
    $record = Get-Content -LiteralPath $resumeIndex -Raw | ConvertFrom-Json
    if ($record.Product -ne 'ShtabAI' -or $record.Backend -ne 'WSL2' -or $record.DistroName -ne $DistroName -or $record.Root -notmatch '^[A-Za-z]:\\' -or $record.Root -match '["\r\n]') { throw 'Некорректная регистрация установки.' }
    $recordRoot = [IO.Path]::GetFullPath($record.Root).TrimEnd('\')
    if ($InstallDir -and [IO.Path]::GetFullPath($InstallDir).TrimEnd('\') -ine $recordRoot) { throw 'Для докачки используйте прежнюю папку установки.' }
    $recoverManifest = Get-Content -LiteralPath (Join-Path $recordRoot 'installation.json') -Raw | ConvertFrom-Json
    if ($recoverManifest.Product -ne 'ShtabAI' -or $recoverManifest.Backend -ne 'WSL2' -or $recoverManifest.DistroName -ne $DistroName -or [IO.Path]::GetFullPath($recoverManifest.Root).TrimEnd('\') -ine $recordRoot) { throw 'Описание установки не соответствует регистрации.' }
    $prepared = $false
    if ($DistroName -in @(Read-Distros)) {
        & $script:wsl --distribution $DistroName --user root --exec /usr/bin/test -f /etc/systemd/system/shtab-ai-install.service
        $prepared = $LASTEXITCODE -eq 0
    }
    if ($prepared) { Resume-ShtabInstallation $resumeIndex; return }
    Write-Host 'Продолжаем подготовку Windows и Ubuntu. Архивы в кэше сохраняются.' -ForegroundColor Cyan
    $InstallDir = $recordRoot
    $Revision = [string]$recoverManifest.Revision
    $HTTPSPort = [int]$recoverManifest.HTTPSPort
    $Acceleration = [string]$recoverManifest.Acceleration
    $Access = if ($recoverManifest.Network) { 'lan' } else { 'local' }
    if ($recoverManifest.CacheDir) { $CacheDir = [string]$recoverManifest.CacheDir }
}
Write-Host 'Обновляем WSL...' -ForegroundColor Cyan
Invoke-WSL --update --web-download
if (-not $recoverManifest -and $DistroName -in @(Read-Distros)) { throw "Дистрибутив $DistroName уже существует. Перед новой установкой воспользуйтесь удалением." }
if (-not $recoverManifest -and (Get-NetTCPConnection -LocalPort $HTTPSPort -State Listen -ErrorAction SilentlyContinue)) { throw "Порт Windows $HTTPSPort занят." }
if (-not $recoverManifest -and (Get-NetTCPConnection -LocalPort 18093 -State Listen -ErrorAction SilentlyContinue)) { throw 'Порт Windows 18093 занят.' }
if (-not $recoverManifest -and (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue)) { throw 'Порт Windows 11435 занят; для Ollama нужен отдельный свободный порт.' }
$defaultRoot = Join-Path $env:LOCALAPPDATA ('ShtabAI\' + $DistroName)
if (-not $InstallDir) {
    Show-Disks
    $InstallDir = Read-Host ("Папка установки (например D:\Apps\$DistroName) [$defaultRoot]")
    if (-not $InstallDir) { $InstallDir=$defaultRoot }
}
if ($InstallDir -notmatch '^[A-Za-z]:\\' -or $InstallDir -match '["\r\n]') { throw 'Укажите полный путь на локальном диске.' }
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$driveLetter = [IO.Path]::GetPathRoot($root).Substring(0,1)
$volume = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
if ($volume.FileSystem -ne 'NTFS' -or $volume.DriveType -ne 'Fixed') { throw 'Для WSL и моделей выберите локальный диск с файловой системой NTFS.' }
if ($root.Length -lt 4) { throw 'Выберите новую папку приложения, а не корень диска.' }
$ancestor=Split-Path $root
while ($ancestor -and -not (Test-Path $ancestor)) { $ancestor=Split-Path $ancestor }
if (-not $ancestor) { throw 'Родительская папка установки недоступна.' }
$checkAncestor=$ancestor
while ($checkAncestor) {
    if ((Get-Item $checkAncestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Путь установки не должен содержать ссылки или точки соединения.' }
    $checkAncestor=Split-Path $checkAncestor
}
$indexPath=Join-Path (Join-Path $env:LOCALAPPDATA 'ShtabAI') ($DistroName+'.json')
if (-not $recoverManifest -and (Test-Path $indexPath)) { throw 'Установка уже зарегистрирована; сначала воспользуйтесь удалением.' }
if (-not $recoverManifest -and (Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0,1))).Free -lt 42949672960) { throw 'На диске установки требуется не менее 40 ГиБ свободного места.' }
if (-not $recoverManifest -and (Test-Path $root)) { throw "Папка установки уже существует: $root. Сначала воспользуйтесь удалением." }
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = Join-Path $desktop ($DistroName + '.url')
if (-not $recoverManifest -and (Test-Path $shortcut)) { throw 'Ярлык на рабочем столе уже существует; сначала удалите предыдущую установку.' }
$wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*localhostForwarding\s*=\s*false\s*$') {
    throw 'В .wslconfig отключён localhostForwarding. Включите его перед установкой.'
}
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*networkingMode\s*=\s*(mirrored|virtioproxy|none)\s*$') {
    throw 'Требуется сетевой режим WSL NAT. Существующий .wslconfig сохранён; выберите NAT перед установкой.'
}
Write-Host 'Обнаруженные видеокарты Windows:'
Get-CimInstance Win32_VideoController | Select-Object -Property @('Name','DriverVersion') | Format-Table -AutoSize | Out-Host
if ($Acceleration -eq 'ask') {
    $choice = Read-Host 'Ускорение: 1 — автоматическая проверка; 2 — процессор [1]'
    if ($choice -in @('','1')) { $Acceleration='auto' } elseif ($choice -eq '2') { $Acceleration='cpu' } else { throw 'Некорректный выбор.' }
}
try {
    $antiviruses=@(Get-CimInstance -Namespace root/SecurityCenter2 -ClassName AntiVirusProduct -ErrorAction Stop)
    Write-Host ('Антивирусы: '+(($antiviruses | ForEach-Object { $_.displayName }) -join ', '))
} catch { Write-Host 'Список антивирусов недоступен через Windows Security Center.' }
Write-Host 'При блокировке Kaspersky вручную приостановите защиту на время установки либо задайте точечные исключения для проверенных файлов. После установки включите защиту и проверьте запуск.'
$gpuDecision=Get-ShtabAcceleration $Acceleration
$Acceleration=$gpuDecision.Mode
if ($Access -eq 'ask') {
    $answer = Read-Host 'Доступ: 1 — только этот компьютер; 2 — локальная сеть [2]'
    if ($answer -in @('','2')) { $Access='lan' } elseif ($answer -eq '1') { $Access='local' } else { throw 'Некорректный выбор режима доступа.' }
}
$lanAddress = ''
if ($Access -eq 'lan') {
    $adapters = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address } | ForEach-Object {
        [pscustomobject]@{Interface=$_.InterfaceAlias; IP=$_.IPv4Address[0].IPAddress; Index=$_.InterfaceIndex; Guid=[string]$_.NetAdapter.InterfaceGuid}
    })
    if (-not $adapters.Count) { throw 'Не найден активный сетевой адаптер со шлюзом IPv4. Выберите локальный доступ.' }
    for ($i=0; $i -lt $adapters.Count; $i++) { Write-Host "$($i+1) - $($adapters[$i].Interface) / $($adapters[$i].IP)" }
    $selected = Read-Host 'Выберите сетевой адаптер [1]'
    if (-not $selected) { $selected='1' }
    $number=0
    if (-not [int]::TryParse($selected,[ref]$number) -or $number -lt 1 -or $number -gt $adapters.Count) { throw 'Некорректный выбор сетевого адаптера.' }
    $lanAddress=$adapters[$number-1].IP
    $profile = Get-NetConnectionProfile -InterfaceIndex $adapters[$number-1].Index -ErrorAction SilentlyContinue
    if (-not $profile) { throw 'Не удалось определить профиль сети. Проверьте подключение.' }
    Write-Host ('Сеть: '+$profile.Name+'; IPv4: '+$lanAddress+'; профиль: '+$profile.NetworkCategory)
    if ($profile.NetworkCategory -eq 'Public') {
        $trusted = Read-Host 'Это доверенная домашняя/офисная сеть? Введите Д, чтобы применить частный профиль; Н — остановить установку'
        if ($trusted -notin @('Д','д','Y','y')) { throw 'Сеть оставлена общедоступной. Для LAN нужен доверенный частный профиль.' }
        Set-NetConnectionProfile -InterfaceIndex $adapters[$number-1].Index -NetworkCategory Private
        $profile=Get-NetConnectionProfile -InterfaceIndex $adapters[$number-1].Index
        if ($profile.NetworkCategory -ne 'Private') { throw 'Частный профиль не применился.' }
    }
}
if ($Revision -eq 'main') {
    $head = Invoke-RestMethod -Uri 'https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main' -Headers @{ 'User-Agent'='ShtabAI-Installer' }
    $Revision = $head.sha
}
if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'Не удалось определить фиксированную версию приложения.' }
if (-not $CacheDir) {
    $defaultCache = Join-Path (Split-Path $root -Parent) 'Shtab.AI-Cache'
    $CacheDir = Read-Host ("Папка постоянного кэша дистрибутивов и моделей [$defaultCache]")
    if (-not $CacheDir) { $CacheDir = $defaultCache }
}
if ($CacheDir -notmatch '^[A-Za-z]:\\' -or $CacheDir -match '["\r\n]') { throw 'Укажите полный локальный путь к кэшу.' }
$script:CacheDir = [IO.Path]::GetFullPath($CacheDir).TrimEnd('\')
if ($script:CacheDir -ieq $root -or $script:CacheDir.StartsWith($root + '\', [StringComparison]::OrdinalIgnoreCase) -or $root.StartsWith($script:CacheDir + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Кэш и приложение должны находиться в отдельных папках.' }
New-Item -ItemType Directory -Path $script:CacheDir -Force | Out-Null
Write-Host ("Постоянный кэш: $script:CacheDir. При удалении приложения он сохраняется.") -ForegroundColor Cyan
$work = Join-Path $ancestor ('shtab-wsl-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
    Show-Stage 2 'Загрузка и проверка пакета Штаб.AI'
    $archive = Join-Path $work 'source.zip'
    Write-Host "Скачиваем Штаб.AI, версия $Revision"
    Receive-File -Uri "https://github.com/wlfyalex-afk/shtab-ai-install/archive/$Revision.zip" -OutFile $archive
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Недопустимый путь файла в архиве.' }
        }
    } finally { $zip.Dispose() }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $work 'source')
    $roots = @(Get-ChildItem (Join-Path $work 'source') -Directory)
    if ($roots.Count -ne 1) { throw 'Некорректная структура архива приложения.' }
    $package = $roots[0].FullName
    Verify-Package $package
    Show-Stage 3 'Загрузка и проверка Ubuntu'
    $image = Join-Path $work 'ubuntu.wsl'
    $imageName = 'ubuntu-24.04.5-wsl-amd64.wsl'
    $imageBase = 'https://releases.ubuntu.com/24.04/'
    $sums = Join-Path $work 'Ubuntu-SHA256SUMS'
    Receive-File -Uri ($imageBase + 'SHA256SUMS') -OutFile $sums
    $text = [IO.File]::ReadAllText($sums,[Text.Encoding]::UTF8)
    $matches = [regex]::Matches($text,('(?im)^([a-f0-9]{64})[ \t]+\*?' + [regex]::Escape($imageName) + '[ \t]*\r?$'))
    if ($matches.Count -ne 1) { throw 'Контрольная сумма образа Ubuntu WSL недоступна.' }
    Write-Host 'Скачиваем образ Ubuntu 24.04 для WSL...'
    Receive-CachedFile -Uri ($imageBase + $imageName) -OutFile $image -SHA256 $matches[0].Groups[1].Value
    if ((Get-FileHash $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne $matches[0].Groups[1].Value.ToLowerInvariant()) { throw 'Не совпала контрольная сумма образа Ubuntu.' }
    Show-Stage 4 'Создание Linux-среды и проверка видеокарты'
    New-Item -ItemType Directory $root -Force | Out-Null
    $manifest = [ordered]@{ Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root; Revision=$Revision; HTTPSPort=$HTTPSPort; Shortcut=$shortcut; TaskName=('ShtabAI-' + $DistroName + '-Start'); CertificateThumbprint=''; WSLConfigCreated=$false; WSLConfigText=''; Acceleration=$Acceleration; Network=($Access -eq 'lan'); LANAddress=$lanAddress; LANInterfaceGuid=$(if ($Access -eq 'lan') { $adapters[$number-1].Guid } else { '' }); LANRule=('ShtabAI-' + $DistroName + '-LAN') }
    if ($recoverManifest) {
        # Keep ownership of WSL configuration, CA and other previously saved fields.
        $savedManifest = [ordered]@{}
        foreach ($property in $recoverManifest.PSObject.Properties) { $savedManifest[$property.Name] = $property.Value }
        $manifest = $savedManifest
    }
    $manifest['CacheDir'] = $script:CacheDir
    $manifestPath = Join-Path $root 'installation.json'
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    New-Item -ItemType Directory (Split-Path $indexPath) -Force | Out-Null
    Write-UTF8 $indexPath (@{Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root} | ConvertTo-Json)
    $backupPath = if ($root -eq $defaultRoot) { Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ShtabAI-Backups' } else { $root+'-Backups' }
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
    $manifest['BackupPath'] = $backupPath
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    if (-not (Test-Path $wslConfig)) {
        $hostMemory = (Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory
        $memoryGB = if ($hostMemory -lt 16106127360) { 8 } else { [Math]::Max(10,[Math]::Floor($hostMemory/1073741824)-6) }
        $cores = [Environment]::ProcessorCount
        $manifest.WSLConfigText = "[wsl2]`nmemory=${memoryGB}GB`nprocessors=$cores`nswap=4GB`nlocalhostForwarding=true`nnetworkingMode=nat`n"
        Write-UTF8 $wslConfig $manifest.WSLConfigText
        $manifest.WSLConfigCreated = $true
        Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        Write-Host "Лимит WSL: $memoryGB ГБ оперативной памяти, ядер процессора: $cores. Другие дистрибутивы WSL не останавливаются."
    }
    if ($DistroName -notin @(Read-Distros)) { Invoke-WSL --import $DistroName (Join-Path $root 'distro') $image --version 2 }
    $configPath = "\\wsl.localhost\$DistroName\etc\wsl.conf"
    $configText = "[boot]`nsystemd=true`n"
    [IO.File]::WriteAllText($configPath,$configText,(New-Object Text.UTF8Encoding($false)))
    if ([IO.File]::ReadAllText($configPath) -cne $configText) { throw 'Не удалось проверить настройки WSL.' }
    Invoke-WSL --terminate $DistroName
    $deadline = (Get-Date).AddMinutes(2)
    do {
        $pidOne = (Invoke-Guest /bin/cat /proc/1/comm | Out-String).Trim()
        if ($pidOne -eq 'systemd') { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($pidOne -ne 'systemd') { throw 'Служба systemd в WSL не запустилась. Обновите WSL, затем удалите эту тестовую установку и повторите запуск.' }
    if ($Acceleration -eq 'nvidia') {
        & $script:wsl --distribution $DistroName --user root --exec /usr/lib/wsl/lib/nvidia-smi -L
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'NVIDIA недоступна в WSL. Автоматически продолжаем на CPU.' -ForegroundColor Yellow
            $Acceleration = 'cpu'; $manifest.Acceleration = 'cpu'
            Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        }
    }
    Write-Host 'Скачиваем Ollama для Windows (включая библиотеки видеокарт)...'
    Show-Stage 5 'Установка Ollama и библиотек видеокарт'
    $ollamaZip = Join-Path $work 'ollama.zip'
    Receive-CachedFile -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64.zip' -OutFile $ollamaZip -SHA256 '428c94622a04764b318ddf13a061898edf69e32ffa896f638ed6015fd3f33288'
    if ((Get-FileHash $ollamaZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '428c94622a04764b318ddf13a061898edf69e32ffa896f638ed6015fd3f33288') { throw 'Не совпала контрольная сумма Ollama.' }
    $ollamaDir = Join-Path $root 'ollama'
    if (-not (Test-Path -LiteralPath (Join-Path $root 'ollama-ready'))) {
        Expand-Archive -LiteralPath $ollamaZip -DestinationPath $ollamaDir -Force
        if ($Acceleration -eq 'amd') {
            $rocmZip = Join-Path $work 'ollama-rocm.zip'
            Receive-CachedFile -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64-rocm.zip' -OutFile $rocmZip -SHA256 'a290510b3ee3b743de54eb3fbae99b69f19a49485f42ce6bcf4a1a6f86e4ba01'
            if ((Get-FileHash $rocmZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'a290510b3ee3b743de54eb3fbae99b69f19a49485f42ce6bcf4a1a6f86e4ba01') { throw 'Не совпала контрольная сумма библиотек AMD.' }
            Expand-Archive -LiteralPath $rocmZip -DestinationPath $ollamaDir -Force
        }
        Write-UTF8 (Join-Path $root 'ollama-ready') 'ready'
    }
    Show-Stage 6 'Настройка автозапуска, сети и запуск Ollama'
    Copy-Item -LiteralPath (Join-Path $package 'windows\Start-ShtabRuntime.ps1') -Destination $root
    Copy-Item -LiteralPath (Join-Path $package 'windows\Manage-ShtabAI.ps1') -Destination $root
    Copy-Item -LiteralPath (Join-Path $package 'windows\Collect-ShtabDiagnostics.ps1') -Destination $root
    $manifest['ModelsPath'] = Join-Path $script:CacheDir 'qwen'
    $manifest['CacheDir'] = $script:CacheDir
    New-Item -ItemType Directory -Path $manifest.ModelsPath -Force | Out-Null
    Test-CachedOllamaModels $manifest.ModelsPath
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    $runtime = Join-Path $root 'Start-ShtabRuntime.ps1'
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runtime + '" -ManifestPath "' + $manifestPath + '"')
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
    $triggers = @((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -AtLogOn -User $user))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    if ($manifest.Network -and -not (Get-NetFirewallRule -Name $manifest.LANRule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $manifest.LANRule -DisplayName ('ShtabAI LAN ' + $DistroName) -Group 'ShtabAI' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $lanAddress -LocalPort $HTTPSPort -RemoteAddress LocalSubnet -Profile @('Private','Domain') | Out-Null
    }
    Register-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Force | Out-Null
    if ((Get-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\').State -ne 'Running') { Start-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' }
    $deadline=(Get-Date).AddMinutes(3)
    $stateFile=Join-Path $root 'runtime-state.json'
    while (-not (Test-Path $stateFile)) {
        if ((Get-Date) -gt $deadline) { throw 'Истекло время запуска Ollama. Проверьте задачу автозапуска и ollama-error.log.' }
        Start-Sleep -Seconds 3
    }
    $runtimeState=Get-Content $stateFile -Raw | ConvertFrom-Json
    $guestMode = if ($Acceleration -eq 'nvidia') { 'nvidia' } else { 'cpu' }
    $lanAddress = [string]$runtimeState.LANAddress
    $httpsHost = if ($lanAddress) { $lanAddress } else { 'localhost' }
    Show-Stage 7 'Подготовка пакетов Ubuntu и запуск установки приложения'
    $guestArchive = (Invoke-Guest wslpath -u $archive | Out-String).Trim()
    $guestCache = (Invoke-Guest wslpath -u $script:CacheDir | Out-String).Trim()
    $setup = 'set -euo pipefail; apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y unzip python3 curl ca-certificates openssl; work=$(mktemp -d); trap ''rm -rf "$work"'' EXIT; unzip -q ' + (Quote-Shell $guestArchive) + ' -d "$work"; cd "$work"/*; sha256sum --quiet -c SHA256SUMS; SHTAB_EXTERNAL_OLLAMA_ENDPOINT=' + (Quote-Shell $runtimeState.Endpoint) + ' SHTAB_MODEL_CACHE=' + (Quote-Shell $guestCache) + ' SHTAB_WINDOWS_ACCELERATION=' + $Acceleration + ' SHTAB_HTTPS_PORT=' + $HTTPSPort + ' bash install.sh ' + $httpsHost + ' ' + $guestMode
    Invoke-Guest /bin/bash -c $setup
    $guestBackups = (Invoke-Guest wslpath -u $backupPath | Out-String).Trim()
    Invoke-Guest /bin/bash -c ('printf ''%s\n'' ' + (Quote-Shell $guestBackups) + ' > /opt/shtab-ai-021/backup-directory')
    Complete-ShtabGuestInstallation
} finally {
    Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}


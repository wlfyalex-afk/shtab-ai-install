#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([switch]$Resume, [switch]$RestartEarly, [string]$CacheDir = '')
$ErrorActionPreference = 'Stop'
function Receive-CachedFile {
    param([string]$Uri, [string]$OutFile, [string]$SHA256)
    if ($SHA256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Нет SHA256 для файла кэша.' }
    $folder = Join-Path $script:CacheDir 'archives'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $cached = Join-Path $folder ($SHA256.ToLowerInvariant() + '-' + (Split-Path $OutFile -Leaf))
    $lock = [IO.File]::Open(($cached + '.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        if (Test-Path -LiteralPath $cached) {
            Write-Host ('Проверяем SHA256 файла из кэша: ' + (Split-Path $OutFile -Leaf)) -ForegroundColor Cyan
            if ((Get-FileHash -LiteralPath $cached -Algorithm SHA256).Hash -ine $SHA256) {
                Remove-Item -LiteralPath $cached -Force
                Write-Host 'Файл повреждён. Скачиваем заново.' -ForegroundColor Yellow
            }
        }
        if (-not (Test-Path -LiteralPath $cached)) {
            # Also accept standard archive names copied here by the user.
            $names = @((Split-Path $OutFile -Leaf), ([Uri]$Uri).Segments[-1]) | Select-Object -Unique
            foreach ($name in $names) {
                foreach ($directory in @($script:CacheDir, $folder)) {
                    $existing = Join-Path $directory $name
                    if ((Test-Path -LiteralPath $existing -PathType Leaf) -and
                        (Get-FileHash -LiteralPath $existing -Algorithm SHA256).Hash -ieq $SHA256) {
                        Copy-Item -LiteralPath $existing -Destination $cached -Force
                        Write-Host ('Проверен и добавлен в кэш: ' + $existing) -ForegroundColor Green
                        break
                    }
                }
                if (Test-Path -LiteralPath $cached) { break }
            }
        }
        if (-not (Test-Path -LiteralPath $cached)) {
            $partial = $cached + '.partial'
            Receive-File -Uri $Uri -OutFile $partial
            if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ine $SHA256) {
                Remove-Item -LiteralPath $partial -Force
                throw 'Контрольная сумма загруженного файла не совпала.'
            }
            Move-Item -LiteralPath $partial -Destination $cached -Force
        } else { Write-Host 'Контрольная сумма совпала — используем кэш, без скачивания.' -ForegroundColor Green }
        Copy-Item -LiteralPath $cached -Destination $OutFile -Force
    } finally { $lock.Dispose() }
}
function Test-CachedOllamaModels {
    param([string]$Models)
    $blobs = Join-Path $Models 'blobs'
    if (Test-Path -LiteralPath $blobs) {
        Get-ChildItem -LiteralPath $blobs -File | Where-Object Name -match '^sha256-[a-f0-9]{64}$' | ForEach-Object {
            Write-Host ('Проверяем SHA256 слоя Qwen: ' + $_.Name) -ForegroundColor Cyan
            if ((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash -ine $_.Name.Substring(7)) {
                Remove-Item -LiteralPath $_.FullName -Force
                Write-Host 'Повреждённый слой удалён; Ollama загрузит его снова.' -ForegroundColor Yellow
            }
        }
    }
}

[Console]::OutputEncoding = New-Object Text.UTF8Encoding($false)
$OutputEncoding = [Console]::OutputEncoding
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
 $progressState = @{
    InstallClock = [Diagnostics.Stopwatch]::StartNew()
    StageClock = [Diagnostics.Stopwatch]::StartNew()
    StageNumber = 0
    StageTitle = 'Подготовка'
    LastHeartbeat = [DateTime]::MinValue
    Finished = $false
}

function Format-Size([double]$Bytes) {
    if ($Bytes -ge 1TB) { return ('{0:N1} ТБ' -f ($Bytes / 1TB)) }
    if ($Bytes -ge 1GB) { return ('{0:N1} ГБ' -f ($Bytes / 1GB)) }
    if ($Bytes -ge 1MB) { return ('{0:N1} МБ' -f ($Bytes / 1MB)) }
    if ($Bytes -ge 1KB) { return ('{0:N1} КБ' -f ($Bytes / 1KB)) }
    return ('{0:N0} байт' -f $Bytes)
}
function Format-Time([TimeSpan]$Time) {
    return ('{0:00}:{1:00}:{2:00}' -f [Math]::Floor($Time.TotalHours), $Time.Minutes, $Time.Seconds)
}
function Show-ModelProgress($Data) {
    if (-not $Data) {
        Write-Progress -Id 3 -Activity 'Загрузка модели' -Status 'Ожидаем данные загрузчика' -PercentComplete -1
        return
    }
    $title = if ($Data.model -eq 'qwen') { 'Qwen — текущий слой' } else { 'Whisper — файлы модели' }
    if ($Data.phase -ne 'download') {
        $text = if ($Data.phase -eq 'error') { 'Ошибка загрузки — см. журнал' } elseif ($Data.phase -eq 'done') { 'Модель готова' } else { 'Файлы получены. Проверка модели' }
        Write-Progress -Id 3 -Activity $title -Status $text -PercentComplete -1
        return
    }
    $done = [double]$Data.completed; $total = [double]$Data.total
    $percent = -1
    $amount = Format-Size $done
    if ($total -gt 0) { $percent=[int][Math]::Min(100,100*$done/$total); $amount += (' / ' + (Format-Size $total) + (' ({0:N1}%)' -f (100*$done/$total))) }
    else { $amount += ' / общий объём уточняется' }
    $eta = 'уточняется'
    if ($null -ne $Data.eta_seconds) { $eta = Format-Time ([TimeSpan]::FromSeconds([double]$Data.eta_seconds)) }
    $text = $amount + ' | ' + (Format-Size ([double]$Data.bytes_per_second)) + '/с | осталось ' + $eta
    if ($Data.stale) { $text += ' | ожидаем новые данные' }
    Write-Progress -Id 3 -Activity $title -Status $text -PercentComplete $percent
    if (-not $script:LastModelMessage -or ((Get-Date)-$script:LastModelMessage).TotalSeconds -ge 30) {
        Write-Host ($title + ': ' + $text)
        $script:LastModelMessage=Get-Date
    }
}

function Show-Disks {
    Get-Volume | Where-Object DriveLetter | Sort-Object DriveLetter | ForEach-Object {
        [pscustomobject]@{
            'Диск' = [string]$_.DriveLetter + ':'
            'Имя' = $_.FileSystemLabel
            'Файловая система' = $_.FileSystem
            'Всего' = Format-Size $_.Size
            'Свободно' = Format-Size $_.SizeRemaining
        }
    } | Format-Table -AutoSize | Out-Host
    Write-Host 'Размеры рассчитаны по 1024: 1 ГБ = 1024 МБ.'
}
function Show-Stage([int]$Number, [string]$Title) {
    if ($progressState.StageNumber -ne $Number) {
        if ($progressState.StageNumber -gt 0) {
            Write-Host ('Предыдущий этап завершён за ' + (Format-Time $progressState.StageClock.Elapsed)) -ForegroundColor Green
        }
        $progressState.StageNumber = $Number
        $progressState.StageTitle = $Title
        $progressState.StageClock.Restart()
        Write-Host ''
        Write-Host ('Этап {0}/17: {1}' -f $Number, $Title) -ForegroundColor Cyan
        $progressState.LastHeartbeat = [DateTime]::MinValue
    }
    $percent = [int][Math]::Floor(($Number - 1) * 100 / 17)
    $elapsed = Format-Time $progressState.InstallClock.Elapsed
    $stageElapsed = Format-Time $progressState.StageClock.Elapsed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Status ('{0}/17: {1} | всего {2} | этап {3}' -f $Number,$Title,$elapsed,$stageElapsed) -PercentComplete $percent
    if (((Get-Date) - $progressState.LastHeartbeat).TotalSeconds -ge 30) {
        Write-Host ('Завершено вех: {0}/17. Прошло: {1}; текущий этап: {2}. Остаток времени: уточняется.' -f ($Number-1),$elapsed,$stageElapsed)
        $progressState.LastHeartbeat = Get-Date
    }
}
function Show-AppStage([string]$Status) {
    switch ($Status) {
        'INSTALLING_DEPENDENCIES' { Show-Stage 8 'Установка Docker и зависимостей приложения' }
        'BUILDING_APP' { Show-Stage 9 'Сборка образа приложения' }
        'INITIALIZING_DATABASE' { Show-Stage 10 'Подготовка базы данных и Ollama' }
        'DOWNLOADING_QWEN' { Show-Stage 11 'Загрузка языковой модели Qwen3 4B' }
        'DOWNLOADING_WHISPER' { Show-Stage 12 'Загрузка модели распознавания Whisper' }
        'CHECKING_DATABASE_AND_MODELS' { Show-Stage 13 'Проверка базы данных и моделей' }
        'STARTING_SERVICES' { Show-Stage 14 'Запуск служб приложения' }
        'READY_FOR_ADMIN' { Show-Stage 14 'Службы готовы; завершаем настройку' }
        default {
            if ($Status -like 'FAILED*') { Write-Host ('Ошибка фоновой установки: ' + $Status) -ForegroundColor Red }
            else { Show-Stage $progressState.StageNumber $progressState.StageTitle }
        }
    }
}
function Complete-Stages {
    $progressState.Finished = $true
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Status 'Все проверки пройдены' -PercentComplete 100
    Write-Host ('Завершено: 17/17 вех. Общее время: ' + (Format-Time $progressState.InstallClock.Elapsed)) -ForegroundColor Green
    Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
}

function Receive-File([string]$Uri, [string]$OutFile) {
    if ([Uri]$Uri -and ([Uri]$Uri).Scheme -ne 'https') { throw 'Загрузка разрешена только по HTTPS.' }
    $name = Split-Path $OutFile -Leaf
    Write-Host ('Загружаем: ' + $name + '. Ожидаем ответ сервера...')
    $request = [Net.HttpWebRequest]::Create($Uri)
    $request.UserAgent = 'ShtabAI-Installer-RU'
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 60000
    $response = $null
    $inputStream = $null
    $outputStream = $null
    $part = $OutFile + '.part'
    try {
        $response = $request.GetResponse()
        $total = [long]$response.ContentLength
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Create($part)
        $buffer = New-Object byte[] 1048576
        $received = [long]0
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $lastUpdate = -1.0
        $lastConsole = -15.0
        while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $read)
            $received += $read
            if ($clock.Elapsed.TotalSeconds - $lastUpdate -ge 1) {
                $seconds = [Math]::Max(0.1, $clock.Elapsed.TotalSeconds)
                $rate = $received / $seconds
                $percent = -1
                $remaining = -1
                $eta = 'вычисляется'
                $sizes = Format-Size $received
                if ($total -gt 0) {
                    $percent = [int][Math]::Min(99, [Math]::Floor($received * 100.0 / $total))
                    $sizes += ' из ' + (Format-Size $total)
                    if ($seconds -ge 5 -and $rate -gt 0) {
                        $remaining = [int][Math]::Min([int]::MaxValue, [Math]::Max(0, ($total - $received) / $rate))
                        $eta = '~' + (Format-Time ([TimeSpan]::FromSeconds($remaining)))
                    }
                }
                $statusText = '{0} | {1}/с | прошло {2} | осталось {3}' -f $sizes,(Format-Size $rate),(Format-Time $clock.Elapsed),$eta
                Show-Stage $progressState.StageNumber $progressState.StageTitle
                Write-Progress -Id 2 -ParentId 1 -Activity ('Загрузка: ' + $name) -Status $statusText -PercentComplete $percent -SecondsRemaining $remaining
                if ($clock.Elapsed.TotalSeconds - $lastConsole -ge 15) {
                    Write-Host $statusText
                    $lastConsole = $clock.Elapsed.TotalSeconds
                }
                $lastUpdate = $clock.Elapsed.TotalSeconds
            }
        }
        $outputStream.Dispose()
        $outputStream = $null
        if ($total -ge 0 -and $received -ne $total) { throw 'Сервер передал неполный файл.' }
        Move-Item -LiteralPath $part -Destination $OutFile -Force
        Write-Host ('Скачано: {0}, размер {1}, время {2}' -f $name,(Format-Size $received),(Format-Time $clock.Elapsed)) -ForegroundColor Green
    } catch {
        throw ('Не удалось скачать ' + $name + ': ' + $_.Exception.Message)
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Close() }
        if (Test-Path -LiteralPath $part) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
        Write-Progress -Id 2 -Activity ('Загрузка: ' + $name) -Completed
    }
}

function Get-EarlyInstallation([string]$DistroName, [string]$ExpectedRoot = '') {
    $registrationFile = Join-Path (Join-Path $env:LOCALAPPDATA 'ShtabAI') ($DistroName + '.json')
    if (-not (Test-Path -LiteralPath $registrationFile)) { throw 'Не найдена регистрация незавершённой установки для этого пользователя Windows.' }
    $registration = Get-Content -LiteralPath $registrationFile -Raw | ConvertFrom-Json
    if ($registration.Product -ne 'ShtabAI' -or $registration.Backend -ne 'WSL2' -or $registration.DistroName -ne $DistroName) { throw 'Регистрация не принадлежит установщику Штаб.AI.' }
    if ($registration.Root -notmatch '^[A-Za-z]:\\') { throw 'Некорректная папка в регистрации установки.' }
    $registeredRoot = [IO.Path]::GetFullPath($registration.Root).TrimEnd('\')
    if ($ExpectedRoot -and $registeredRoot -ine $ExpectedRoot) { throw 'Папка не совпадает с регистрацией незавершённой установки.' }
    $manifestFile = Join-Path $registeredRoot 'installation.json'
    if (-not (Test-Path -LiteralPath $manifestFile)) { throw 'Не найден паспорт незавершённой установки.' }
    $manifest = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
    if ($manifest.Product -ne 'ShtabAI' -or $manifest.Backend -ne 'WSL2' -or $manifest.DistroName -ne $DistroName -or $manifest.Root -ine $registeredRoot -or $manifest.Revision -notmatch '^[a-f0-9]{40}$') { throw 'Паспорт установки не совпадает с ожидаемой версией и папкой.' }
    if ($manifest.HTTPSPort -ne 8445 -or $manifest.TaskName -ne ('ShtabAI-' + $DistroName + '-Start') -or $manifest.LANRule -ne ('ShtabAI-' + $DistroName + '-LAN')) { throw 'Неожиданные настройки незавершённой установки.' }
    if ($manifest.CertificateThumbprint -or (Test-Path (Join-Path $registeredRoot 'ollama')) -or (Test-Path (Join-Path $registeredRoot 'Start-ShtabRuntime.ps1')) -or (Get-ScheduledTask -TaskName $manifest.TaskName -ErrorAction SilentlyContinue)) { throw 'Этот режим предназначен только для остановки перед запуском systemd, до установки Ollama и приложения.' }
    if (-not $manifest.BackupPath -or -not (Test-Path -LiteralPath $manifest.BackupPath)) { throw 'Не найдена зарегистрированная папка резервных копий.' }
    $distros = @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction Stop | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object DistributionName -eq $DistroName)
    if ($distros.Count -ne 1 -or $distros[0].Version -ne 2) { throw 'Не найден ожидаемый собственный дистрибутив WSL2.' }
    $basePath = $distros[0].BasePath
    if ($basePath.StartsWith('\\?\')) { $basePath = $basePath.Substring(4) }
    if ([IO.Path]::GetFullPath($basePath).TrimEnd('\') -ine (Join-Path $registeredRoot 'distro')) { throw 'Дистрибутив WSL находится вне зарегистрированной папки установки.' }
    return $manifest
}

function Remove-EarlyInstallation($Manifest) {
    $rootToRemove = [IO.Path]::GetFullPath($Manifest.Root).TrimEnd('\')
    if ((Get-Item -LiteralPath $rootToRemove -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Папка прежней установки является ссылкой. Автоматическое удаление остановлено.' }
    $entries = @(Get-ChildItem -LiteralPath $rootToRemove -Force)
    foreach ($entry in $entries) {
        if ($entry.Name -notin @('installation.json','distro') -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'В папке найдены дополнительные файлы или ссылки. Автоматическое удаление остановлено.' }
    }
    Write-Host ('Начинаем заново: удаляем только незавершённый дистрибутив ' + $Manifest.DistroName + ' из ' + $rootToRemove) -ForegroundColor Yellow
    $wslExe = Join-Path $env:SystemRoot 'System32\wsl.exe'
    if (-not [Environment]::Is64BitProcess) { $wslExe = Join-Path $env:SystemRoot 'Sysnative\wsl.exe' }
    & $wslExe --unregister $Manifest.DistroName
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось удалить незавершённый дистрибутив WSL. Новая установка не начата.' }
    Remove-Item -LiteralPath $rootToRemove -Recurse -Force
    $registrationFile = Join-Path (Join-Path $env:LOCALAPPDATA 'ShtabAI') ($Manifest.DistroName + '.json')
    Remove-Item -LiteralPath $registrationFile -Force
    Write-Host 'Незавершённая установка удалена. Папка резервных копий сохранена.' -ForegroundColor Green
}

#Requires -Version 5.1
# Compatibility snapshot: https://docs.ollama.com/gpu, checked 2026-10-09.
# The 4096 MiB free-memory threshold is our conservative installation policy,
# not an official model minimum. Actual inference is still required.
function Select-ShtabAcceleration($Cards,[string]$Requested='auto') {
    if ($Requested -eq 'cpu') { return [pscustomobject]@{Mode='cpu';Reason='Выбран процессор'} }
    foreach ($card in $Cards) {
        if ($Requested -notin @('auto','nvidia')) { continue }
        if ($card.Vendor -ne 'nvidia') { continue }
        $cc=0.0; $driver=0.0; $free=0.0
        $culture=[Globalization.CultureInfo]::InvariantCulture
        $style=[Globalization.NumberStyles]::Float
        if (-not [double]::TryParse([string]$card.CC,$style,$culture,[ref]$cc) -or -not [double]::TryParse([string]$card.Driver,$style,$culture,[ref]$driver) -or -not [double]::TryParse([string]$card.Free,$style,$culture,[ref]$free)) { continue }
        $minimumDriver=550
        if ($cc -lt 6.3) { $minimumDriver=570 }
        if ($cc -ge 5 -and $driver -ge $minimumDriver -and $free -ge 4096) {
            return [pscustomobject]@{Mode='nvidia';Reason=([string]$card.Name+'; CUDA CC '+$cc+'; free MiB '+$free)}
        }
    }
    # Exact Windows ROCm list; no family/prefix guessing, no Linux overrides.
    $amd=@('AMD Radeon RX 7900 XTX','AMD Radeon RX 7900 XT','AMD Radeon RX 7900 GRE','AMD Radeon RX 7800 XT','AMD Radeon RX 7700 XT','AMD Radeon RX 7600 XT','AMD Radeon RX 7600','AMD Radeon PRO W7900','AMD Radeon PRO W7800','AMD Radeon PRO W7700','AMD Radeon PRO W7600','AMD Radeon PRO W7500')
    foreach ($card in $Cards) {
        if ($Requested -in @('auto','amd') -and $card.Name -in $amd) {
            return [pscustomobject]@{Mode='amd';Reason=([string]$card.Name+'; ROCm требует пробного запуска; Whisper работает на CPU')}
        }
    }
    return [pscustomobject]@{Mode='cpu';Reason='Совместимая GPU с подходящим драйвером и запасом памяти не подтверждена; автоматически используем CPU'}
}
function Get-ShtabAcceleration([string]$Requested='auto') {
    $cards=@(Get-CimInstance Win32_VideoController | ForEach-Object {
        [pscustomobject]@{Name=$_.Name;Vendor='other';CC='';Driver=$_.DriverVersion;Total='';Free=''}
    })
    $smi=Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $smi) {
        $path=Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
        if (Test-Path -LiteralPath $path) { $smi=[pscustomobject]@{Source=$path} }
    }
    if ($smi) {
        $saved=$ErrorActionPreference
        try {
            $ErrorActionPreference='Continue'
            $lines=& $smi.Source --query-gpu=name,compute_cap,driver_version,memory.total,memory.free --format=csv,noheader,nounits 2>$null
            $exit=$LASTEXITCODE
        } finally { $ErrorActionPreference=$saved }
        if ($exit -eq 0) {
            foreach ($item in ($lines | ConvertFrom-Csv -Header Name,CC,Driver,Total,Free)) {
                $cards += [pscustomobject]@{Name=$item.Name.Trim();Vendor='nvidia';CC=$item.CC.Trim();Driver=$item.Driver.Trim();Total=$item.Total.Trim();Free=$item.Free.Trim()}
            }
        }
    }
    Write-Host 'Диагностика GPU: точная модель, драйвер, CUDA, видеопамять и свободная память (МБ)'
    $cards | Format-Table Name,Driver,CC,Total,Free -AutoSize | Out-Host
    $selection=Select-ShtabAcceleration $cards $Requested
    Write-Host ('Выбран режим: '+$selection.Mode+' / '+$selection.Reason)
    return $selection
}

function Install-ShtabAI {
[CmdletBinding()]
param(
    [ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName = 'ShtabAI-021',
    [ValidateSet('ask','auto','cpu','nvidia','amd')][string]$Acceleration = 'ask',
    [ValidateSet('ask','local','lan')][string]$Access = 'ask',
    [ValidateRange(1024,65535)][int]$HTTPSPort = 8445,
    [ValidatePattern('^(main|[a-f0-9]{40})$')][string]$Revision = 'main',
    [string]$InstallDir = '',
    [switch]$ResumeEarly
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Invoke-WSL {
    & $script:wsl @args
    if ($LASTEXITCODE -ne 0) { throw "Ошибка команды WSL (код $LASTEXITCODE). Данные установки сохранены." }
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
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось получить список установленных дистрибутивов WSL.' }
    return @($result | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
}
function Verify-Package([string]$Root) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'SHA256SUMS') -Encoding UTF8) {
        if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Некорректный список контрольных сумм.' }
        $expected = $Matches[1]; $relative = $Matches[2]
        if ($relative -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'В пакете обнаружен небезопасный путь.' }
        $actual = (Get-FileHash -LiteralPath (Join-Path $Root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Контрольная сумма не совпала: $relative" }
    }
}
Show-Stage 1 'Проверка компьютера и подготовка WSL'
Write-Host 'Проверяем Windows, оперативную память и процессор...' -ForegroundColor Cyan
$os = Get-CimInstance Win32_OperatingSystem
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'Требуется 64-разрядная Windows x64.' }
if ($os.ProductType -eq 1 -and [int]$os.BuildNumber -lt 19045) { throw 'Требуется Windows 10 22H2 или Windows 11.' }
if ($os.ProductType -ne 1) { throw 'Эта версия рассчитана на Windows 10/11; Windows Server не поддерживается.' }
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
if ($restart) { Write-Host 'Компоненты WSL включены. Перезагрузите Windows и снова запустите этот файл.' -ForegroundColor Yellow; return }
if (-not (Test-Path $script:wsl)) { throw 'WSL недоступен. Перезагрузите Windows и повторите установку.' }
if (-not (Test-ShtabVirtualizationReady)) { return }
Write-Host 'Проверяем среду WSL...' -ForegroundColor Cyan
if (-not (Test-WSLInstalled)) {
    Write-Host 'Устанавливаем WSL без дополнительного дистрибутива Linux...' -ForegroundColor Cyan
    Install-ShtabWSLRuntime
    Write-Host 'WSL установлен. Перезагрузите Windows и снова запустите этот файл.' -ForegroundColor Yellow
    return
}
Write-Host 'Обновляем WSL...' -ForegroundColor Cyan
Invoke-WSL --update --web-download
if ((-not $ResumeEarly) -and $DistroName -in @(Read-Distros)) { throw "Дистрибутив $DistroName уже существует. Для новой установки сначала удалите прежнюю через программу удаления." }
if (Get-NetTCPConnection -LocalPort $HTTPSPort -State Listen -ErrorAction SilentlyContinue) { throw "Порт Windows $HTTPSPort уже занят." }
if (Get-NetTCPConnection -LocalPort 18093 -State Listen -ErrorAction SilentlyContinue) { throw 'Порт Windows 18093 уже занят.' }
if (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue) { throw 'Порт Windows 11435 уже занят; он нужен Ollama.' }
$defaultRoot = Join-Path $env:LOCALAPPDATA ('ShtabAI\' + $DistroName)
if (-not $InstallDir) {
    Show-Disks
    $InstallDir = Read-Host ("Папка установки (например D:\Apps\$DistroName) [$defaultRoot]")
    if (-not $InstallDir) { $InstallDir=$defaultRoot }
}
if ($InstallDir -notmatch '^[A-Za-z]:\\' -or $InstallDir -match '["\r\n]') { throw 'Укажите полный путь на локальном диске, например D:\Apps\ShtabAI-021.' }
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
if ($ResumeEarly) { $resumeManifest = Get-EarlyInstallation $DistroName $root }
$driveLetter = [IO.Path]::GetPathRoot($root).Substring(0,1)
$volume = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
if ($volume.FileSystem -ne 'NTFS' -or $volume.DriveType -ne 'Fixed') { throw 'Для WSL и моделей нужен локальный диск NTFS.' }
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
if ((-not $ResumeEarly) -and (Test-Path $indexPath)) { throw 'Найдена регистрация прежней установки. Сначала используйте программу удаления.' }
if ((Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0,1))).Free -lt 42949672960) { throw 'На диске установки требуется не менее 40 ГБ свободного места (42,9 млрд байт).' }
if ((-not $ResumeEarly) -and (Test-Path $root)) { throw "Папка уже существует: $root. Сначала используйте программу удаления." }
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = Join-Path $desktop ($DistroName + '.url')
if (Test-Path $shortcut) { throw 'Ярлык уже существует. Сначала удалите прежнюю установку.' }
$wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*localhostForwarding\s*=\s*false\s*$') {
    throw 'В .wslconfig отключён localhostForwarding. Включите его перед установкой.'
}
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*networkingMode\s*=\s*(mirrored|virtioproxy|none)\s*$') {
    throw 'Нужен режим WSL NAT. Существующий .wslconfig сохранён; настройте NAT перед установкой.'
}
Write-Host 'Обнаружены видеокарты:'
Get-CimInstance Win32_VideoController | Select-Object @{Name='Видеокарта';Expression={$_.Name}},@{Name='Драйвер';Expression={$_.DriverVersion}} | Format-Table -AutoSize | Out-Host
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
    if ($answer -in @('','2')) { $Access='lan' } elseif ($answer -eq '1') { $Access='local' } else { throw 'Некорректный выбор доступа.' }
}
$lanAddress = ''
if ($Access -eq 'lan') {
    $adapters = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address } | ForEach-Object {
        [pscustomobject]@{Interface=$_.InterfaceAlias; IP=$_.IPv4Address[0].IPAddress; Index=$_.InterfaceIndex; Guid=[string]$_.NetAdapter.InterfaceGuid}
    })
    if (-not $adapters.Count) { throw 'Не найден активный сетевой интерфейс со шлюзом IPv4. Выберите локальный доступ.' }
    for ($i=0; $i -lt $adapters.Count; $i++) { Write-Host "$($i+1) - $($adapters[$i].Interface) / $($adapters[$i].IP)" }
    $selected = Read-Host 'Выберите сетевой интерфейс [1]'
    if (-not $selected) { $selected='1' }
    $number=0
    if (-not [int]::TryParse($selected,[ref]$number) -or $number -lt 1 -or $number -gt $adapters.Count) { throw 'Некорректный сетевой интерфейс.' }
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
            if ($entry.FullName -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'В архиве обнаружен небезопасный путь.' }
        }
    } finally { $zip.Dispose() }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $work 'source')
    $roots = @(Get-ChildItem (Join-Path $work 'source') -Directory)
    if ($roots.Count -ne 1) { throw 'Неожиданная структура архива приложения.' }
    $package = $roots[0].FullName
    Verify-Package $package
    if ($ResumeEarly) {
        Write-Host 'Продолжаем собственную незавершённую установку. Ubuntu уже создана и повторно не скачивается.' -ForegroundColor Cyan
        $manifest = [ordered]@{}
        foreach ($property in $resumeManifest.PSObject.Properties) { $manifest[$property.Name] = $property.Value }
        $manifestPath = Join-Path $root 'installation.json'
        $backupPath = $manifest.BackupPath
        $manifest.Acceleration = $Acceleration
        $manifest.Network = ($Access -eq 'lan')
        $manifest.LANAddress = $lanAddress
        $manifest.LANInterfaceGuid = if ($Access -eq 'lan') { $adapters[$number-1].Guid } else { '' }
        Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        Show-Stage 4 'Исправление конфигурации и запуск созданной Ubuntu'
    } else {
        Show-Stage 3 'Загрузка и проверка Ubuntu'
        $image = Join-Path $work 'ubuntu.wsl'
        $imageName = 'ubuntu-24.04.5-wsl-amd64.wsl'
        $imageBase = 'https://releases.ubuntu.com/24.04/'
        $sums = Join-Path $work 'Ubuntu-SHA256SUMS'
        Receive-File -Uri ($imageBase + 'SHA256SUMS') -OutFile $sums
        $text = [IO.File]::ReadAllText($sums,[Text.Encoding]::UTF8)
        $matches = [regex]::Matches($text,('(?im)^([a-f0-9]{64})[ \t]+\*?' + [regex]::Escape($imageName) + '[ \t]*\r?$'))
        if ($matches.Count -ne 1) { throw 'Не найдена контрольная сумма образа Ubuntu WSL.' }
        Write-Host 'Скачиваем образ Ubuntu 24.04 для WSL...'
        Receive-CachedFile -Uri ($imageBase + $imageName) -OutFile $image -SHA256 $matches[0].Groups[1].Value
        if ((Get-FileHash $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne $matches[0].Groups[1].Value.ToLowerInvariant()) { throw 'Контрольная сумма образа Ubuntu не совпала.' }
        Show-Stage 4 'Создание Linux-среды и проверка видеокарты'
        New-Item -ItemType Directory $root -Force | Out-Null
        $manifest = [ordered]@{ Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root; Revision=$Revision; HTTPSPort=$HTTPSPort; Shortcut=$shortcut; TaskName=('ShtabAI-' + $DistroName + '-Start'); CertificateThumbprint=''; WSLConfigCreated=$false; WSLConfigText=''; Acceleration=$Acceleration; Network=($Access -eq 'lan'); LANAddress=$lanAddress; LANInterfaceGuid=$(if ($Access -eq 'lan') { $adapters[$number-1].Guid } else { '' }); LANRule=('ShtabAI-' + $DistroName + '-LAN') }
        $manifestPath = Join-Path $root 'installation.json'
        Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        New-Item -ItemType Directory (Split-Path $indexPath) -Force | Out-Null
        Write-UTF8 $indexPath (@{Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root} | ConvertTo-Json)
        $backupPath = if ($root -eq $defaultRoot) { Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ShtabAI-Backups' } else { $root+'-Backups' }
        New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
        $manifest.Add('BackupPath',$backupPath)
        Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    }
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
    if (-not $ResumeEarly) { Invoke-WSL --import $DistroName (Join-Path $root 'distro') $image --version 2 }
    # Access the imported root filesystem through WSL's supported file share.
    # Write actual LF bytes; no Bash printf and no native quoting layers.
    $configPath = "\\wsl.localhost\$DistroName\etc\wsl.conf"
    $configText = "[boot]`nsystemd=true`n"
    [IO.File]::WriteAllText($configPath, $configText, (New-Object Text.UTF8Encoding($false)))
    if ([IO.File]::ReadAllText($configPath) -cne $configText) { throw 'Проверка записанного wsl.conf не пройдена.' }
    Write-Host 'Конфигурация WSL записана и проверена: systemd=true.' -ForegroundColor Green
    Invoke-WSL --terminate $DistroName
    $deadline = (Get-Date).AddMinutes(2)
    do {
        $pidOne = (Invoke-Guest /bin/cat /proc/1/comm | Out-String).Trim()
        if ($pidOne -eq 'systemd') { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($pidOne -ne 'systemd') { throw 'Служба systemd в WSL не запустилась. Данные сохранены; для повторной попытки используйте этот файл с параметром -Resume.' }
    if ($Acceleration -eq 'nvidia') {
        & $script:wsl --distribution $DistroName --user root --exec /usr/lib/wsl/lib/nvidia-smi -L
        if ($LASTEXITCODE -ne 0) {
            Write-Host 'NVIDIA недоступна в WSL. Автоматически продолжаем на CPU.' -ForegroundColor Yellow
            $Acceleration = 'cpu'; $manifest.Acceleration = 'cpu'
            Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        }
    }
    Show-Stage 5 'Установка Ollama и библиотек видеокарт'
    Write-Host 'Скачиваем Ollama для Windows и библиотеки видеокарт...'
    $ollamaZip = Join-Path $work 'ollama.zip'
    Receive-CachedFile -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64.zip' -OutFile $ollamaZip -SHA256 '428c94622a04764b318ddf13a061898edf69e32ffa896f638ed6015fd3f33288'
    if ((Get-FileHash $ollamaZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '428c94622a04764b318ddf13a061898edf69e32ffa896f638ed6015fd3f33288') { throw 'Контрольная сумма Ollama не совпала.' }
    $ollamaDir = Join-Path $root 'ollama'
    Expand-Archive -LiteralPath $ollamaZip -DestinationPath $ollamaDir
    if ($Acceleration -eq 'amd') {
        $rocmZip = Join-Path $work 'ollama-rocm.zip'
        Receive-CachedFile -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64-rocm.zip' -OutFile $rocmZip -SHA256 'a290510b3ee3b743de54eb3fbae99b69f19a49485f42ce6bcf4a1a6f86e4ba01'
        if ((Get-FileHash $rocmZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'a290510b3ee3b743de54eb3fbae99b69f19a49485f42ce6bcf4a1a6f86e4ba01') { throw 'Контрольная сумма библиотек AMD не совпала.' }
        Expand-Archive -LiteralPath $rocmZip -DestinationPath $ollamaDir -Force
    }
    Show-Stage 6 'Настройка автозапуска, сети и запуск Ollama'
    Copy-Item -LiteralPath (Join-Path $package 'windows\Start-ShtabRuntime.ps1') -Destination $root
    Copy-Item -LiteralPath (Join-Path $package 'windows\Manage-ShtabAI.ps1') -Destination $root
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
    if ($manifest.Network) {
        New-NetFirewallRule -Name $manifest.LANRule -DisplayName ('ShtabAI LAN ' + $DistroName) -Group 'ShtabAI' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $lanAddress -LocalPort $HTTPSPort -RemoteAddress LocalSubnet -Profile @('Private','Domain') | Out-Null
    }
    Register-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' -Action $action -Trigger $triggers -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\'
    $deadline=(Get-Date).AddMinutes(3)
    $stateFile=Join-Path $root 'runtime-state.json'
    while (-not (Test-Path $stateFile)) {
        if ((Get-Date) -gt $deadline) { throw 'Истекло время запуска Ollama. Проверьте задачу запуска и ollama-error.log.' }
        Start-Sleep -Seconds 3
    }
    $runtimeState=Get-Content $stateFile -Raw | ConvertFrom-Json
    $guestMode = if ($Acceleration -eq 'nvidia') { 'nvidia' } else { 'cpu' }
    $lanAddress = [string]$runtimeState.LANAddress
    $httpsHost = if ($lanAddress) { $lanAddress } else { 'localhost' }
    $guestArchive = (Invoke-Guest wslpath -u $archive | Out-String).Trim()
    $guestCache = (Invoke-Guest wslpath -u $script:CacheDir | Out-String).Trim()
    $setup = 'set -euo pipefail; apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y unzip python3 curl ca-certificates openssl; work=$(mktemp -d); trap ''rm -rf "$work"'' EXIT; unzip -q ' + (Quote-Shell $guestArchive) + ' -d "$work"; cd "$work"/*; sha256sum --quiet -c SHA256SUMS; SHTAB_EXTERNAL_OLLAMA_ENDPOINT=' + (Quote-Shell $runtimeState.Endpoint) + ' SHTAB_MODEL_CACHE=' + (Quote-Shell $guestCache) + ' SHTAB_WINDOWS_ACCELERATION=' + $Acceleration + ' SHTAB_HTTPS_PORT=' + $HTTPSPort + ' bash install.sh ' + $httpsHost + ' ' + $guestMode
    Show-Stage 7 'Подготовка пакетов Ubuntu и запуск установки приложения'
    Invoke-Guest /bin/bash -c $setup
    $guestBackups = (Invoke-Guest wslpath -u $backupPath | Out-String).Trim()
    Invoke-Guest /bin/bash -c ('printf ''%s\n'' ' + (Quote-Shell $guestBackups) + ' > /opt/shtab-ai-021/backup-directory')
    $started = Get-Date
    $deadline = $started.AddHours(3)
    $lastStatus = ''
    do {
        $snapshot = (Invoke-Guest /usr/bin/python3 /opt/shtab-ai-021/scripts/install-progress.py --json | Out-String) | ConvertFrom-Json
        $status = [string]$snapshot.status
        $elapsed = ((Get-Date) - $started).ToString('hh\:mm\:ss')
        Show-AppStage $status
        if ($status -in @('DOWNLOADING_QWEN','DOWNLOADING_WHISPER')) { Show-ModelProgress $snapshot.progress } else { Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed }
        $lastStatus=$status
        if ($status -eq 'READY_FOR_ADMIN') { break }
        if ($status -like 'FAILED*' -or (Get-Date) -gt $deadline) {
            Invoke-Guest /bin/journalctl -u shtab-ai-install -n 80 --no-pager
            throw "Установка не завершилась: $status. Для установки с нуля используйте программу удаления."
        }
        Start-Sleep -Seconds 10
    } while ($true)
    Show-Stage 15 'Настройка сертификата HTTPS'
    Invoke-Guest /opt/shtab-ai-021/shtabctl certificate
    $cert = Join-Path $root 'shtab-ai-root.crt'
    $guestCert = (Invoke-Guest wslpath -u $cert | Out-String).Trim()
    Invoke-Guest /bin/cp /opt/shtab-ai-021/shtab-ai-root.crt $guestCert
    $imported = Import-Certificate -FilePath $cert -CertStoreLocation Cert:\CurrentUser\Root
    $manifest.CertificateThumbprint = $imported.Thumbprint
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    Show-Stage 16 'Создание администратора'
    Write-Host 'Создайте первого администратора (пароль вводится скрыто):'
    Invoke-Guest /opt/shtab-ai-021/shtabctl bootstrap
    $url = "https://localhost:$HTTPSPort/login"
    Write-UTF8 $shortcut ("[InternetShortcut]`nURL=$url`n")
    $shell=New-Object -ComObject WScript.Shell
    $managerLink=$shell.CreateShortcut((Join-Path $desktop ($DistroName+'-Manager.lnk')))
    $managerLink.TargetPath=$powershell
    $managerLink.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $root 'Manage-ShtabAI.ps1')+'" -ManifestPath "'+$manifestPath+'"'
    $managerLink.WorkingDirectory=$root
    $managerLink.Description='Штаб.AI: резервные копии, восстановление и управление службами'
    $managerLink.Save()
    # Check the Windows-to-WSL path with normal certificate validation.
    Show-Stage 17 'Проверка страницы входа и сетевого доступа'
    $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15
    if ($response.StatusCode -ne 200) { throw 'Проверка страницы входа из Windows не пройдена.' }
    $qwenActual=(Invoke-Guest /bin/cat /opt/shtab-ai-021/qwen-compute.json | Out-String) | ConvertFrom-Json
    $asrActual=(Invoke-Guest /bin/cat /opt/shtab-ai-021/download-progress/asr-compute.json | Out-String) | ConvertFrom-Json
    Write-Host ('Проверенный режим Qwen: '+$qwenActual.device+'; Whisper: '+$asrActual.device) -ForegroundColor Green
    Write-Host "Штаб.AI готов: $url | выбранный предварительно режим: $Acceleration" -ForegroundColor Green
    $currentManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $lanAddress = [string]$currentManifest.LANAddress
    if ($manifest.Network -and $lanAddress) {
        $networkURL = "https://${lanAddress}:$HTTPSPort/login"
        $networkResponse = Invoke-WebRequest -UseBasicParsing -Uri $networkURL -TimeoutSec 15
        if ($networkResponse.StatusCode -ne 200) { throw 'Проверка сетевого адреса не пройдена.' }
        Write-Host "Адрес в сети: $networkURL. На других компьютерах добавьте в доверенные публичный сертификат: $cert"
        Write-Host 'Проверьте вход и загрузку записи с другого компьютера: локальная проверка не проверяет его браузер и сетевую защиту.'
    }
    Complete-Stages
    Start-Process $url
} finally {
    Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

}

$log = Join-Path $PSScriptRoot ('ShtabAI-install-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log')
$exitCode = 0
$transcribing = $false
try {
    Start-Transcript -Path $log -Append
    $transcribing = $true
    Write-Host 'Штаб.AI — установка для Windows 10/11 x64 через WSL2.' -ForegroundColor Cyan
    if ($Resume -and $RestartEarly) { throw 'Выберите только один режим: продолжить или начать заново.' }
    $freshDefault = ''
    if ($RestartEarly) {
        $existing = Get-EarlyInstallation 'ShtabAI-021'
        $freshDefault = $existing.Root
        Remove-EarlyInstallation $existing
    }
    Write-Host 'Выберите новую папку на локальном диске NTFS. Нужны минимум 40 ГБ свободного места.'
    Write-Host '17 крупных вех имеют разную длительность. Процент показывает завершённые вехи.'
    Write-Host 'Время загрузки файлов оценивается по фактической скорости; общий срок пока неизвестен.'
    Show-Disks
    $default = Join-Path $env:LOCALAPPDATA 'ShtabAI\ShtabAI-021'
    if ($freshDefault) { $default = $freshDefault }
    if ($Resume) {
        $existing = Get-EarlyInstallation 'ShtabAI-021'
        $installDir = $existing.Root
        Write-Host ('Продолжение установки в папке: ' + $installDir) -ForegroundColor Cyan
    } else {
        $installDir = Read-Host "Папка установки [$default]"
        if (-not $installDir) { $installDir = $default }
    }
    if ($installDir -notmatch '^[A-Za-z]:\\' -or $installDir -match '["\r\n]') { throw 'Укажите полный путь на локальном диске, без кавычек.' }
    $installDir = [IO.Path]::GetFullPath($installDir).TrimEnd('\')
    if ($installDir.Length -lt 4) { throw 'Выберите папку приложения, а не корень диска.' }
    if ((-not $Resume) -and (Test-Path -LiteralPath $installDir)) { throw 'Папка уже существует. Выберите новую папку; существующие данные не удаляются.' }
    $volume = Get-Volume -DriveLetter $installDir.Substring(0,1)
    if ($volume.FileSystem -ne 'NTFS' -or $volume.DriveType -ne 'Fixed') { throw 'Нужен локальный диск NTFS.' }
    if ($volume.SizeRemaining -lt 40GB) { throw 'На выбранном диске требуется не менее 40 ГБ свободного места.' }
    Write-Host ('Папка установки: ' + $installDir)
    Write-Host 'Если потребуется перезагрузка Windows, после неё снова запустите этот файл.'
    Install-ShtabAI -Revision 'main' -InstallDir $installDir -Acceleration ask -Access ask -ResumeEarly:$Resume
    if (-not $progressState.Finished) {
        Write-Host 'Установка приложения ещё не завершена. Выполните указания выше и повторите запуск после перезагрузки.' -ForegroundColor Yellow
    }
} catch {
    Write-Host ('Установка остановлена: ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage
    $exitCode = 1
} finally {
    Write-Progress -Id 3 -Activity 'Загрузка модели' -Completed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
    if ($transcribing) { Stop-Transcript }
    Write-Host ('Журнал установки: ' + $log)
}
exit $exitCode

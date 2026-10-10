$ProgressPreference = 'Continue'
$progressState = @{
    InstallClock = [Diagnostics.Stopwatch]::StartNew()
    StageClock = [Diagnostics.Stopwatch]::StartNew()
    StageNumber = 0
    StageTitle = 'Подготовка'
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
        Write-Progress -Id 3 -ParentId 1 -Activity 'Загрузка модели' -Status 'Ожидаем данные загрузчика' -PercentComplete -1
        return
    }
    $title = if ($Data.model -eq 'qwen') { 'Qwen — текущий слой' } else { 'Whisper — файлы модели' }
    if ($Data.phase -ne 'download') {
        $text = if ($Data.phase -eq 'error') { 'Ошибка загрузки — см. журнал' } elseif ($Data.phase -eq 'done') { 'Модель готова' } else { 'Файлы получены. Проверка модели' }
        Write-Progress -Id 3 -ParentId 1 -Activity $title -Status $text -PercentComplete -1
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
    Write-Progress -Id 3 -ParentId 1 -Activity $title -Status $text -PercentComplete $percent

}

function Show-OperationProgress($Data) {
    if (-not $Data) {
        Write-Progress -Id 3 -ParentId 1 -Activity 'Текущая операция' -Completed
        return
    }
    Write-Progress -Id 3 -ParentId 1 -Activity ([string]$Data.title) -Status ([string]$Data.text) -PercentComplete ([int]$Data.percent)
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
        $progressState.StageNumber = $Number
        $progressState.StageTitle = $Title
        $progressState.StageClock.Restart()

    }
    $percent = [int][Math]::Floor(($Number - 1) * 100 / 17)
    $elapsed = Format-Time $progressState.InstallClock.Elapsed
    $stageElapsed = Format-Time $progressState.StageClock.Elapsed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Status ('{0}/17: {1} | всего {2} | этап {3}' -f $Number,$Title,$elapsed,$stageElapsed) -PercentComplete $percent

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
    Write-Progress -Id 3 -ParentId 1 -Activity 'Загрузка модели' -Completed
    Write-Progress -Id 1 -Activity 'Установка Штаб.AI' -Completed
}

function New-ShtabDownloadRequest([string]$Uri) {
    return [Net.HttpWebRequest]::Create($Uri)
}

function Receive-File([string]$Uri, [string]$OutFile, [switch]$Resume) {
    if ([Uri]$Uri -and ([Uri]$Uri).Scheme -ne 'https') { throw 'Загрузка разрешена только по HTTPS.' }
    $name = Split-Path $OutFile -Leaf
    Write-Host ('Загружаем: ' + $name + '. Ожидаем ответ сервера...')
    $request = New-ShtabDownloadRequest $Uri
    $request.UserAgent = 'ShtabAI-Installer-RU'
    $request.Timeout = 60000
    $request.ReadWriteTimeout = 60000
    $response = $null
    $inputStream = $null
    $outputStream = $null
    $part = $OutFile + '.part'
    $offset = [long]0
    if ($Resume -and (Test-Path -LiteralPath $part)) {
        $offset = (Get-Item -LiteralPath $part).Length
        if ($offset -gt 0) {
            Write-Host ('Докачка: ' + $name + ', сохранено ' + (Format-Size $offset)) -ForegroundColor Cyan
            $request.AddRange($offset)
        }
    }
    try {
        try { $response = $request.GetResponse() } catch [Net.WebException] {
            # The partial may already be complete, or this server rejects Range.
            if ($offset -le 0 -or -not $_.Exception.Response -or [int]$_.Exception.Response.StatusCode -ne 416) { throw }
            $_.Exception.Response.Close()
            $offset = 0
            $request = New-ShtabDownloadRequest $Uri
            $request.UserAgent = 'ShtabAI-Installer-RU'
            $request.Timeout = 60000
            $request.ReadWriteTimeout = 60000
            $response = $request.GetResponse()
        }
        if ([int]$response.StatusCode -eq 206) {
            if ($offset -le 0 -or $response.Headers['Content-Range'] -notmatch ('^bytes ' + $offset + '-[0-9]+/[0-9]+$')) { throw 'Некорректный ответ сервера на докачку.' }
        } else { $offset = 0 }
        $total = [long]$response.ContentLength
        if ($total -ge 0) { $total += $offset }
        $inputStream = $response.GetResponseStream()
        $outputStream = [IO.File]::Open($part, $(if ($offset -gt 0) { 'Append' } else { 'Create' }), 'Write', 'None')
        $buffer = New-Object byte[] 1048576
        $received = $offset
        $clock = [Diagnostics.Stopwatch]::StartNew()
        $lastUpdate = -1.0
        while (($read = $inputStream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $outputStream.Write($buffer, 0, $read)
            $received += $read
            if ($clock.Elapsed.TotalSeconds - $lastUpdate -ge 1) {
                $seconds = [Math]::Max(0.1, $clock.Elapsed.TotalSeconds)
                $rate = ($received - $offset) / $seconds
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
                if ($percent -ge 0) { $statusText = ('{0}% | ' -f $percent) + $statusText }
                Show-Stage $progressState.StageNumber $progressState.StageTitle
                Write-Progress -Id 2 -ParentId 1 -Activity ('Загрузка: ' + $name) -Status $statusText -PercentComplete $percent -SecondsRemaining $remaining
                $lastUpdate = $clock.Elapsed.TotalSeconds
            }
        }
        $outputStream.Dispose()
        $outputStream = $null
        if ($total -ge 0 -and $received -ne $total) { throw 'Сервер передал неполный файл.' }
        Move-Item -LiteralPath $part -Destination $OutFile -Force
        Write-Host ('Скачано 100%: {0}, размер {1}, время {2}' -f $name,(Format-Size $received),(Format-Time $clock.Elapsed)) -ForegroundColor Green
    } catch {
        throw ('Не удалось скачать ' + $name + ': ' + $_.Exception.Message)
    } finally {
        if ($outputStream) { $outputStream.Dispose() }
        if ($inputStream) { $inputStream.Dispose() }
        if ($response) { $response.Close() }
        if (-not $Resume -and (Test-Path -LiteralPath $part)) { Remove-Item -LiteralPath $part -Force -ErrorAction SilentlyContinue }
        Write-Progress -Id 2 -Activity ('Загрузка: ' + $name) -Completed
    }
}


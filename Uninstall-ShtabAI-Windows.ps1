#Requires -Version 5.1
[CmdletBinding()]
param([ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName,[switch]$ListOnly,[switch]$KeepWindowOpen)
$ErrorActionPreference='Stop'
function Remove-ShtabShortcuts($Manifest,[string]$Root) {
    $distro = [string]$Manifest.DistroName
    $folders = @([Environment]::GetFolderPath('Desktop'), [Environment]::GetFolderPath('CommonDesktopDirectory'))
    $saved = [string]$Manifest.Shortcut
    if ($saved) {
        if (-not [IO.Path]::IsPathRooted($saved) -or (Split-Path $saved -Leaf) -ine ($distro + '.url')) {
            throw 'Неожиданный путь ярлыка в описании установки.'
        }
        $folders += Split-Path $saved -Parent
    }
    $shell = $null
    foreach ($folder in @($folders | Where-Object { $_ } | Select-Object -Unique)) {
        $urlPath = Join-Path $folder ($distro + '.url')
        if (Test-Path -LiteralPath $urlPath) {
            $text = [IO.File]::ReadAllText($urlPath)
            $match = [regex]::Match($text, '(?im)^URL=(.+?)\s*$')
            $uri = $null
            $hosts = @('localhost', '127.0.0.1', [string]$Manifest.LANAddress)
            if ($match.Success -and [Uri]::TryCreate($match.Groups[1].Value.Trim(), [UriKind]::Absolute, [ref]$uri) -and
                $uri.Scheme -eq 'https' -and $uri.Port -eq [int]$Manifest.HTTPSPort -and
                $uri.AbsolutePath -eq '/login' -and $uri.Host -in $hosts) {
                Remove-Item -LiteralPath $urlPath -Force -ErrorAction Stop
                Write-Host ('Удалён ярлык: ' + $urlPath)
            } else { Write-Warning ('Адрес ярлыка изменён; ярлык сохранён: ' + $urlPath) }
        }
        $managerPath = Join-Path $folder ($distro + '-Manager.lnk')
        if (Test-Path -LiteralPath $managerPath) {
            if (-not $shell) { $shell = New-Object -ComObject WScript.Shell }
            $link = $shell.CreateShortcut($managerPath)
            $expected = '-File "' + (Join-Path $Root 'Manage-ShtabAI.ps1') + '"'
            if ($link.Arguments.IndexOf($expected, [StringComparison]::OrdinalIgnoreCase) -ge 0) {
                Remove-Item -LiteralPath $managerPath -Force -ErrorAction Stop
                Write-Host ('Удалён ярлык: ' + $managerPath)
            } else { Write-Warning ('Адрес ярлыка диспетчера изменён; ярлык сохранён: ' + $managerPath) }
        }
    }
}

try {
    $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
    $principal=New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        $powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -KeepWindowOpen'
        if ($DistroName) { $arguments+=' -DistroName '+$DistroName }
        if ($ListOnly) { $arguments+=' -ListOnly' }
        $process=Start-Process $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    $base=Join-Path $env:LOCALAPPDATA 'ShtabAI'
    $candidates=@()
    if (Test-Path $base) {
        foreach ($folder in Get-ChildItem $base -Directory) {
            $file=Join-Path $folder.FullName 'installation.json'
            if ((Test-Path $file) -and $folder.Name -match '^ShtabAI-[A-Za-z0-9-]+$') {
                $record=Get-Content $file -Raw | ConvertFrom-Json
                if ($record.Product -eq 'ShtabAI' -and $record.Backend -eq 'WSL2' -and $record.DistroName -eq $folder.Name) {
                    $candidates+=[pscustomobject]@{Number=$candidates.Count+1; Name=$folder.Name; Root=$folder.FullName; Acceleration=$record.Acceleration}
                }
            }
        }
        foreach ($index in Get-ChildItem $base -File -Filter 'ShtabAI-*.json') {
            $record=Get-Content $index.FullName -Raw | ConvertFrom-Json
            if ($record.Product -eq 'ShtabAI' -and $record.Backend -eq 'WSL2' -and $record.DistroName -match '^ShtabAI-[A-Za-z0-9-]+$' -and $index.BaseName -eq $record.DistroName) {
                $file=Join-Path $record.Root 'installation.json'
                if (-not (Test-Path $file)) { throw ('Установка недоступна: '+$record.Root+'. Подключите диск установки и повторите попытку.') }
                $actual=Get-Content $file -Raw | ConvertFrom-Json
                if ($actual.Product -ne 'ShtabAI' -or $actual.Backend -ne 'WSL2' -or $actual.DistroName -ne $record.DistroName -or $actual.Root -ne $record.Root) { throw 'Регистрация установки не соответствует её описанию.' }
                if (-not @($candidates | Where-Object Root -eq $record.Root).Count) {
                    $candidates+=[pscustomobject]@{Number=$candidates.Count+1; Name=$actual.DistroName; Root=$actual.Root; Acceleration=$actual.Acceleration}
                }
            }
        }
    }
    if (-not $candidates.Count) { Write-Host 'Установки Штаб.AI в WSL и папки незавершённых установок не найдены.'; return }
    $candidates | Format-Table -Property @('Number','Name','Acceleration','Root') -AutoSize | Out-Host
    if ($ListOnly) { return }
    if (-not $DistroName) {
        $choice=Read-Host 'Выберите номер установки или 0 для отмены'
        $number=0
        if (-not [int]::TryParse($choice,[ref]$number) -or $number -lt 1 -or $number -gt $candidates.Count) { Write-Host 'Отменено.'; return }
        $DistroName=$candidates[$number-1].Name
    }
    $selected=@($candidates | Where-Object Name -eq $DistroName)
    if ($selected.Count -ne 1) { throw 'Установка Штаб.AI не найдена.' }
    $root=$selected[0].Root
    $manifestPath=Join-Path $root 'installation.json'
    $manifest=Get-Content $manifestPath -Raw | ConvertFrom-Json
    if ($root -notmatch '^[A-Za-z]:\\' -or [IO.Path]::GetFullPath($root).TrimEnd('\').Length -lt 4) { throw 'Недопустимая папка установки.' }
    if ([IO.Path]::GetFullPath($manifest.Root).TrimEnd('\') -ne [IO.Path]::GetFullPath($root).TrimEnd('\')) { throw 'Папка не соответствует описанию установки. Ничего не удалено.' }
    if ((Get-Item $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Папка установки является ссылкой. Ничего не удалено.' }
    if (@(Get-ChildItem $root -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Папка установки содержит ссылки. Ничего не удалено.' }
    $registered=@(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object DistributionName -eq $DistroName)
    if ($registered.Count -gt 1) { throw 'Обнаружено несколько совпадающих регистраций WSL.' }
    if ($registered.Count -eq 1) {
        $actual=([string]$registered[0].BasePath).Replace('\\?\','').TrimEnd('\')
        $expected=(Join-Path $root 'distro').TrimEnd('\')
        if ([IO.Path]::GetFullPath($actual) -ne [IO.Path]::GetFullPath($expected)) { throw 'Дистрибутив WSL использует другую папку. Ничего не удалено.' }
    }
    $taskName='ShtabAI-'+$DistroName+'-Start'
    if ($manifest.TaskName -ne $taskName) { throw 'Задача автозапуска не соответствует установке.' }
    $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task -and ($task.Actions.Arguments -notlike ('*'+(Join-Path $root 'Start-ShtabRuntime.ps1')+'*'))) { throw 'Неожиданная команда задачи автозапуска. Ничего не удалено.' }
    Write-Host 'Будут удалены выбранный дистрибутив WSL, записи, пользователи, Ollama, ярлыки и сетевые настройки этой установки. Модели внутри папки приложения удаляются; отдельный постоянный кэш сохраняется. Новая резервная копия не создаётся.' -ForegroundColor Yellow
    $answer=Read-Host ('Для подтверждения введите DELETE '+$DistroName+'')
    if ($answer -cne ('DELETE '+$DistroName)) { Write-Host 'Отменено.'; return }
    if ($task) {
        Disable-ScheduledTask -InputObject $task | Out-Null
        Stop-ScheduledTask -InputObject $task -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -InputObject $task -Confirm:$false
    }
    # Stop only executables inside this installation, including native Ollama runners.
    $prefix=(Join-Path $root 'ollama')+'\'
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
    $wsl=Join-Path $env:SystemRoot 'System32\wsl.exe'
    if ($registered.Count -eq 1) {
        & $wsl --terminate $DistroName
        & $wsl --unregister $DistroName
        if ($LASTEXITCODE -ne 0) { throw 'Не удалось удалить дистрибутив WSL. Файлы сохранены.' }
    }
    foreach ($name in @(($taskName+'-Ollama'),('ShtabAI-'+$DistroName+'-LAN'))) {
        $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($rule -and $rule.Group -eq 'ShtabAI') { Remove-NetFirewallRule -Name $name }
    }
    if ($manifest.Network) {
        & netsh.exe interface portproxy delete v4tov4 listenaddress=$($manifest.LANAddress) listenport=$($manifest.HTTPSPort) | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host 'Правило сетевой переадресации не найдено или очистка не завершена; проверьте netsh interface portproxy show v4tov4.' -ForegroundColor Yellow }
    }
    Remove-ShtabShortcuts $manifest $root
    if ($manifest.CertificateThumbprint -match '^[A-Fa-f0-9]{40}$') {
        $certificate='Cert:\CurrentUser\Root\'+$manifest.CertificateThumbprint
        if (Test-Path $certificate) { Remove-Item $certificate -Force }
    }
    $wslConfig=Join-Path $env:USERPROFILE '.wslconfig'
    $otherInstallations=@($candidates | Where-Object Name -ne $DistroName)
    if ($manifest.WSLConfigCreated -and -not $otherInstallations.Count -and (Test-Path $wslConfig)) {
        if ([IO.File]::ReadAllText($wslConfig) -ceq $manifest.WSLConfigText) { Remove-Item $wslConfig -Force }
    }
    Remove-Item -LiteralPath $root -Recurse -Force
    $indexPath=Join-Path $base ($DistroName+'.json')
    if (Test-Path $indexPath) {
        $index=Get-Content $indexPath -Raw | ConvertFrom-Json
        if ($index.Product -eq 'ShtabAI' -and $index.Root -eq $root) { Remove-Item $indexPath -Force }
    }
    Write-Host 'Штаб.AI удалена. Windows, WSL, драйверы видеокарт и другие дистрибутивы сохранены.' -ForegroundColor Green
} catch {
    Write-Host ('Удаление остановлено: '+$_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($KeepWindowOpen -and $principal -and $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        [void](Read-Host 'Нажмите Enter, чтобы закрыть окно удаления')
    }
}

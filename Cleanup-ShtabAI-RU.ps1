#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param([string]$InstallDir='', [switch]$KeepWSL, [switch]$RemoveBackups)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=New-Object Text.UTF8Encoding($false)
$OutputEncoding=[Console]::OutputEncoding
function Assert-ShtabRoot([string]$Path) {
    if ($Path -notmatch '^[A-Za-z]:\\' -or $Path -match '["\r\n]') { throw 'Нужен абсолютный путь на локальном диске.' }
    $full=[IO.Path]::GetFullPath($Path).TrimEnd('\')
    if ($full.Length -lt 4) { throw 'Корень диска удалять нельзя.' }
    $forbidden=@($env:SystemRoot,$env:ProgramFiles,${env:ProgramFiles(x86)},$env:USERPROFILE,$env:PUBLIC,$env:ProgramData,$env:TEMP)
    foreach ($item in $forbidden) {
        if ($item -and $full -ieq [IO.Path]::GetFullPath($item).TrimEnd('\')) { throw 'Системную или пользовательскую папку удалять нельзя.' }
    }
    $probe=$full
    while ($probe -and (Test-Path -LiteralPath $probe)) {
        if ((Get-Item -LiteralPath $probe -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Путь содержит ссылку. Очистка остановлена.' }
        $probe=Split-Path $probe -Parent
    }
    return $full
}
function Get-WSLRegistrations {
    @(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath })
}
function Remove-OwnedTree([string]$Path,[switch]$Top) {
    if (-not (Test-Path -LiteralPath $Path)) { return }
    # Enumerate children explicitly: never recurse through junctions/symlinks.
    foreach ($child in (Get-ChildItem -LiteralPath $Path -Force)) {
        if ($Top -and $child.Name -eq 'installation.json') { continue }
        if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            if ($child.PSIsContainer) { [IO.Directory]::Delete($child.FullName) } else { [IO.File]::Delete($child.FullName) }
        }
        elseif ($child.PSIsContainer) { Remove-OwnedTree $child.FullName }
        else { Remove-Item -LiteralPath $child.FullName -Force }
    }
    if ($Top) { Remove-Item -LiteralPath (Join-Path $Path 'installation.json') -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath $Path -Force
}
$log=Join-Path $PSScriptRoot ('ShtabAI-cleanup-'+(Get-Date -Format yyyyMMdd-HHmmss)+'.log')
$record=Join-Path $env:LOCALAPPDATA 'ShtabAI\ShtabAI-021.json'
$transcribing=$false
$exitCode=0
try {
    Start-Transcript -Path $log | Out-Null
    $transcribing=$true
    $registration=$null
    if (Test-Path -LiteralPath $record) { $registration=Get-Content -LiteralPath $record -Raw | ConvertFrom-Json }
    if (-not $InstallDir) {
        if ($registration) { $InstallDir=$registration.Root }
        elseif (Test-Path -LiteralPath 'D:\Shtab.AI\installation.json') { $InstallDir='D:\Shtab.AI' }
        else { throw 'Не найдена установка. Укажите -InstallDir с её точной папкой.' }
    }
    $root=Assert-ShtabRoot $InstallDir
    if ($PSScriptRoot -ieq $root -or $PSScriptRoot.StartsWith($root+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Сохраните скрипт очистки вне папки приложения, например D:\Temp.' }
    $passport=Join-Path $root 'installation.json'
    if (-not (Test-Path -LiteralPath $passport)) { throw 'Нет паспорта installation.json. Удалять папку вслепую нельзя.' }
    $manifest=Get-Content -LiteralPath $passport -Raw | ConvertFrom-Json
    if ($manifest.Product -ne 'ShtabAI' -or $manifest.Backend -ne 'WSL2' -or $manifest.DistroName -ne 'ShtabAI-021' -or [IO.Path]::GetFullPath($manifest.Root).TrimEnd('\') -ine $root) { throw 'Паспорт не соответствует тестовой установке Штаб.AI.' }
    $distro=$manifest.DistroName
    $task='ShtabAI-'+$distro+'-Start'
    $rule='ShtabAI-'+$distro+'-LAN'
    if ($manifest.TaskName -ne $task -or $manifest.LANRule -ne $rule -or $manifest.HTTPSPort -ne 8445) { throw 'Неожиданные имена задачи или сетевого правила.' }
    $distros=@(Get-WSLRegistrations)
    $owned=@($distros | Where-Object DistributionName -eq $distro)
    if ($owned.Count -gt 1) { throw 'Дублируется регистрация WSL.' }
    if ($owned.Count -eq 1) {
        $base=[string]$owned[0].BasePath
        if ($base.StartsWith('\\?\')) { $base=$base.Substring(4) }
        if ([IO.Path]::GetFullPath($base).TrimEnd('\') -ine (Join-Path $root 'distro')) { throw 'WSL-дистрибутив находится вне проверенной папки.' }
    }
    $backup=''
    if ($RemoveBackups -and $manifest.BackupPath) {
        $backup=Assert-ShtabRoot $manifest.BackupPath
        if ((Split-Path $backup -Leaf) -notin @('ShtabAI-Backups','shtab-ai-021-backups')) { throw 'Папка резервных копий имеет неожиданное имя; её удаление остановлено.' }
    }
    Write-Host ('Удаляем тестовую установку: '+$root) -ForegroundColor Yellow
    Write-Host 'Будут удалены её Ubuntu, база данных, модели, службы, ярлыки и сетевые правила.'
    Write-Host 'Закройте Far, Проводник и терминалы, работающие внутри папки приложения, чтобы они не удерживали файлы.'
    Write-Progress -Activity 'Очистка Штаб.AI' -Status 'Остановка собственной задачи и Ollama' -PercentComplete 10
    $scheduled=Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($scheduled) { Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue; Unregister-ScheduledTask -TaskName $task -Confirm:$false }
    $prefix=(Join-Path $root 'ollama')+'\'
    Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    $wsl=Join-Path $env:SystemRoot 'System32\wsl.exe'
    Write-Progress -Activity 'Очистка Штаб.AI' -Status 'Удаление собственного WSL-дистрибутива' -PercentComplete 25
    if ($owned.Count -eq 1) {
        & $wsl --unregister $distro
        if ($LASTEXITCODE -ne 0) { throw 'WSL не подтвердил удаление. Файлы сохранены.' }
        if (@(Get-WSLRegistrations | Where-Object DistributionName -eq $distro).Count) { throw 'Регистрация WSL осталась; удаление файлов остановлено.' }
    }
    foreach ($name in @($rule,($task+'-Ollama'))) { Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Remove-NetFirewallRule }
    if ($manifest.LANAddress -match '^\d+\.\d+\.\d+\.\d+$') { & netsh.exe interface portproxy delete v4tov4 listenaddress=$($manifest.LANAddress) listenport=8445 | Out-Null }
    $thumb=[string]$manifest.CertificateThumbprint
    if ($thumb -match '^[A-Fa-f0-9]{40}$') {
        $public=Join-Path $root 'shtab-ai-root.crt'
        if (Test-Path -LiteralPath $public) {
            $cert=New-Object -TypeName Security.Cryptography.X509Certificates.X509Certificate2 -ArgumentList $public
            if ($cert.Thumbprint -eq $thumb) { Remove-Item ('Cert:\CurrentUser\Root\'+$thumb) -ErrorAction SilentlyContinue }
        }
    }
    $desktop=[Environment]::GetFolderPath('Desktop')
    foreach ($name in @($distro+'.url',$distro+'-Manager.lnk')) { Remove-Item -LiteralPath (Join-Path $desktop $name) -Force -ErrorAction SilentlyContinue }
    $config=Join-Path $env:USERPROFILE '.wslconfig'
    if ($manifest.WSLConfigCreated -and (Test-Path -LiteralPath $config) -and [IO.File]::ReadAllText($config) -ceq [string]$manifest.WSLConfigText) { Remove-Item -LiteralPath $config -Force }
    Write-Progress -Activity 'Очистка Штаб.AI' -Status 'Удаление файлов установки и моделей' -PercentComplete 60
    Remove-OwnedTree $root -Top
    Remove-Item -LiteralPath $record -Force -ErrorAction SilentlyContinue
    if ($backup) { Remove-OwnedTree $backup }
    elseif ($manifest.BackupPath) { Write-Host ('Резервные копии сохранены: '+$manifest.BackupPath) }
    $remaining=@(Get-WSLRegistrations)
    if (-not $KeepWSL -and -not $remaining.Count) {
        Write-Progress -Activity 'Очистка Штаб.AI' -Status 'Удаление общего WSL' -PercentComplete 80
        & $wsl --shutdown
        & $wsl --uninstall
        if ($LASTEXITCODE -ne 0) { throw 'Установка Штаб.AI удалена, но общий WSL не удалился. См. журнал; очистка требует завершения.' }
        $feature=Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux
        if ($feature.State -eq 'Enabled') { Disable-WindowsOptionalFeature -Online -FeatureName Microsoft-Windows-Subsystem-Linux -NoRestart | Out-Null }
        Write-Host 'WSL удалён. Перезагрузите Windows перед новой установкой.' -ForegroundColor Green
    } elseif ($remaining.Count) {
        Write-Host ('Общий WSL сохранён: найдены другие дистрибутивы — '+(($remaining | ForEach-Object { $_.DistributionName }) -join ', ')) -ForegroundColor Yellow
    } else { Write-Host 'Общий WSL сохранён по параметру -KeepWSL.' }
    if (Test-Path -LiteralPath $root) { throw 'Папка установки осталась.' }
    Write-Host 'Тестовая установка очищена. Журналы сохранены для диагностики.' -ForegroundColor Green
} catch {
    Write-Host ('Очистка остановлена: '+$_.Exception.Message) -ForegroundColor Red
    Write-Host $_.InvocationInfo.PositionMessage
    $exitCode=1
} finally {
    Write-Progress -Activity 'Очистка Штаб.AI' -Completed
    if ($transcribing) { Stop-Transcript | Out-Null }
    Write-Host ('Журнал: '+$log)
}
exit $exitCode

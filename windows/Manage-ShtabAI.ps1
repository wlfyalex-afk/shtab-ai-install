#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ManifestPath)
$ErrorActionPreference='Stop'
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
$principal=New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $powershell=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arguments='-NoProfile -ExecutionPolicy Bypass -File "'+$PSCommandPath+'" -ManifestPath "'+$ManifestPath+'"'
    Start-Process $powershell -Verb RunAs -ArgumentList $arguments -Wait
    exit
}
$manifest=Get-Content $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.Product -ne 'ShtabAI' -or $manifest.DistroName -notmatch '^ShtabAI-[A-Za-z0-9-]+$') { throw 'Некорректная установка.' }
$wsl=Join-Path $env:SystemRoot 'System32\wsl.exe'
function Sync-Certificate {
    & $wsl --distribution $manifest.DistroName --user root --exec /opt/shtab-ai-021/shtabctl certificate
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось экспортировать сертификат.' }
    $cert=Join-Path $manifest.Root 'shtab-ai-root.crt'
    $guestPath=((& $wsl --distribution $manifest.DistroName --user root --exec wslpath -u $cert) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось определить путь сертификата.' }
    & $wsl --distribution $manifest.DistroName --user root --exec /bin/cp /opt/shtab-ai-021/shtab-ai-root.crt $guestPath
    if ($LASTEXITCODE -ne 0) { throw 'Не удалось скопировать сертификат.' }
    $imported=Import-Certificate -FilePath $cert -CertStoreLocation Cert:\CurrentUser\Root
    $manifest=Get-Content $ManifestPath -Raw | ConvertFrom-Json
    $old=$manifest.CertificateThumbprint
    $manifest.CertificateThumbprint=$imported.Thumbprint
    [IO.File]::WriteAllText($ManifestPath,($manifest | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    if ($old -match '^[A-Fa-f0-9]{40}$' -and $old -ne $imported.Thumbprint) { Remove-Item ('Cert:\CurrentUser\Root\'+$old) -ErrorAction SilentlyContinue }
}
while ($true) {
    $manifest=Get-Content $ManifestPath -Raw | ConvertFrom-Json
    Clear-Host
    Write-Host ('Диспетчер Штаб.AI / '+$manifest.DistroName)
    Write-Host '1 — Открыть приложение'
    Write-Host '2 — Управление: копии, восстановление, пользователи и журналы'
    Write-Host '3 — Запустить Штаб.AI'
    Write-Host '4 — Остановить Штаб.AI'
    Write-Host '5 — Журнал Ollama'
    Write-Host '6 — Адрес в сети и публичный сертификат'
    Write-Host '7 — Открыть папку резервных копий'
    Write-Host '0 — Выход'
    $choice=Read-Host 'Выберите пункт'
    switch ($choice) {
        '0' { exit }
        '1' { Start-Process ("https://localhost:$($manifest.HTTPSPort)/login") }
        '2' {
            & $wsl --distribution $manifest.DistroName --user root --exec /opt/shtab-ai-021/shtabctl menu
            try { Sync-Certificate } catch { Write-Host $_.Exception.Message -ForegroundColor Yellow }
        }
        '3' {
            Start-ScheduledTask -TaskName $manifest.TaskName
            Start-Sleep -Seconds 3
            & $wsl --distribution $manifest.DistroName --user root --exec /opt/shtab-ai-021/shtabctl start
        }
        '4' {
            & $wsl --distribution $manifest.DistroName --user root --exec /opt/shtab-ai-021/shtabctl stop
            Stop-ScheduledTask -TaskName $manifest.TaskName
            $prefix=(Join-Path $manifest.Root 'ollama')+'\'
            Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath.StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
            & $wsl --terminate $manifest.DistroName
        }
        '5' { Get-Content (Join-Path $manifest.Root 'ollama-error.log') -Tail 60 -ErrorAction SilentlyContinue }
        '6' {
            if ($manifest.Network -and $manifest.LANAddress) { Write-Host ("Адрес в сети: https://$($manifest.LANAddress):$($manifest.HTTPSPort)/login") } else { Write-Host "Сетевой доступ сейчас недоступен; локальный адрес: https://localhost:$($manifest.HTTPSPort)/login" }
            Write-Host ('Публичный сертификат: '+(Join-Path $manifest.Root 'shtab-ai-root.crt'))
            Write-Host 'На другом компьютере Windows добавьте сертификат в доверенные корневые центры сертификации текущего пользователя.'
        }
        '7' { Start-Process explorer.exe -ArgumentList ('"'+$manifest.BackupPath+'"') }
        default { Write-Host 'Некорректный выбор.' }
    }
    [void](Read-Host 'Нажмите Enter')
}


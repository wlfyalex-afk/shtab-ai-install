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
if ($manifest.Product -ne 'ShtabAI' -or $manifest.DistroName -notmatch '^ShtabAI-[A-Za-z0-9-]+$') { throw 'Invalid installation.' }
$wsl=Join-Path $env:SystemRoot 'System32\wsl.exe'
function Sync-Certificate {
    & $wsl --distribution $manifest.DistroName --user root --exec /opt/shtab-ai-021/shtabctl certificate
    if ($LASTEXITCODE -ne 0) { throw 'Certificate export failed.' }
    $cert=Join-Path $manifest.Root 'shtab-ai-root.crt'
    $guestPath=((& $wsl --distribution $manifest.DistroName --user root --exec wslpath -u $cert) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Certificate path failed.' }
    & $wsl --distribution $manifest.DistroName --user root --exec /bin/cp /opt/shtab-ai-021/shtab-ai-root.crt $guestPath
    if ($LASTEXITCODE -ne 0) { throw 'Certificate copy failed.' }
    $imported=Import-Certificate -FilePath $cert -CertStoreLocation Cert:\CurrentUser\Root
    $old=$manifest.CertificateThumbprint
    $manifest.CertificateThumbprint=$imported.Thumbprint
    [IO.File]::WriteAllText($ManifestPath,($manifest | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    if ($old -match '^[A-Fa-f0-9]{40}$' -and $old -ne $imported.Thumbprint) { Remove-Item ('Cert:\CurrentUser\Root\'+$old) -ErrorAction SilentlyContinue }
}
while ($true) {
    Clear-Host
    Write-Host ('Shtab.AI manager / '+$manifest.DistroName)
    Write-Host '1 - Open application'
    Write-Host '2 - Administration: backup, restore, users, status, logs'
    Write-Host '3 - Start Shtab.AI'
    Write-Host '4 - Stop Shtab.AI'
    Write-Host '5 - Native Ollama log'
    Write-Host '6 - LAN address and public certificate'
    Write-Host '7 - Open backup folder (preserved after uninstall)'
    Write-Host '0 - Exit'
    $choice=Read-Host 'Select'
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
            if ($manifest.Network) { Write-Host ("LAN: https://$($manifest.LANAddress):$($manifest.HTTPSPort)/login") }
            Write-Host ('Public certificate: '+(Join-Path $manifest.Root 'shtab-ai-root.crt'))
            Write-Host 'On another Windows PC, import this certificate into Trusted Root Certification Authorities for the current user.'
        }
        '7' { Start-Process explorer.exe -ArgumentList ('"'+$manifest.BackupPath+'"') }
        default { Write-Host 'Invalid selection.' }
    }
    [void](Read-Host 'Press Enter')
}

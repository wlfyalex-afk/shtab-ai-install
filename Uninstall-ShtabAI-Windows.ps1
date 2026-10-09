#Requires -Version 5.1
[CmdletBinding()]
param([ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName,[switch]$ListOnly,[switch]$KeepWindowOpen)
$ErrorActionPreference='Stop'
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
                if (-not (Test-Path $file)) { throw ('Installation is unavailable: '+$record.Root+'. Connect the installation drive and retry.') }
                $actual=Get-Content $file -Raw | ConvertFrom-Json
                if ($actual.Product -ne 'ShtabAI' -or $actual.Backend -ne 'WSL2' -or $actual.DistroName -ne $record.DistroName -or $actual.Root -ne $record.Root) { throw 'Installation registration mismatch.' }
                if (-not @($candidates | Where-Object Root -eq $record.Root).Count) {
                    $candidates+=[pscustomobject]@{Number=$candidates.Count+1; Name=$actual.DistroName; Root=$actual.Root; Acceleration=$actual.Acceleration}
                }
            }
        }
    }
    if (-not $candidates.Count) { Write-Host 'No Shtab.AI WSL installations or unfinished installation directories found.'; return }
    $candidates | Format-Table -Property @('Number','Name','Acceleration','Root') -AutoSize | Out-Host
    if ($ListOnly) { return }
    if (-not $DistroName) {
        $choice=Read-Host 'Select installation number, or 0 to cancel'
        $number=0
        if (-not [int]::TryParse($choice,[ref]$number) -or $number -lt 1 -or $number -gt $candidates.Count) { Write-Host 'Cancelled.'; return }
        $DistroName=$candidates[$number-1].Name
    }
    $selected=@($candidates | Where-Object Name -eq $DistroName)
    if ($selected.Count -ne 1) { throw 'Owned installation not found.' }
    $root=$selected[0].Root
    $manifestPath=Join-Path $root 'installation.json'
    $manifest=Get-Content $manifestPath -Raw | ConvertFrom-Json
    if ($root -notmatch '^[A-Za-z]:\\' -or [IO.Path]::GetFullPath($root).TrimEnd('\').Length -lt 4) { throw 'Unsafe installation root.' }
    if ([IO.Path]::GetFullPath($manifest.Root).TrimEnd('\') -ne [IO.Path]::GetFullPath($root).TrimEnd('\')) { throw 'Manifest root mismatch. Nothing deleted.' }
    if ((Get-Item $root -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installation root is a link. Nothing deleted.' }
    if (@(Get-ChildItem $root -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Installation directory contains links. Nothing deleted.' }
    $registered=@(Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Lxss' -ErrorAction SilentlyContinue | ForEach-Object { Get-ItemProperty $_.PSPath } | Where-Object DistributionName -eq $DistroName)
    if ($registered.Count -gt 1) { throw 'Ambiguous WSL registration.' }
    if ($registered.Count -eq 1) {
        $actual=([string]$registered[0].BasePath).Replace('\\?\','').TrimEnd('\')
        $expected=(Join-Path $root 'distro').TrimEnd('\')
        if ([IO.Path]::GetFullPath($actual) -ne [IO.Path]::GetFullPath($expected)) { throw 'WSL distribution uses another directory. Nothing deleted.' }
    }
    $taskName='ShtabAI-'+$DistroName+'-Start'
    if ($manifest.TaskName -ne $taskName) { throw 'Task ownership mismatch.' }
    $task=Get-ScheduledTask -TaskName $taskName -TaskPath '\' -ErrorAction SilentlyContinue
    if ($task -and ($task.Actions.Arguments -notlike ('*'+(Join-Path $root 'Start-ShtabRuntime.ps1')+'*'))) { throw 'Unexpected task action. Nothing deleted.' }
    Write-Host 'Will delete the selected WSL distribution, recordings, users, models, native Ollama, shortcut and owned network settings. No backup.' -ForegroundColor Yellow
    $answer=Read-Host ('Type DELETE '+$DistroName+' to confirm')
    if ($answer -cne ('DELETE '+$DistroName)) { Write-Host 'Cancelled.'; return }
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
        if ($LASTEXITCODE -ne 0) { throw 'WSL removal failed. Files preserved.' }
    }
    foreach ($name in @(($taskName+'-Ollama'),('ShtabAI-'+$DistroName+'-LAN'))) {
        $rule=Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue
        if ($rule -and $rule.Group -eq 'ShtabAI') { Remove-NetFirewallRule -Name $name }
    }
    if ($manifest.Network) {
        & netsh.exe interface portproxy delete v4tov4 listenaddress=$($manifest.LANAddress) listenport=$($manifest.HTTPSPort) | Out-Null
        if ($LASTEXITCODE -ne 0) { Write-Host 'No LAN forwarding entry, or cleanup incomplete; inspect netsh interface portproxy show v4tov4.' -ForegroundColor Yellow }
    }
    $shortcut=Join-Path ([Environment]::GetFolderPath('Desktop')) ($DistroName+'.url')
    if ((Test-Path $shortcut) -and (Get-Content $shortcut -Raw) -match ('(?m)^URL=https://localhost:'+ $manifest.HTTPSPort+'/login\s*$')) { Remove-Item $shortcut -Force }
    $managerPath=Join-Path ([Environment]::GetFolderPath('Desktop')) ($DistroName+'-Manager.lnk')
    if (Test-Path $managerPath) {
        $shell=New-Object -ComObject WScript.Shell
        $link=$shell.CreateShortcut($managerPath)
        if ($link.Arguments -like ('*'+(Join-Path $root 'Manage-ShtabAI.ps1')+'*')) { Remove-Item $managerPath -Force }
    }
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
    Write-Host 'Shtab.AI removed. Windows, WSL, GPU drivers and other distributions preserved.' -ForegroundColor Green
} catch {
    Write-Host ('Removal stopped: '+$_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($KeepWindowOpen -and $principal -and $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        [void](Read-Host 'Press Enter to close this removal window')
    }
}

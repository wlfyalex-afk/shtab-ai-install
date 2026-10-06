#Requires -Version 5.1
#Requires -RunAsAdministrator
$ErrorActionPreference = 'Stop'
$mp = Join-Path $env:ProgramFiles 'Multipass\bin\multipass.exe'
if (Test-Path $mp) {
    $raw = & $mp list --format json
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Multipass VMs; uninstall stopped to preserve data.' }
    $inventory = $raw | Out-String | ConvertFrom-Json
    if (@($inventory.list).Count -gt 0) { throw 'Multipass contains VMs. Uninstall stopped; no VM was deleted.' }
}
$keys = @('HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
$packages = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^Multipass(?:\s|$)' -and $_.Publisher -match 'Canonical' })
if ($packages.Count -eq 0) { Write-Host 'Multipass is not installed.'; return }
if ($packages.Count -ne 1 -or $packages[0].PSChildName -notmatch '^\{[0-9A-Fa-f-]{36}\}$') { throw 'Cannot identify the Multipass MSI package safely.' }
$result = Start-Process msiexec.exe -ArgumentList ('/x ' + $packages[0].PSChildName + ' /qn /norestart') -Wait -PassThru
if ($result.ExitCode -notin @(0,3010)) { throw "Multipass uninstall failed: $($result.ExitCode)" }
Write-Host 'Multipass removed. Hyper-V and other VMs were preserved.'
if ($result.ExitCode -eq 3010) { Write-Host 'Windows restart is required.' }

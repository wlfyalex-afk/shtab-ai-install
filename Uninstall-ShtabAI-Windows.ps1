#Requires -Version 5.1
<#
Remove one native Hyper-V Shtab.AI installation and its Windows connection settings.
No backup is made. Shared tools, external switches and other VMs are retained.
Run under the Windows account used to install Shtab.AI.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[a-z](?:[a-z0-9-]{0,39}[a-z0-9])?$')][string]$VMName = 'shtab-ai',
    [string]$VMRoot = '',
    [ValidateRange(1024,65535)][int]$HTTPSPort = 8445
)
$ErrorActionPreference = 'Stop'
$exitCode = 0
try {
    if (-not $VMRoot) { $VMRoot = Join-Path $env:SystemDrive ('ShtabAI\' + $VMName) }
    if ($VMRoot.Contains('"')) { throw 'Invalid directory name.' }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        if (-not $PSCommandPath) { throw 'Save this script to a file before running it.' }
        $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $PSCommandPath + '" -VMName ' + $VMName + ' -VMRoot "' + $VMRoot + '" -HTTPSPort ' + $HTTPSPort
        $process = Start-Process -FilePath $powershell -Verb RunAs -ArgumentList $arguments -Wait -PassThru
        exit $process.ExitCode
    }
    Import-Module Hyper-V
    $VMRoot = [IO.Path]::GetFullPath($VMRoot).TrimEnd('\')
    if ((Split-Path $VMRoot -Leaf) -ne $VMName) { throw 'VM directory must end with the VM name. Nothing deleted.' }
    $prefix = $VMRoot + '\'
    $vm = Get-VM -Name $VMName -ErrorAction SilentlyContinue
    $connectionDir = Join-Path $env:LOCALAPPDATA ('ShtabAI\' + $VMName)
    foreach ($directory in @($VMRoot,$connectionDir)) {
        if (Test-Path -LiteralPath $directory) {
            $item = Get-Item -LiteralPath $directory -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Installation directory is not a regular directory. Nothing deleted.' }
            if (@(Get-ChildItem -LiteralPath $directory -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count -gt 0) { throw 'Installation directory contains links. Nothing deleted.' }
        }
    }
    if ($vm) {
        $ownedPaths = @($vm.Path) + @(Get-VMHardDiskDrive -VM $vm | ForEach-Object Path)
        foreach ($path in $ownedPaths) {
            if ($path -and -not ([IO.Path]::GetFullPath($path).TrimEnd('\') + '\').StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'VM uses another directory. Specify its actual -VMRoot. Nothing deleted.' }
        }
    }
    foreach ($other in @(Get-VM | Where-Object Name -ne $VMName)) {
        $paths = @($other.Path) + @(Get-VMHardDiskDrive -VM $other | ForEach-Object Path) + @(Get-VMDvdDrive -VM $other | ForEach-Object Path)
        foreach ($path in $paths) {
            if ($path -and ([IO.Path]::GetFullPath($path).TrimEnd('\') + '\').StartsWith($prefix,[StringComparison]::OrdinalIgnoreCase)) { throw 'Another VM uses this directory. Nothing deleted.' }
        }
    }
    $switchName = 'ShtabAI-' + $VMName
    $switch = Get-VMSwitch -Name $switchName -ErrorAction SilentlyContinue
    $networkUsers = @(Get-VM | Where-Object Name -ne $VMName | Get-VMNetworkAdapter | Where-Object SwitchName -eq $switchName)
    if ($networkUsers.Count -gt 0 -or ($switch -and $switch.SwitchType -ne 'Internal')) { throw 'Installation network is shared or unexpected. Nothing deleted.' }
    $taskName = 'ShtabAI-' + $VMName + '-Connection'
    $task = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
    if ($task) {
        $taskArgs = ($task.Actions | ForEach-Object Arguments) -join ' '
        if ($taskArgs -notmatch ('-VMName\s+' + [regex]::Escape($VMName) + '(\s|$)') -or $taskArgs -notmatch '-Backend\s+HyperV') { throw 'Unexpected connection task. Nothing deleted.' }
        if ($taskArgs -match '-Port\s+(\d+)') { $HTTPSPort = [int]$Matches[1] }
    }
    $certificateFile = Join-Path $connectionDir 'shtab-ai-root.crt'
    $thumbprint = ''
    if (Test-Path -LiteralPath $certificateFile) {
        $certificate = New-Object Security.Cryptography.X509Certificates.X509Certificate2($certificateFile)
        $thumbprint = $certificate.Thumbprint
        $certificate.Dispose()
    }
    Write-Host ('Will delete VM ' + $VMName + ', ALL its application data and disk in ' + $VMRoot + '.') -ForegroundColor Yellow
    Write-Host 'No backup will be made. Close any running Windows installer first.'
    $answer = Read-Host ('Type DELETE ' + $VMName + ' to confirm')
    if ($answer -cne ('DELETE ' + $VMName)) { throw 'Cancelled. Nothing deleted.' }
    foreach ($name in @($taskName,('ShtabAI-' + $VMName + '-Start'))) {
        Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue | Unregister-ScheduledTask -Confirm:$false
    }
    if ($vm) {
        if ($vm.State -ne 'Off') { Stop-VM -VM $vm -TurnOff -Force }
        Remove-VM -VM $vm -Force
    }
    Get-NetNat -Name $switchName -ErrorAction SilentlyContinue | Remove-NetNat -Confirm:$false
    if ($switch) { Remove-VMSwitch -VMSwitch $switch -Force }
    if ($task) {
        $mapping = & netsh.exe interface portproxy show v4tov4
        if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect port forwarding. VM removed; cleanup incomplete.' }
        $pattern = '^\s*127\.0\.0\.1\s+' + $HTTPSPort + '\s+\S+\s+443\s*$'
        if (@($mapping | Where-Object { $_ -match $pattern }).Count -gt 0) {
            & netsh.exe interface portproxy delete v4tov4 listenaddress=127.0.0.1 listenport=$HTTPSPort | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Cannot remove port forwarding. Cleanup incomplete.' }
        }
    }
    $hosts = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
    $lines = @(Get-Content -LiteralPath $hosts)
    $marker = '# ShtabAI ' + [regex]::Escape($VMName) + '\s*$'
    $kept = @($lines | Where-Object { $_ -notmatch $marker })
    if ($kept.Count -ne $lines.Count) { Set-Content -LiteralPath $hosts -Value $kept -Encoding ASCII }
    if ($thumbprint) {
        $trusted = 'Cert:\CurrentUser\Root\' + $thumbprint
        if (Test-Path -LiteralPath $trusted) { Remove-Item -LiteralPath $trusted -Force }
    }
    foreach ($path in @($VMRoot,$connectionDir)) {
        if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Recurse -Force }
    }
    Write-Host 'Shtab.AI VM, disk, keys and Windows connection settings removed.' -ForegroundColor Green
    Write-Host 'Windows, Hyper-V, QEMU, download cache, external switches and other VMs retained.'
} catch {
    Write-Host ('Uninstallation stopped: ' + $_.Exception.Message) -ForegroundColor Red
    $exitCode = 1
}
[void](Read-Host 'Press Enter to close this window')
exit $exitCode

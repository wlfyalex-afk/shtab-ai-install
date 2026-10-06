#Requires -Version 5.1
#Requires -RunAsAdministrator
param(
    [string]$MultipassPath = '',
    [ValidateSet('Multipass','HyperV')][string]$Backend = 'Multipass',
    [ValidatePattern('^[a-z](?:[a-z0-9-]{0,39}[a-z0-9])?$')][string]$VMName = 'shtab-ai-test',
    [ValidateRange(1024,65535)][int]$Port = 8443
)
$ErrorActionPreference = 'Stop'
if ($Backend -eq 'HyperV') {
    Import-Module Hyper-V -ErrorAction Stop
    $vm = Get-VM -Name $VMName -ErrorAction SilentlyContinue
    if (-not $vm -or $vm.State -ne 'Running') { exit 0 }
    $ip = @(Get-VMNetworkAdapter -VMName $VMName | ForEach-Object IPAddresses | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' -and $_ -notlike '169.254.*' }) | Select-Object -First 1
} else {
    if (-not $MultipassPath) { throw 'MultipassPath is required for the Multipass backend.' }
$json = & $MultipassPath info $VMName --format json 2>$null
if ($LASTEXITCODE -ne 0) { exit 0 } # VM may be stopped; keep last mapping.
$info = ($json | Out-String | ConvertFrom-Json).info.PSObject.Properties[$VMName].Value
if ($info.state -ne 'Running') { exit 0 }
$ip = @($info.ipv4 | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' }) | Select-Object -First 1
}
if (-not $ip) { exit 0 }
Start-Service iphlpsvc
$mapping = & netsh.exe interface portproxy show v4tov4
if ($LASTEXITCODE -ne 0) { throw 'Cannot read Windows port forwarding.' }
$pattern = '^\s*127\.0\.0\.1\s+' + $Port + '\s+' + [regex]::Escape($ip) + '\s+443\s*$'
if (@($mapping | Where-Object { $_ -match $pattern }).Count -eq 0) {
    & netsh.exe interface portproxy set v4tov4 listenaddress=127.0.0.1 listenport=$Port connectaddress=$ip connectport=443
    if ($LASTEXITCODE -ne 0) {
        & netsh.exe interface portproxy add v4tov4 listenaddress=127.0.0.1 listenport=$Port connectaddress=$ip connectport=443
        if ($LASTEXITCODE -ne 0) { throw 'Cannot configure Windows port forwarding.' }
    }
}


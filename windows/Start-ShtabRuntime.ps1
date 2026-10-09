#Requires -Version 5.1
#Requires -RunAsAdministrator
param([Parameter(Mandatory=$true)][string]$ManifestPath)
$ErrorActionPreference = 'Stop'
[Net.WebRequest]::DefaultWebProxy = New-Object Net.WebProxy
$manifest = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.Product -ne 'ShtabAI' -or $manifest.Backend -ne 'WSL2' -or $manifest.DistroName -notmatch '^ShtabAI-[A-Za-z0-9-]+$') { throw 'Invalid installation manifest.' }
$root = Split-Path $ManifestPath
if ([IO.Path]::GetFullPath($manifest.Root) -ne [IO.Path]::GetFullPath($root)) { throw 'Installation path mismatch.' }
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$ollama = Join-Path $root 'ollama\ollama.exe'
$keepalive = $null
$server = $null
$lastIP = ''
$lastGateway = ''
$lastMode = ''
$lastForward = '__startup__'
$previousLAN = [string]$manifest.LANAddress
function Guest {
    & $wsl --distribution $manifest.DistroName --user root --exec @args
    if ($LASTEXITCODE -ne 0) { throw 'WSL runtime command failed.' }
}
function Write-State([string]$IP,[string]$Gateway) {
    $json = @{ IP=$IP; Gateway=$Gateway; Endpoint=("http://${Gateway}:11435"); Ready=$true; Acceleration=$lastMode; LANAddress=$manifest.LANAddress; LANUrl=$(if ($manifest.LANAddress) { "https://$($manifest.LANAddress):$($manifest.HTTPSPort)/login" } else { "" }) } | ConvertTo-Json
    $temporary = Join-Path $root 'runtime-state.tmp'
    [IO.File]::WriteAllText($temporary,$json,(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination (Join-Path $root 'runtime-state.json') -Force
}
function Current-LAN {
    if (-not $manifest.Network) { return '' }
    $configs = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address })
    if ($manifest.LANInterfaceGuid) {
        $configs = @($configs | Where-Object { [string]$_.NetAdapter.InterfaceGuid -eq [string]$manifest.LANInterfaceGuid })
    } else {
        # Upgrade older manifests only when the original address or one unambiguous trusted adapter is available.
        $original = @($configs | Where-Object { $_.IPv4Address.IPAddress -contains $manifest.LANAddress })
        if ($original.Count -eq 1) { $configs = $original }
    }
    $trusted = @($configs | Where-Object {
        $profiles = @(Get-NetConnectionProfile -InterfaceIndex $_.InterfaceIndex -ErrorAction SilentlyContinue)
        $profiles.Count -eq 1 -and $profiles[0].NetworkCategory -in @('Private','DomainAuthenticated')
    })
    if ($trusted.Count -ne 1) { return '' }
    $adapter = $trusted[0]
    $manifest | Add-Member -NotePropertyName LANInterfaceGuid -NotePropertyValue ([string]$adapter.NetAdapter.InterfaceGuid) -Force
    $addresses = @($adapter.IPv4Address | Where-Object { $_.IPAddress -notlike '169.254.*' -and $_.IPAddress -notlike '127.*' })
    if (-not $addresses.Count) { return '' }
    return [string]$addresses[0].IPAddress
}
function Sync-LAN([string]$IP) {
    $listen = Current-LAN
    $port = [int]$manifest.HTTPSPort
    $key = "$listen/$IP"
    if ($key -eq $script:lastForward) { return }
    if ($script:previousLAN) {
        & netsh.exe interface portproxy delete v4tov4 listenaddress=$script:previousLAN listenport=$port | Out-Null
    }
    Get-NetFirewallRule -Name $manifest.LANRule -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    if ($listen) {
        Start-Service iphlpsvc
        & netsh.exe interface portproxy add v4tov4 listenaddress=$listen listenport=$port connectaddress=$IP connectport=$port | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Cannot configure LAN forwarding.' }
        New-NetFirewallRule -Name $manifest.LANRule -DisplayName ('ShtabAI LAN ' + $manifest.DistroName) -Group 'ShtabAI' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $listen -LocalPort $port -RemoteAddress LocalSubnet -Profile @('Private','Domain') | Out-Null
    }
    $manifest.LANAddress = $listen
    # Reload fields that the manager may have changed (certificate, backup location).
    $fresh = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
    $fresh.LANAddress = $listen
    if ($manifest.LANInterfaceGuid) { $fresh | Add-Member -NotePropertyName LANInterfaceGuid -NotePropertyValue $manifest.LANInterfaceGuid -Force }
    $temp = $ManifestPath + '.runtime.tmp'
    [IO.File]::WriteAllText($temp,($fresh | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temp -Destination $ManifestPath -Force
    $script:previousLAN = $listen
    $script:lastForward = $key
}
function Sync-GuestConfig([string]$Gateway) {
    $config = '/opt/shtab-ai-021/scripts/runtime-config.py'
    & $wsl --distribution $manifest.DistroName --user root --exec /usr/bin/test -f $config
    if ($LASTEXITCODE -ne 0) { return }
    $hostName = if ($manifest.LANAddress) { $manifest.LANAddress } else { 'localhost' }
    $changed = (Guest /usr/bin/python3 $config "http://${Gateway}:11435" $hostName | Out-String).Trim()
    if ($changed -eq 'CHANGED') {
        Guest /bin/systemctl start docker
        Guest /bin/bash /opt/shtab-ai-021/scripts/dc.sh up -d --no-deps web meeting-worker llm-worker brief-worker proxy
        Guest /bin/rm -f /opt/shtab-ai-021/.runtime-config-pending
    }
}
try {
    $keepalive = Start-Process -FilePath $wsl -ArgumentList ('--distribution ' + $manifest.DistroName + ' --user root --exec /bin/sleep infinity') -WindowStyle Hidden -PassThru
    while ($true) {
      try {
        if ($keepalive.HasExited) {
            $keepalive = Start-Process -FilePath $wsl -ArgumentList ('--distribution ' + $manifest.DistroName + ' --user root --exec /bin/sleep infinity') -WindowStyle Hidden -PassThru
        }
        $ip = ((Guest /bin/hostname -I | Out-String).Trim() -split '\s+' | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' }) | Select-Object -First 1
        $route = (Guest /sbin/ip -4 route show default | Out-String).Trim()
        if ($route -notmatch '^default via (\d+\.\d+\.\d+\.\d+)') { throw 'WSL NAT gateway not found. This installer requires WSL NAT networking.' }
        $gateway = $Matches[1]
        if (-not $ip) { throw 'WSL address is unavailable.' }
        $mode = [string]$manifest.Acceleration
        & $wsl --distribution $manifest.DistroName --user root --exec /usr/bin/test -f /opt/shtab-ai-021/native-cpu-fallback.json
        if ($LASTEXITCODE -eq 0) {
            $fallback = (Guest /bin/cat /opt/shtab-ai-021/native-cpu-fallback.json | Out-String) | ConvertFrom-Json
            if ($fallback.mode -eq 'cpu') { $mode='cpu' }
        }
        if ($mode -ne $lastMode -or $ip -ne $lastIP -or $gateway -ne $lastGateway -or -not $server -or $server.HasExited) {
            if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force }
            $ruleName = $manifest.TaskName + '-Ollama'
            $rule = Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue
            if ($rule) { Remove-NetFirewallRule -Name $ruleName }
            New-NetFirewallRule -Name $ruleName -DisplayName ('ShtabAI native Ollama ' + $manifest.DistroName) -Group 'ShtabAI' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $gateway -LocalPort 11435 -RemoteAddress $ip -Profile Any | Out-Null
            $env:OLLAMA_HOST = "${gateway}:11435"
            $env:OLLAMA_MODELS = Join-Path $root 'models'
            $env:OLLAMA_KEEP_ALIVE = '0'
            $env:OLLAMA_NUM_PARALLEL = '1'
            $env:OLLAMA_MAX_LOADED_MODELS = '1'
            $env:OLLAMA_VULKAN = '0'
            Remove-Item -Path @('Env:CUDA_VISIBLE_DEVICES','Env:HIP_VISIBLE_DEVICES','Env:ROCR_VISIBLE_DEVICES') -ErrorAction SilentlyContinue
            if ($mode -eq 'cpu') {
                $env:CUDA_VISIBLE_DEVICES = '-1'; $env:HIP_VISIBLE_DEVICES = '-1'; $env:ROCR_VISIBLE_DEVICES = '-1'
            } elseif ($mode -eq 'amd') { $env:CUDA_VISIBLE_DEVICES = '-1' }
            elseif ($mode -eq 'nvidia') { $env:HIP_VISIBLE_DEVICES = '-1'; $env:ROCR_VISIBLE_DEVICES = '-1' }
            $server = Start-Process -FilePath $ollama -ArgumentList 'serve' -WorkingDirectory (Split-Path $ollama) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $root 'ollama.log') -RedirectStandardError (Join-Path $root 'ollama-error.log') -PassThru
            $ready = $false
            for ($attempt=0; $attempt -lt 30; $attempt++) {
                if ($server.HasExited) { throw 'Native Ollama stopped. See ollama-error.log.' }
                try { $null = Invoke-RestMethod -Uri ("http://${gateway}:11435/api/version") -TimeoutSec 2; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
            }
            if (-not $ready) { throw 'Native Ollama did not become ready.' }
            $lastIP=$ip; $lastGateway=$gateway; $lastMode=$mode
            if ($mode -eq 'cpu') {
                $freshMode = Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
                $freshMode.Acceleration='cpu'
                [IO.File]::WriteAllText($ManifestPath,($freshMode | ConvertTo-Json),(New-Object Text.UTF8Encoding($false)))
                $manifest.Acceleration='cpu'
                & $wsl --distribution $manifest.DistroName --user root --exec /usr/bin/test -d /opt/shtab-ai-021
                if ($LASTEXITCODE -eq 0) { Guest /bin/touch /opt/shtab-ai-021/native-cpu-applied }
            }
        }
        Sync-LAN $ip
        if ($mode -eq 'cpu' -and $fallback -and $fallback.mode -eq 'cpu') {
            Guest /bin/touch /opt/shtab-ai-021/native-cpu-applied
        }
        Sync-GuestConfig $gateway
        Write-State $ip $gateway
        if ($keepalive.HasExited) { throw 'WSL keepalive stopped.' }
      } catch {
        Add-Content -LiteralPath (Join-Path $root 'runtime-error.log') -Value ((Get-Date -Format o) + ' ' + $_.Exception.Message)
      }
        Start-Sleep -Seconds 15
    }
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    if ($keepalive -and -not $keepalive.HasExited) { Stop-Process -Id $keepalive.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath (Join-Path $root 'runtime-state.json') -Force -ErrorAction SilentlyContinue
}


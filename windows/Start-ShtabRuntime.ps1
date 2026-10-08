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
function Guest {
    & $wsl --distribution $manifest.DistroName --user root --exec @args
    if ($LASTEXITCODE -ne 0) { throw 'WSL runtime command failed.' }
}
function Write-State([string]$IP,[string]$Gateway) {
    $json = @{ IP=$IP; Gateway=$Gateway; Endpoint=("http://${Gateway}:11435"); Ready=$true } | ConvertTo-Json
    $temporary = Join-Path $root 'runtime-state.tmp'
    [IO.File]::WriteAllText($temporary,$json,(New-Object Text.UTF8Encoding($false)))
    Move-Item -LiteralPath $temporary -Destination (Join-Path $root 'runtime-state.json') -Force
}
try {
    $keepalive = Start-Process -FilePath $wsl -ArgumentList ('--distribution ' + $manifest.DistroName + ' --user root --exec /bin/sleep infinity') -WindowStyle Hidden -PassThru
    while ($true) {
        $ip = ((Guest /bin/hostname -I | Out-String).Trim() -split '\s+' | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' }) | Select-Object -First 1
        $route = (Guest /sbin/ip -4 route show default | Out-String).Trim()
        if ($route -notmatch '^default via (\d+\.\d+\.\d+\.\d+)') { throw 'WSL NAT gateway not found. This installer requires WSL NAT networking.' }
        $gateway = $Matches[1]
        if (-not $ip) { throw 'WSL address is unavailable.' }
        if ($ip -ne $lastIP -or $gateway -ne $lastGateway -or -not $server -or $server.HasExited) {
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
            if ($manifest.Acceleration -eq 'cpu') {
                $env:CUDA_VISIBLE_DEVICES = '-1'; $env:HIP_VISIBLE_DEVICES = '-1'; $env:ROCR_VISIBLE_DEVICES = '-1'
            } elseif ($manifest.Acceleration -eq 'amd') { $env:CUDA_VISIBLE_DEVICES = '-1' }
            elseif ($manifest.Acceleration -eq 'nvidia') { $env:HIP_VISIBLE_DEVICES = '-1'; $env:ROCR_VISIBLE_DEVICES = '-1' }
            $server = Start-Process -FilePath $ollama -ArgumentList 'serve' -WorkingDirectory (Split-Path $ollama) -WindowStyle Hidden -RedirectStandardOutput (Join-Path $root 'ollama.log') -RedirectStandardError (Join-Path $root 'ollama-error.log') -PassThru
            $ready = $false
            for ($attempt=0; $attempt -lt 30; $attempt++) {
                if ($server.HasExited) { throw 'Native Ollama stopped. See ollama-error.log.' }
                try { $null = Invoke-RestMethod -Uri ("http://${gateway}:11435/api/version") -TimeoutSec 2; $ready=$true; break } catch { Start-Sleep -Seconds 2 }
            }
            if (-not $ready) { throw 'Native Ollama did not become ready.' }
            if ($manifest.Network) {
                Start-Service iphlpsvc
                $listen = $manifest.LANAddress; $port = $manifest.HTTPSPort
                & netsh.exe interface portproxy set v4tov4 listenaddress=$listen listenport=$port connectaddress=$ip connectport=$port | Out-Null
                if ($LASTEXITCODE -ne 0) {
                    & netsh.exe interface portproxy add v4tov4 listenaddress=$listen listenport=$port connectaddress=$ip connectport=$port | Out-Null
                    if ($LASTEXITCODE -ne 0) { throw 'Cannot configure LAN forwarding.' }
                }
            }
            # A WSL address can change after restart. Keep the installed endpoint current.
            $update = 'import os; from pathlib import Path; p=Path("/opt/shtab-ai-021/.env"); endpoint="http://' + $gateway + ':11435"; lines=p.read_text().splitlines(); old=next((x.split("=",1)[1] for x in lines if x.startswith("SHTAB_OLLAMA_ENDPOINT=")),""); temp=p.with_suffix(".native.tmp"); temp.write_text("\n".join([x for x in lines if not x.startswith("SHTAB_OLLAMA_ENDPOINT=")]+["SHTAB_OLLAMA_ENDPOINT="+endpoint])+"\n"); temp.chmod(0o600); os.replace(temp,p); print("CHANGED" if old!=endpoint else "SAME")'
            & $wsl --distribution $manifest.DistroName --user root --exec /usr/bin/test -f /opt/shtab-ai-021/.env
            if ($LASTEXITCODE -eq 0) {
                $changed = (Guest /usr/bin/python3 -c $update | Out-String).Trim()
                $status = (Guest /bin/bash -c 'cat /var/lib/shtab-ai-021/status 2>/dev/null || true' | Out-String).Trim()
                if ($changed -eq 'CHANGED' -and $status -eq 'READY_FOR_ADMIN') {
                    Guest /bin/systemctl start docker
                    Guest /bin/bash /opt/shtab-ai-021/scripts/dc.sh up -d --no-deps web meeting-worker llm-worker brief-worker
                }
            }
            $lastIP=$ip; $lastGateway=$gateway
            Write-State $ip $gateway
        }
        if ($keepalive.HasExited) { throw 'WSL keepalive stopped.' }
        Start-Sleep -Seconds 15
    }
} finally {
    if ($server -and -not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    if ($keepalive -and -not $keepalive.HasExited) { Stop-Process -Id $keepalive.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item -LiteralPath (Join-Path $root 'runtime-state.json') -Force -ErrorAction SilentlyContinue
}

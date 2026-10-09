#Requires -Version 5.1
#Requires -RunAsAdministrator
[CmdletBinding()]
param(
    [ValidatePattern('^ShtabAI-[A-Za-z0-9-]+$')][string]$DistroName = 'ShtabAI-021',
    [ValidateSet('ask','cpu','nvidia','amd')][string]$Acceleration = 'ask',
    [ValidateSet('ask','local','lan')][string]$Access = 'ask',
    [ValidateRange(1024,65535)][int]$HTTPSPort = 8445,
    [ValidatePattern('^(main|[a-f0-9]{40})$')][string]$Revision = 'main',
    [string]$InstallDir = ''
)
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Invoke-WSL {
    & $script:wsl @args
    if ($LASTEXITCODE -ne 0) { throw "WSL command failed (code $LASTEXITCODE). Installation data preserved." }
}
function Invoke-Guest {
    Invoke-WSL --distribution $DistroName --user root --exec @args
}
function Test-WSLInstalled {
    # Windows PowerShell 5.1 turns redirected native stderr into errors.
    # Missing WSL is an expected probe result; use its exit code instead.
    $savedPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $null = & $script:wsl --version 2>$null
        $versionExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $savedPreference
    }
    return ($versionExitCode -eq 0)
}
function Quote-Shell([string]$Value) {
    $q = [string][char]39
    return $q + $Value.Replace($q,($q + [char]34 + $q + [char]34 + $q)) + $q
}
function Write-UTF8([string]$Path,[string]$Value) {
    [IO.File]::WriteAllText($Path,$Value.Replace("`r`n","`n"),(New-Object Text.UTF8Encoding($false)))
}
function Read-Distros {
    $result = & $script:wsl --list --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing WSL distributions.' }
    return @($result | ForEach-Object { ($_ -replace "`0",'').Trim() } | Where-Object { $_ })
}
function Verify-Package([string]$Root) {
    foreach ($line in Get-Content -LiteralPath (Join-Path $Root 'SHA256SUMS') -Encoding UTF8) {
        if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Invalid checksum manifest.' }
        $expected = $Matches[1]; $relative = $Matches[2]
        if ($relative -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Unsafe package path.' }
        $actual = (Get-FileHash -LiteralPath (Join-Path $Root $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Checksum mismatch: $relative" }
    }
}
$os = Get-CimInstance Win32_OperatingSystem
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') { throw 'Windows x64 is required.' }
if ($os.ProductType -eq 1 -and [int]$os.BuildNumber -lt 19045) { throw 'Requires Windows 10 22H2 or Windows 11 (Home/Pro/Enterprise/Education).' }
if ($os.ProductType -ne 1) { throw 'This edition supports Windows 10/11 Home/Pro; Windows Server is excluded.' }
if ((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory -lt 16106127360) { throw 'At least 16 GB host RAM is required; 24 GB or more is recommended.' }
if ([Environment]::ProcessorCount -lt 4) { throw 'At least 4 logical CPU cores are required.' }
$script:wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
if (-not [Environment]::Is64BitProcess) { $script:wsl = Join-Path $env:SystemRoot 'Sysnative\wsl.exe' }
$restart = $false
foreach ($name in @('Microsoft-Windows-Subsystem-Linux','VirtualMachinePlatform')) {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName $name
    if ($feature.State -ne 'Enabled') {
        $result = Enable-WindowsOptionalFeature -Online -FeatureName $name -All -NoRestart
        $restart = $restart -or $result.RestartNeeded
    }
}
if ($restart) { Write-Host 'WSL components enabled. Restart Windows and repeat the same installation command.' -ForegroundColor Yellow; return }
if (-not (Test-Path $script:wsl)) { throw 'WSL executable is unavailable. Restart Windows and repeat installation.' }
if (-not (Test-WSLInstalled)) {
    Write-Host 'Installing WSL runtime (no default Linux distribution)...' -ForegroundColor Cyan
    Invoke-WSL --install --no-distribution --web-download
    Write-Host 'WSL installed. Restart Windows and repeat installation.' -ForegroundColor Yellow
    return
}
Invoke-WSL --update --web-download
if ($DistroName -in @(Read-Distros)) { throw "Distribution $DistroName already exists. Use the uninstaller before a clean installation." }
if (Get-NetTCPConnection -LocalPort $HTTPSPort -State Listen -ErrorAction SilentlyContinue) { throw "Windows port $HTTPSPort is occupied." }
if (Get-NetTCPConnection -LocalPort 18093 -State Listen -ErrorAction SilentlyContinue) { throw 'Windows port 18093 is occupied.' }
if (Get-NetTCPConnection -LocalPort 11435 -State Listen -ErrorAction SilentlyContinue) { throw 'Windows port 11435 is occupied; native Ollama needs its own port.' }
$defaultRoot = Join-Path $env:LOCALAPPDATA ('ShtabAI\' + $DistroName)
if (-not $InstallDir) {
    Get-Volume | Where-Object DriveLetter | Select-Object -Property @('DriveLetter','FileSystem','SizeRemaining') | Format-Table -AutoSize | Out-Host
    $InstallDir = Read-Host ("Installation folder (for example D:\Apps\$DistroName) [$defaultRoot]")
    if (-not $InstallDir) { $InstallDir=$defaultRoot }
}
if ($InstallDir -notmatch '^[A-Za-z]:\\' -or $InstallDir -match '["\r\n]') { throw 'Choose an absolute local drive path.' }
$root = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
$driveLetter = [IO.Path]::GetPathRoot($root).Substring(0,1)
$volume = Get-Volume -DriveLetter $driveLetter -ErrorAction Stop
if ($volume.FileSystem -ne 'NTFS' -or $volume.DriveType -ne 'Fixed') { throw 'Choose a local fixed NTFS drive for WSL and models.' }
if ($root.Length -lt 4) { throw 'Choose a new application folder, not a drive root.' }
$ancestor=Split-Path $root
while ($ancestor -and -not (Test-Path $ancestor)) { $ancestor=Split-Path $ancestor }
if (-not $ancestor) { throw 'Installation parent is unavailable.' }
$checkAncestor=$ancestor
while ($checkAncestor) {
    if ((Get-Item $checkAncestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installation path must not contain junctions or links.' }
    $checkAncestor=Split-Path $checkAncestor
}
$indexPath=Join-Path (Join-Path $env:LOCALAPPDATA 'ShtabAI') ($DistroName+'.json')
if (Test-Path $indexPath) { throw 'An installation registration already exists; use the uninstaller first.' }
if ((Get-PSDrive -Name ([IO.Path]::GetPathRoot($root).Substring(0,1))).Free -lt 42949672960) { throw 'At least 40 GiB free on the installation drive is required.' }
if (Test-Path $root) { throw "Installation directory already exists: $root. Use the uninstaller first." }
$desktop = [Environment]::GetFolderPath('Desktop')
$shortcut = Join-Path $desktop ($DistroName + '.url')
if (Test-Path $shortcut) { throw 'The desktop shortcut already exists; remove the previous installation first.' }
$wslConfig = Join-Path $env:USERPROFILE '.wslconfig'
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*localhostForwarding\s*=\s*false\s*$') {
    throw 'WSL localhostForwarding is disabled in .wslconfig. Enable it before installation.'
}
if ((Test-Path $wslConfig) -and (Get-Content $wslConfig -Raw) -match '(?im)^\s*networkingMode\s*=\s*(mirrored|virtioproxy|none)\s*$') {
    throw 'This release requires WSL NAT mode. Existing .wslconfig is preserved; select NAT before installing.'
}
Write-Host 'Detected Windows display adapters:'
Get-CimInstance Win32_VideoController | Select-Object -Property @('Name','DriverVersion') | Format-Table -AutoSize | Out-Host
if ($Acceleration -eq 'ask') {
    Write-Host '1 - CPU. 2 - NVIDIA (Ollama + Whisper). 3 - AMD Radeon (native Ollama; Whisper on CPU).'
    $choice = Read-Host 'Acceleration [1]'
    if ($choice -in @('','1')) { $Acceleration = 'cpu' }
    elseif ($choice -eq '2') { $Acceleration = 'nvidia' }
    elseif ($choice -eq '3') { $Acceleration = 'amd' }
    else { throw 'Invalid acceleration selection.' }
}
if ($Access -eq 'ask') {
    $answer = Read-Host 'Access: 1 - this PC only; 2 - local network [2]'
    if ($answer -in @('','2')) { $Access='lan' } elseif ($answer -eq '1') { $Access='local' } else { throw 'Invalid access selection.' }
}
$lanAddress = ''
if ($Access -eq 'lan') {
    $adapters = @(Get-NetIPConfiguration | Where-Object { $_.IPv4DefaultGateway -and $_.NetAdapter.Status -eq 'Up' -and $_.IPv4Address } | ForEach-Object {
        [pscustomobject]@{Interface=$_.InterfaceAlias; IP=$_.IPv4Address[0].IPAddress; Index=$_.InterfaceIndex}
    })
    if (-not $adapters.Count) { throw 'No active LAN interface with an IPv4 gateway. Select local access.' }
    for ($i=0; $i -lt $adapters.Count; $i++) { Write-Host "$($i+1) - $($adapters[$i].Interface) / $($adapters[$i].IP)" }
    $selected = Read-Host 'Select LAN interface [1]'
    if (-not $selected) { $selected='1' }
    $number=0
    if (-not [int]::TryParse($selected,[ref]$number) -or $number -lt 1 -or $number -gt $adapters.Count) { throw 'Invalid LAN interface.' }
    $lanAddress=$adapters[$number-1].IP
    $profile = Get-NetConnectionProfile -InterfaceIndex $adapters[$number-1].Index -ErrorAction SilentlyContinue
    if ($profile -and $profile.NetworkCategory -eq 'Public') {
        throw 'Selected network is Public. Set the trusted office/home network to Private, or select local-only access.'
    }
}
if ($Revision -eq 'main') {
    $head = Invoke-RestMethod -Uri 'https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/main' -Headers @{ 'User-Agent'='ShtabAI-Installer' }
    $Revision = $head.sha
}
if ($Revision -notmatch '^[a-f0-9]{40}$') { throw 'Cannot resolve a fixed application revision.' }
$work = Join-Path $ancestor ('shtab-wsl-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
    $archive = Join-Path $work 'source.zip'
    Write-Host "Downloading Shtab.AI revision $Revision"
    Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/wlfyalex-afk/shtab-ai-install/archive/$Revision.zip" -OutFile $archive
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Unsafe archive path.' }
        }
    } finally { $zip.Dispose() }
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $work 'source')
    $roots = @(Get-ChildItem (Join-Path $work 'source') -Directory)
    if ($roots.Count -ne 1) { throw 'Unexpected source archive layout.' }
    $package = $roots[0].FullName
    Verify-Package $package
    $image = Join-Path $work 'ubuntu.wsl'
    $imageName = 'ubuntu-24.04.5-wsl-amd64.wsl'
    $imageBase = 'https://releases.ubuntu.com/24.04/'
    $sums = Join-Path $work 'Ubuntu-SHA256SUMS'
    Invoke-WebRequest -UseBasicParsing -Uri ($imageBase + 'SHA256SUMS') -OutFile $sums
    $text = [IO.File]::ReadAllText($sums,[Text.Encoding]::UTF8)
    $matches = [regex]::Matches($text,('(?im)^([a-f0-9]{64})[ \t]+\*?' + [regex]::Escape($imageName) + '[ \t]*\r?$'))
    if ($matches.Count -ne 1) { throw 'Ubuntu WSL image checksum is unavailable.' }
    Write-Host 'Downloading Ubuntu 24.04 WSL image...'
    Invoke-WebRequest -UseBasicParsing -Uri ($imageBase + $imageName) -OutFile $image
    if ((Get-FileHash $image -Algorithm SHA256).Hash.ToLowerInvariant() -ne $matches[0].Groups[1].Value.ToLowerInvariant()) { throw 'Ubuntu image checksum mismatch.' }
    New-Item -ItemType Directory $root -Force | Out-Null
    $manifest = [ordered]@{ Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root; Revision=$Revision; HTTPSPort=$HTTPSPort; Shortcut=$shortcut; TaskName=('ShtabAI-' + $DistroName + '-Start'); CertificateThumbprint=''; WSLConfigCreated=$false; WSLConfigText=''; Acceleration=$Acceleration; Network=($Access -eq 'lan'); LANAddress=$lanAddress; LANRule=('ShtabAI-' + $DistroName + '-LAN') }
    $manifestPath = Join-Path $root 'installation.json'
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    New-Item -ItemType Directory (Split-Path $indexPath) -Force | Out-Null
    Write-UTF8 $indexPath (@{Product='ShtabAI'; Backend='WSL2'; DistroName=$DistroName; Root=$root} | ConvertTo-Json)
    $backupPath = if ($root -eq $defaultRoot) { Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'ShtabAI-Backups' } else { $root+'-Backups' }
    New-Item -ItemType Directory -Path $backupPath -Force | Out-Null
    $manifest.Add('BackupPath',$backupPath)
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    if (-not (Test-Path $wslConfig)) {
        $memoryGB = [Math]::Max(10,[Math]::Floor((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1073741824)-6)
        $cores = [Environment]::ProcessorCount
        $manifest.WSLConfigText = "[wsl2]`nmemory=${memoryGB}GB`nprocessors=$cores`nswap=4GB`nlocalhostForwarding=true`nnetworkingMode=nat`n"
        Write-UTF8 $wslConfig $manifest.WSLConfigText
        $manifest.WSLConfigCreated = $true
        Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        Write-Host "Created WSL resource limit: $memoryGB GB RAM, $cores CPU. Existing WSL distributions are not stopped."
    }
    Invoke-WSL --import $DistroName (Join-Path $root 'distro') $image --version 2
    Invoke-Guest /bin/bash -c 'printf "[boot]\nsystemd=true\n" > /etc/wsl.conf'
    Invoke-WSL --terminate $DistroName
    $deadline = (Get-Date).AddMinutes(2)
    do {
        $pidOne = (Invoke-Guest /bin/cat /proc/1/comm | Out-String).Trim()
        if ($pidOne -eq 'systemd') { break }
        Start-Sleep -Seconds 3
    } while ((Get-Date) -lt $deadline)
    if ($pidOne -ne 'systemd') { throw 'WSL systemd did not start. Update WSL, then remove this test installation and retry.' }
    if ($Acceleration -eq 'nvidia') {
        & $script:wsl --distribution $DistroName --user root --exec /usr/lib/wsl/lib/nvidia-smi -L
        if ($LASTEXITCODE -ne 0) {
            $fallback = Read-Host 'NVIDIA unavailable in WSL. Type CPU to continue without GPU, or Enter to stop'
            if ($fallback -cne 'CPU') { throw 'GPU check failed. Installation data preserved for removal.' }
            $Acceleration = 'cpu'; $manifest.Acceleration = 'cpu'
            Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
        }
    }
    Write-Host 'Downloading native Ollama for Windows (including GPU libraries)...'
    $ollamaZip = Join-Path $work 'ollama.zip'
    Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64.zip' -OutFile $ollamaZip
    if ((Get-FileHash $ollamaZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne '428c94622a04764b318ddf13a061898edf69e32ffa896f638ed6015fd3f33288') { throw 'Native Ollama checksum mismatch.' }
    $ollamaDir = Join-Path $root 'ollama'
    Expand-Archive -LiteralPath $ollamaZip -DestinationPath $ollamaDir
    if ($Acceleration -eq 'amd') {
        $rocmZip = Join-Path $work 'ollama-rocm.zip'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://github.com/ollama/ollama/releases/download/v0.34.1/ollama-windows-amd64-rocm.zip' -OutFile $rocmZip
        if ((Get-FileHash $rocmZip -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'a290510b3ee3b743de54eb3fbae99b69f19a49485f42ce6bcf4a1a6f86e4ba01') { throw 'AMD library checksum mismatch.' }
        Expand-Archive -LiteralPath $rocmZip -DestinationPath $ollamaDir -Force
    }
    Copy-Item -LiteralPath (Join-Path $package 'windows\Start-ShtabRuntime.ps1') -Destination $root
    Copy-Item -LiteralPath (Join-Path $package 'windows\Manage-ShtabAI.ps1') -Destination $root
    $runtime = Join-Path $root 'Start-ShtabRuntime.ps1'
    $powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument ('-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $runtime + '" -ManifestPath "' + $manifestPath + '"')
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType S4U -RunLevel Highest
    $triggers = @((New-ScheduledTaskTrigger -AtStartup),(New-ScheduledTaskTrigger -AtLogOn -User $user))
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    if ($manifest.Network) {
        New-NetFirewallRule -Name $manifest.LANRule -DisplayName ('ShtabAI LAN ' + $DistroName) -Group 'ShtabAI' -Direction Inbound -Action Allow -Protocol TCP -LocalAddress $lanAddress -LocalPort $HTTPSPort -RemoteAddress LocalSubnet -Profile @('Private','Domain') | Out-Null
    }
    Register-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\' -Action $action -Trigger $triggers -Principal $principal -Settings $settings | Out-Null
    Start-ScheduledTask -TaskName $manifest.TaskName -TaskPath '\'
    $deadline=(Get-Date).AddMinutes(3)
    $stateFile=Join-Path $root 'runtime-state.json'
    while (-not (Test-Path $stateFile)) {
        if ((Get-Date) -gt $deadline) { throw 'Native Ollama startup timed out. Check the task and ollama-error.log.' }
        Start-Sleep -Seconds 3
    }
    $runtimeState=Get-Content $stateFile -Raw | ConvertFrom-Json
    $guestMode = if ($Acceleration -eq 'nvidia') { 'nvidia' } else { 'cpu' }
    $httpsHost = if ($manifest.Network) { $lanAddress } else { 'localhost' }
    $guestArchive = (Invoke-Guest wslpath -u $archive | Out-String).Trim()
    $setup = 'set -euo pipefail; apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y unzip python3 curl ca-certificates openssl; work=$(mktemp -d); trap ''rm -rf "$work"'' EXIT; unzip -q ' + (Quote-Shell $guestArchive) + ' -d "$work"; cd "$work"/*; sha256sum --quiet -c SHA256SUMS; SHTAB_EXTERNAL_OLLAMA_ENDPOINT=' + (Quote-Shell $runtimeState.Endpoint) + ' SHTAB_WINDOWS_ACCELERATION=' + $Acceleration + ' SHTAB_HTTPS_PORT=' + $HTTPSPort + ' bash install.sh ' + $httpsHost + ' ' + $guestMode
    Invoke-Guest /bin/bash -c $setup
    $guestBackups = (Invoke-Guest wslpath -u $backupPath | Out-String).Trim()
    Invoke-Guest /bin/bash -c ('printf ''%s\n'' ' + (Quote-Shell $guestBackups) + ' > /opt/shtab-ai-021/backup-directory')
    $started = Get-Date
    $deadline = $started.AddHours(3)
    $lastStatus = ''
    do {
        $status = (Invoke-Guest /bin/bash -c 'cat /var/lib/shtab-ai-021/status 2>/dev/null || echo STARTING' | Out-String).Trim()
        $elapsed = ((Get-Date) - $started).ToString('hh\:mm\:ss')
        Write-Progress -Id 1 -Activity 'Shtab.AI installation' -Status "$status | elapsed $elapsed" -PercentComplete -1
        if ($lastStatus -ne $status) { Invoke-Guest /opt/shtab-ai-021/shtabctl progress --once; $lastStatus=$status }
        if ($status -eq 'READY_FOR_ADMIN') { break }
        if ($status -like 'FAILED*' -or (Get-Date) -gt $deadline) {
            Invoke-Guest /bin/journalctl -u shtab-ai-install -n 80 --no-pager
            throw "Installation did not complete: $status. Use the uninstaller for a clean retry."
        }
        Start-Sleep -Seconds 10
    } while ($true)
    Invoke-Guest /opt/shtab-ai-021/shtabctl certificate
    $cert = Join-Path $root 'shtab-ai-root.crt'
    $guestCert = (Invoke-Guest wslpath -u $cert | Out-String).Trim()
    Invoke-Guest /bin/cp /opt/shtab-ai-021/shtab-ai-root.crt $guestCert
    $imported = Import-Certificate -FilePath $cert -CertStoreLocation Cert:\CurrentUser\Root
    $manifest.CertificateThumbprint = $imported.Thumbprint
    Write-UTF8 $manifestPath ($manifest | ConvertTo-Json)
    Write-Host 'Create the first administrator (password is entered privately):'
    Invoke-Guest /opt/shtab-ai-021/shtabctl bootstrap
    $url = "https://localhost:$HTTPSPort/login"
    Write-UTF8 $shortcut ("[InternetShortcut]`nURL=$url`n")
    $shell=New-Object -ComObject WScript.Shell
    $managerLink=$shell.CreateShortcut((Join-Path $desktop ($DistroName+'-Manager.lnk')))
    $managerLink.TargetPath=$powershell
    $managerLink.Arguments='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $root 'Manage-ShtabAI.ps1')+'" -ManifestPath "'+$manifestPath+'"'
    $managerLink.WorkingDirectory=$root
    $managerLink.Description='Shtab.AI: backups, restore, status and service control'
    $managerLink.Save()
    # Check the Windows-to-WSL path with normal certificate validation.
    $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec 15
    if ($response.StatusCode -ne 200) { throw 'The Windows login page check failed.' }
    Write-Host "Shtab.AI ready: $url | acceleration: $Acceleration" -ForegroundColor Green
    if ($manifest.Network) {
        $networkURL = "https://${lanAddress}:$HTTPSPort/login"
        $networkResponse = Invoke-WebRequest -UseBasicParsing -Uri $networkURL -TimeoutSec 15
        if ($networkResponse.StatusCode -ne 200) { throw 'LAN address check failed.' }
        Write-Host "LAN: $networkURL. On other PCs trust the public certificate: $cert"
        Write-Host 'A remote PC login/upload test is still required; this local test cannot prove a remote firewall or browser configuration.'
    }
    Start-Process $url
} finally {
    Write-Progress -Id 1 -Activity 'Shtab.AI installation' -Completed
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}

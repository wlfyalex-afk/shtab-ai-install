#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
Create a NEW standard Ubuntu 24.04 VM using native Hyper-V on Windows Pro or Server.
Never deletes an existing VM. First Windows/Hyper-V acceptance test is required.
#>
[CmdletBinding()]
param(
    [ValidatePattern('^[a-z](?:[a-z0-9-]{0,39}[a-z0-9])?$')][string]$VMName = 'shtab-ai-test',
    [ValidateRange(4,32)][int]$CPUs = 4,
    [ValidateRange(12,128)][int]$MemoryGB = 12,
    [ValidateRange(80,2048)][int]$DiskGB = 120,
    [string]$PackageZip = '',
    [string]$VMSwitchName = '',
    [string]$VMRoot = '',
    [ValidateRange(1024,65535)][int]$HTTPSPort = 8443,
    [ValidatePattern('^(main|[a-f0-9]{40})$')][string]$Revision = 'main'
)
$ErrorActionPreference = 'Stop'
$httpsName = "$VMName.local"
$refreshSource = Join-Path $PSScriptRoot 'Refresh-ShtabConnection.ps1'

[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not [Environment]::Is64BitOperatingSystem -or $env:PROCESSOR_ARCHITECTURE -eq 'ARM64') {
    throw 'Windows x64 is required.'
}
$os = Get-CimInstance Win32_OperatingSystem
if ([version]$os.Version -lt [version]'10.0.17763') { throw 'Requires Windows 10/11 Pro or Windows Server 2019 or later.' }
if ($os.ProductType -eq 1 -and $os.OperatingSystemSKU -notin @(48,49,161,162,164,165)) {
    throw 'Requires Windows Pro. Windows Home, Enterprise and Education are not supported by this installer.'
}
if ($os.ProductType -ne 1) {
    $feature = Get-WindowsFeature -Name Hyper-V
    if (-not $feature.Installed) {
        $result = Install-WindowsFeature -Name Hyper-V -IncludeManagementTools
        if (-not $result.Success) { throw 'Hyper-V role installation failed.' }
        Write-Host 'Hyper-V installed. Restart Windows, then repeat the same installation command.'
        return
    }
} else {
    $feature = Get-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V
    if ($feature.State -ne 'Enabled') {
        Enable-WindowsOptionalFeature -Online -FeatureName Microsoft-Hyper-V -All -NoRestart | Out-Null
        Write-Host 'Hyper-V installed. Restart Windows, then repeat the same installation command.'
        return
    }
}
if (-not (Get-CimInstance Win32_ComputerSystem).HypervisorPresent) {
    throw 'Hyper-V is not running. Restart Windows and check BIOS virtualization.'
}
$ram = (Get-CimInstance Win32_OperatingSystem).FreePhysicalMemory / 1048576
if ($ram -lt ($MemoryGB + 3)) { throw "At least $($MemoryGB + 3) GB of free host RAM is required." }
function Invoke-ShtabVM {
    Invoke-ShtabHyperV @args
}
Write-Host 'Using native Hyper-V with a standard Ubuntu cloud image.'
$backend = Join-Path $env:TEMP ('Shtab-NativeHyperV-' + [guid]::NewGuid().ToString('N') + '.ps1')
Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/$Revision/windows/Native-HyperV.ps1" -OutFile $backend
try { . $backend; Initialize-ShtabHyperV } finally { Remove-Item -LiteralPath $backend -Force -ErrorAction SilentlyContinue }
$inventory = (Invoke-ShtabVM list --format json | Out-String | ConvertFrom-Json)
if (@($inventory.list | Where-Object name -eq $VMName).Count -gt 0) {
    throw "VM '$VMName' already exists. Choose a new -VMName; nothing was deleted."
}
if (Get-Command Get-VM -ErrorAction SilentlyContinue) {
    if (Get-VM -Name $VMName -ErrorAction SilentlyContinue) { throw "Hyper-V VM '$VMName' already exists." }
}
if (Get-NetTCPConnection -LocalPort $HTTPSPort -State Listen -ErrorAction SilentlyContinue) {
    throw "Windows port $HTTPSPort is occupied. Specify another -HTTPSPort."
}
$hostsPath = Join-Path $env:SystemRoot 'System32\drivers\etc\hosts'
if ((Get-Content $hostsPath | Where-Object { ($_ -split '#')[0] -match ('(^|\s)' + [regex]::Escape($httpsName) + '(\s|$)') })) {
    throw "Hostname $httpsName already exists in hosts. Choose another -VMName."
}
$work = Join-Path $env:TEMP ('shtab-setup-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $work | Out-Null
try {
    $downloadKey = Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
    $downloadValue = $downloadKey.'{374DE290-123F-4565-9164-39C4925E467B}'
    $downloads = if ($downloadValue) { [Environment]::ExpandEnvironmentVariables($downloadValue) } else { Join-Path $env:USERPROFILE 'Downloads' }
    if (-not $downloads) { $downloads = Join-Path $env:USERPROFILE 'Downloads' }
    New-Item -ItemType Directory -Force $downloads | Out-Null
    $archive = Join-Path $downloads ('shtab-ai-' + [guid]::NewGuid().ToString('N') + '.zip')
    if ($PackageZip) {
        Copy-Item -LiteralPath (Resolve-Path $PackageZip).Path -Destination $archive
    } else {
        Write-Host 'Downloading the public Shtab.AI test package.'
        Invoke-WebRequest -UseBasicParsing -Uri "https://github.com/wlfyalex-afk/shtab-ai-install/archive/$Revision.zip" -OutFile $archive
    }
    # Validate the package structure before creating a VM.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $zip = [IO.Compression.ZipFile]::OpenRead($archive)
    try {
        $installers = @($zip.Entries | Where-Object FullName -Match '^[^/]+/install\.sh$')
        if ($installers.Count -ne 1) { throw 'Expected one repository root containing install.sh.' }
        foreach ($entry in $zip.Entries) {
            if ($entry.FullName -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Unsafe ZIP path.' }
        }
    } finally { $zip.Dispose() }
    $expanded = Join-Path $downloads ('shtab-ai-setup-' + [guid]::NewGuid().ToString('N'))
    Expand-Archive -LiteralPath $archive -DestinationPath $expanded
    $roots = @(Get-ChildItem -LiteralPath $expanded -Directory)
    if ($roots.Count -ne 1) { throw 'Unexpected package root.' }
    $packageRoot = $roots[0].FullName
    foreach ($line in Get-Content -LiteralPath (Join-Path $packageRoot 'SHA256SUMS')) {
        if ($line -notmatch '^([a-f0-9]{64})  (.+)$') { throw 'Invalid SHA256SUMS entry.' }
        $expected = $Matches[1]; $relative = $Matches[2]
        if ($relative -match '(^/|(^|/)\.\.(/|$)|\\|:)') { throw 'Unsafe checksum path.' }
        $actual = (Get-FileHash -LiteralPath (Join-Path $packageRoot $relative) -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $expected) { throw "Package checksum mismatch: $relative" }
    }
    $refreshSource = Join-Path $packageRoot 'windows\Refresh-ShtabConnection.ps1'
    if (-not (Test-Path $refreshSource)) { throw 'Package is missing the connection helper.' }
    Write-Host "Creating $VMName : Ubuntu 24.04, $CPUs CPU, $MemoryGB GB RAM, $DiskGB GB disk."
    Invoke-ShtabVM launch 24.04 --name $VMName --cpus $CPUs --memory "${MemoryGB}G" --disk "${DiskGB}G" --timeout 900
    Invoke-ShtabVM transfer $archive "${VMName}:/home/ubuntu/shtab.zip"
    # No GitHub credential is transferred to the guest.
    $guestScript = @'
set -euo pipefail
test ! -e /opt/shtab-ai-021/installation-created
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y unzip python3 curl ca-certificates openssl
work=$(mktemp -d /var/tmp/shtab-source.XXXXXX)
trap 'rm -rf "$work" /home/ubuntu/shtab.zip' EXIT
unzip -q /home/ubuntu/shtab.zip -d "$work"
mapfile -t roots < <(find "$work" -mindepth 1 -maxdepth 1 -type d)
test "${#roots[@]}" = 1
cd "${roots[0]}"
sha256sum --quiet -c SHA256SUMS
bash install.sh __HTTPS_NAME__
'@
    $guestScript = $guestScript.Replace('__HTTPS_NAME__', $httpsName)
    $scriptFile = Join-Path $work 'guest-install.sh'
    [IO.File]::WriteAllText($scriptFile, $guestScript.Replace("`r`n","`n"), (New-Object Text.UTF8Encoding($false)))
    Invoke-ShtabVM transfer $scriptFile "${VMName}:/home/ubuntu/guest-install.sh"
    Invoke-ShtabVM exec $VMName '--' sudo bash '/home/ubuntu/guest-install.sh'
    Invoke-ShtabVM exec $VMName '--' rm '/home/ubuntu/guest-install.sh'
    Write-Host 'Background installation started. Waiting for completion (up to 2 hours).'
    $deadline = (Get-Date).AddHours(2)
    do {
        $status = (Invoke-ShtabVM exec $VMName '--' sudo bash -c 'if [ -f /var/lib/shtab-ai-021/status ]; then cat /var/lib/shtab-ai-021/status; else echo STARTING; fi' | Out-String).Trim()
        Write-Host "Shtab.AI: $status"
        if ($status -eq 'READY_FOR_ADMIN') { break }
        if ($status -like 'FAILED*') {
            Invoke-ShtabVM exec $VMName '--' sudo journalctl -u shtab-ai-install -n 100 --no-pager
            throw "Shtab.AI installation failed: $status. VM preserved; diagnostic log printed above."
        }
        if ((Get-Date) -gt $deadline) {
            Invoke-ShtabVM exec $VMName '--' sudo journalctl -u shtab-ai-install -n 100 --no-pager
            throw 'Waiting timed out; installation remains running. Diagnostic log printed above.'
        }
        Start-Sleep -Seconds 10
    } while ($true)
    Invoke-ShtabVM info $VMName
    # Stable browser URL; a per-user elevated task refreshes the VM IP every minute.
    $connectionDir = Join-Path $env:LOCALAPPDATA "ShtabAI\$VMName"
    New-Item -ItemType Directory -Force $connectionDir | Out-Null
    $refresh = Join-Path $connectionDir 'Refresh-ShtabConnection.ps1'
    Copy-Item $refreshSource $refresh
    Add-Content -LiteralPath $hostsPath -Value ([Environment]::NewLine + '127.0.0.1 ' + $httpsName + ' # ShtabAI ' + $VMName) -Encoding ASCII
    & $refresh -Backend HyperV -VMName $VMName -Port $HTTPSPort
    $taskArguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $refresh + '" -Backend HyperV -VMName ' + $VMName + ' -Port ' + $HTTPSPort
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $taskArguments
    $user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    $triggers = @(
        (New-ScheduledTaskTrigger -AtLogOn -User $user),
        (New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1))
    )
    $principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
    Register-ScheduledTask -TaskName "ShtabAI-$VMName-Connection" -Action $action -Trigger $triggers -Principal $principal -Settings $settings | Out-Null
    $startAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -Command "Start-VM -Name ' + $VMName + '"')
    $startTrigger = New-ScheduledTaskTrigger -AtLogOn -User $user
    $startSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
    Register-ScheduledTask -TaskName "ShtabAI-$VMName-Start" -Action $startAction -Trigger $startTrigger -Principal $principal -Settings $startSettings | Out-Null
    Invoke-ShtabVM exec $VMName '--' sudo '/opt/shtab-ai-021/shtabctl' certificate
    Invoke-ShtabVM exec $VMName '--' sudo cp '/opt/shtab-ai-021/shtab-ai-root.crt' '/home/ubuntu/shtab-ai-root.crt'
    $certificate = Join-Path $connectionDir 'shtab-ai-root.crt'
    Invoke-ShtabVM transfer "${VMName}:/home/ubuntu/shtab-ai-root.crt" $certificate
    Import-Certificate -FilePath $certificate -CertStoreLocation Cert:\CurrentUser\Root | Out-Null
    Write-Host 'Create the first Shtab.AI administrator now:'
    Invoke-ShtabVM exec $VMName '--' sudo '/opt/shtab-ai-021/shtabctl' bootstrap
    $url = "https://${httpsName}:$HTTPSPort"
    Write-Host "Shtab.AI: $url (the VM IP can change)."
    Start-Process $url
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}




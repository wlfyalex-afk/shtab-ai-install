# Native Hyper-V backend for the common Shtab.AI installer. No Multipass calls.
function Initialize-ShtabHyperV {
    Import-Module Hyper-V -ErrorAction Stop
    $script:sshPath = (Get-Command ssh.exe -ErrorAction SilentlyContinue).Source
    if (-not $script:sshPath) {
        Write-Host 'Installing the Windows OpenSSH client automatically.'
        $capability = Add-WindowsCapability -Online -Name OpenSSH.Client~~~~0.0.1.0
        if ($capability.RestartNeeded) { throw 'OpenSSH client installed. Restart Windows and repeat the same command.' }
        $script:sshPath = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
    }
    $script:scpPath = Join-Path (Split-Path $script:sshPath) 'scp.exe'
    $script:keygenPath = Join-Path (Split-Path $script:sshPath) 'ssh-keygen.exe'
    foreach ($path in @($script:sshPath,$script:scpPath,$script:keygenPath)) {
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing OpenSSH client file: $path" }
    }
    $script:nativeStaticNetwork = $false
    if ($VMSwitchName) {
        $script:nativeSwitch = Get-VMSwitch -Name $VMSwitchName -ErrorAction Stop
    } else {
        $switches = @(Get-VMSwitch | Where-Object SwitchType -eq 'External')
        if ($switches.Count -gt 1) { throw 'Several external switches exist. Specify -VMSwitchName to select the VM network.' }
        if ($switches.Count -eq 1) {
            $script:nativeSwitch = $switches[0]
        } else {
            # No external switch: create a separate internal NAT, never touch the physical NIC.
            if (@(Get-NetNat -ErrorAction SilentlyContinue).Count -gt 0) { throw 'An existing NAT requires explicit network selection with -VMSwitchName.' }
            $script:nativeStaticNetwork = $true
            $script:nativeSubnet = ''
            $routes = @(Get-NetRoute -AddressFamily IPv4 | Where-Object DestinationPrefix -ne '0.0.0.0/0')
            foreach ($third in 240..250) {
                $candidateIP = [Net.IPAddress]::Parse("192.168.$third.1")
                $bytes = $candidateIP.GetAddressBytes()
                $value = ([uint64]$bytes[0] * 16777216) + ([uint64]$bytes[1] * 65536) + ([uint64]$bytes[2] * 256) + $bytes[3]
                $occupied = $false
                foreach ($route in $routes) {
                    $parts = $route.DestinationPrefix.Split('/')
                    $rb = [Net.IPAddress]::Parse($parts[0]).GetAddressBytes()
                    $rv = ([uint64]$rb[0] * 16777216) + ([uint64]$rb[1] * 65536) + ([uint64]$rb[2] * 256) + $rb[3]
                    $width = [Math]::Pow(2,32 - [int]$parts[1])
                    if ([Math]::Floor($value / $width) -eq [Math]::Floor($rv / $width)) { $occupied = $true; break }
                }
                if (-not $occupied) { $script:nativeSubnet = "192.168.$third"; break }
            }
            if (-not $script:nativeSubnet) { throw 'No free internal NAT subnet was found.' }
            # Network changes happen only when the VM is created, after package checks.
        }
    }
    if (-not $VMRoot) { $VMRoot = Join-Path (Get-VMHost).VirtualHardDiskPath $VMName }
    $script:nativeRoot = [IO.Path]::GetFullPath($VMRoot)
    if (Test-Path -LiteralPath $script:nativeRoot) { throw "VM directory already exists: $script:nativeRoot. Nothing was deleted." }
    $script:nativeConnection = Join-Path $env:LOCALAPPDATA "ShtabAI\$VMName"
    $script:nativeKey = Join-Path $script:nativeConnection 'hyperv-ed25519'
    $script:nativeKnownHosts = Join-Path $script:nativeConnection 'known_hosts'
    $script:nativeIP = ''
}
function Get-ShtabHyperVIP {
    if ($script:nativeStaticNetwork) { return ($script:nativeSubnet + '.2') }
    $addresses = @(Get-VMNetworkAdapter -VMName $VMName | ForEach-Object IPAddresses)
    return ($addresses | Where-Object { $_ -match '^\d+\.\d+\.\d+\.\d+$' -and $_ -notlike '127.*' -and $_ -notlike '169.254.*' } | Select-Object -First 1)
}
function Get-ShtabSSHOptions {
    return @('-i',$script:nativeKey,'-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new','-o',"UserKnownHostsFile=$script:nativeKnownHosts")
}
function ConvertTo-ShtabShellArgument([string]$Value) {
    $quote = [string][char]39
    $escaped = $quote + [char]34 + $quote + [char]34 + $quote
    return $quote + $Value.Replace($quote,$escaped) + $quote
}
function New-ShtabSeedISO([string]$Source, [string]$Destination) {
    if (-not ('ShtabIsoStream' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
public static class ShtabIsoStream {
    public static void Save(object streamObject, string path) {
        IStream input = (IStream)streamObject;
        IntPtr count = Marshal.AllocHGlobal(4);
        try {
            using (FileStream output = new FileStream(path, FileMode.CreateNew, FileAccess.Write)) {
                byte[] buffer = new byte[1024 * 1024];
                while (true) {
                    input.Read(buffer, buffer.Length, count);
                    int read = Marshal.ReadInt32(count);
                    if (read == 0) break;
                    output.Write(buffer, 0, read);
                }
            }
        } finally { Marshal.FreeHGlobal(count); }
    }
}
'@
    }
    $image = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
    $image.ChooseImageDefaultsForMediaType(12)
    $image.FileSystemsToCreate = 3 # ISO9660 and Joliet
    $image.VolumeName = 'cidata'
    $image.Root.AddTree($Source,$false)
    $result = $image.CreateResultImage()
    [ShtabIsoStream]::Save($result.ImageStream,$Destination)
}
function New-ShtabHyperV {
    # All persistent paths are reserved for this new VM; never overwrite another VM.
    if (Get-VM -Name $VMName -ErrorAction SilentlyContinue) { throw "VM '$VMName' already exists." }
    if ($script:nativeStaticNetwork) {
        $switchName = 'ShtabAI-' + $VMName
        if (Get-VMSwitch -Name $switchName -ErrorAction SilentlyContinue) { throw 'An installation switch already exists. Nothing was changed.' }
        $script:nativeSwitch = New-VMSwitch -Name $switchName -SwitchType Internal
        $adapter = Get-NetAdapter -Name "vEthernet ($switchName)"
        New-NetIPAddress -InterfaceIndex $adapter.ifIndex -IPAddress ($script:nativeSubnet + '.1') -PrefixLength 24 | Out-Null
        New-NetNat -Name $switchName -InternalIPInterfaceAddressPrefix ($script:nativeSubnet + '.0/24') | Out-Null
    }
    New-Item -ItemType Directory -Path $script:nativeRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $script:nativeConnection | Out-Null
    if (Test-Path $script:nativeKey) { throw 'An SSH key from a previous native installation exists. Nothing was overwritten.' }
    & $script:keygenPath -q -t ed25519 -f $script:nativeKey -N '""'
    if ($LASTEXITCODE -ne 0) { throw 'Cannot create the VM SSH key.' }
    $key = (Get-Content -LiteralPath ($script:nativeKey + '.pub') -Raw).Trim()
    # Disable inherited permissions; retain only current administrator and SYSTEM.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $script:nativeKey /inheritance:r /grant:r "${identity}:(F)" 'SYSTEM:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict SSH key permissions.' }
    $base = 'https://cloud-images.ubuntu.com/releases/noble/release/'
    $file = 'ubuntu-24.04-server-cloudimg-amd64-azure.vhd.tar.gz'
    $compressed = Join-Path $downloads $file
    Write-Host 'Downloading the official Ubuntu 24.04 Hyper-V disk.'
    $sums = (Invoke-WebRequest -UseBasicParsing -Uri ($base + 'SHA256SUMS')).Content
    $matches = @($sums -split "`n" | Where-Object { $_ -match ('^[a-f0-9]{64}\s+\*?' + [regex]::Escape($file) + '\s*$') })
    if ($matches.Count -ne 1) { throw 'Cannot locate the Ubuntu image SHA256.' }
    $expected = ($matches[0] -split '\s+')[0]
    if (-not (Test-Path $compressed) -or (Get-FileHash -LiteralPath $compressed -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + $file) -OutFile $compressed
    }
    if ((Get-FileHash -LiteralPath $compressed -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Ubuntu image checksum mismatch.' }
    $extract = Join-Path $script:nativeRoot 'image'
    New-Item -ItemType Directory $extract | Out-Null
    $tar = Get-Command tar.exe -ErrorAction SilentlyContinue
    if (-not $tar) { throw 'Windows tar.exe is required to unpack the Canonical disk image.' }
    $members = @(& $tar.Source -tzf $compressed)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect Ubuntu image archive.' }
    $vhdMembers = @($members | Where-Object { $_ -match '(^|/)[^/]+\.vhd$' -and $_ -notmatch '(^/|(^|/)\.\.(/|$)|\\|:)' })
    if ($vhdMembers.Count -ne 1) { throw 'Expected one Ubuntu VHD disk.' }
    & $tar.Source -xzf $compressed -C $extract $vhdMembers[0]
    if ($LASTEXITCODE -ne 0) { throw 'Cannot unpack Ubuntu disk.' }
    $vhd = Join-Path $extract $vhdMembers[0]
    $disk = Join-Path $script:nativeRoot ($VMName + '.vhdx')
    Convert-VHD -Path $vhd -DestinationPath $disk -VHDType Dynamic
    Resize-VHD -Path $disk -SizeBytes ($DiskGB * 1073741824)
    Remove-Item -LiteralPath $extract -Recurse -Force
    $seed = Join-Path $script:nativeRoot 'seed'
    New-Item -ItemType Directory $seed | Out-Null
    $userData = @"
#cloud-config
hostname: $VMName
manage_etc_hosts: true
ssh_pwauth: false
ssh_authorized_keys:
  - $key
disable_root: true
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
packages:
  - openssh-server
  - linux-cloud-tools-virtual
runcmd:
  - [systemctl, enable, --now, ssh]
"@
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $seed 'user-data'),$userData.Replace("`r`n","`n"),$utf8)
    [IO.File]::WriteAllText((Join-Path $seed 'meta-data'),"instance-id: $VMName-$([guid]::NewGuid().ToString('N'))`nlocal-hostname: $VMName`n",$utf8)
    if ($script:nativeStaticNetwork) {
        $dns = @(Get-DnsClientServerAddress -AddressFamily IPv4 | ForEach-Object ServerAddresses | Where-Object { $_ -and $_ -notlike '127.*' -and $_ -ne '0.0.0.0' } | Select-Object -Unique -First 2)
        if ($dns.Count -eq 0) { $dns = @('1.1.1.1','8.8.8.8') }
        $network = @"
version: 2
ethernets:
  primary:
    match:
      name: 'e*'
    dhcp4: false
    addresses: ['$($script:nativeSubnet).2/24']
    routes:
      - to: default
        via: $($script:nativeSubnet).1
    nameservers:
      addresses: [$($dns -join ', ')]
"@
        [IO.File]::WriteAllText((Join-Path $seed 'network-config'),$network.Replace("`r`n","`n"),$utf8)
    }
    $seedISO = Join-Path $script:nativeRoot 'seed.iso' 
    New-ShtabSeedISO $seed $seedISO
    Write-Host "Creating a native Hyper-V VM using switch '$($script:nativeSwitch.Name)'."
    $vm = New-VM -Name $VMName -Generation 2 -MemoryStartupBytes ($MemoryGB * 1073741824) -VHDPath $disk -Path $script:nativeRoot -SwitchName $script:nativeSwitch.Name
    Set-VMProcessor -VM $vm -Count $CPUs
    Set-VMMemory -VM $vm -DynamicMemoryEnabled $false
    Set-VM -VM $vm -AutomaticStartAction Start -AutomaticStopAction ShutDown
    Set-VMFirmware -VM $vm -EnableSecureBoot On -SecureBootTemplate MicrosoftUEFICertificateAuthority
    Add-VMDvdDrive -VM $vm -Path $seedISO | Out-Null
    $bootDisk = Get-VMHardDiskDrive -VM $vm | Select-Object -First 1
    Set-VMFirmware -VM $vm -FirstBootDevice $bootDisk
    Start-VM -VM $vm
    Write-Host 'Waiting for Ubuntu DHCP and SSH (up to 15 minutes).'
    $deadline = (Get-Date).AddMinutes(15)
    do {
        $script:nativeIP = Get-ShtabHyperVIP
        if ($script:nativeIP) {
            $options = Get-ShtabSSHOptions
            $savedPreference = $ErrorActionPreference
            try {
                $ErrorActionPreference = 'Continue'
                & $script:sshPath @options "ubuntu@$script:nativeIP" 'true' 2>$null
                $ready = $LASTEXITCODE -eq 0
            } finally { $ErrorActionPreference = $savedPreference }
            if ($ready) {
                & $script:sshPath @options "ubuntu@$script:nativeIP" 'sudo timeout 600 cloud-init status --wait'
                if ($LASTEXITCODE -notin @(0,2)) { throw 'Ubuntu cloud-init failed. VM preserved for diagnostics.' }
                return
            }
        }
        Write-Host 'Ubuntu is starting; waiting for cloud-init and SSH.'
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "Ubuntu SSH did not become ready. VM and disk preserved. Open Hyper-V console for '$VMName'."
}
function Invoke-ShtabHyperV {
    $operation = $args[0]
    switch ($operation) {
        'get' { 'hyperv'; return }
        'list' { @{list=@(Get-VM | ForEach-Object { @{name=$_.Name} })} | ConvertTo-Json -Depth 4; return }
        'launch' { New-ShtabHyperV; return }
        'info' { Get-VM -Name $VMName | Format-Table -Property @('Name','State'); return }
        'transfer' {
            $source = [string]$args[1]; $target = [string]$args[2]
            $prefix = $VMName + ':'
            if ($source.StartsWith($prefix)) { $source = 'ubuntu@' + $script:nativeIP + ':' + $source.Substring($prefix.Length) }
            if ($target.StartsWith($prefix)) { $target = 'ubuntu@' + $script:nativeIP + ':' + $target.Substring($prefix.Length) }
            $options = Get-ShtabSSHOptions
            & $script:scpPath @options $source $target
            if ($LASTEXITCODE -ne 0) { throw 'Native Hyper-V file transfer failed.' }
            return
        }
        'exec' {
            $command = @($args | Select-Object -Skip 3 | ForEach-Object { ConvertTo-ShtabShellArgument ([string]$_) }) -join ' '
            $options = Get-ShtabSSHOptions
            # The application bootstrap needs an interactive terminal.
            if ($command -match "'bootstrap'$") { $options += '-t' }
            & $script:sshPath @options "ubuntu@$script:nativeIP" $command
            if ($LASTEXITCODE -ne 0) { throw 'Native Hyper-V guest command failed.' }
            return
        }
        default { throw "Unsupported native Hyper-V operation: $operation" }
    }
}

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
    if (-not $VMRoot) { $VMRoot = Join-Path (Join-Path $env:SystemDrive 'ShtabAI') $VMName }
    $script:nativeRoot = [IO.Path]::GetFullPath($VMRoot)
    if (Test-Path -LiteralPath $script:nativeRoot) {
        $rootItem = Get-Item -LiteralPath $script:nativeRoot -Force
        if (-not $rootItem.PSIsContainer -or ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -or @(Get-ChildItem -LiteralPath $script:nativeRoot -Force).Count -gt 0) {
            throw "VM directory contains existing data: $script:nativeRoot. Nothing was changed."
        }
        Write-Host 'Continuing preparation in the empty VM directory from the previous attempt.'
    }
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
    return @('-F','NUL','-i',$script:nativeKey,'-o','BatchMode=yes','-o','ConnectTimeout=5','-o','StrictHostKeyChecking=accept-new','-o',"UserKnownHostsFile=$script:nativeKnownHosts")
}
function ConvertTo-ShtabShellArgument([string]$Value) {
    $quote = [string][char]39
    $escaped = $quote + [char]34 + $quote + [char]34 + $quote
    return $quote + $Value.Replace($quote,$escaped) + $quote
}
function Get-ShtabQemuImg([string]$Downloads) {
    $qemu = Join-Path $env:ProgramFiles 'qemu\qemu-img.exe'
    if (Test-Path -LiteralPath $qemu) { return $qemu }
    # Windows binaries linked by qemu.org/download. Pin both version and digest.
    $name = 'qemu-w64-setup-20260811.exe'
    $expected = '5bcf9eed634e8575a37b74f445af41a2fe4106da512d0c30c368301d4c105037fdfab40a5287367a28a957624cddebbc8c07e16c88ab6634f554cdf3d16bf543'
    $setup = Join-Path $Downloads $name
    Write-Host 'Preparing the QEMU disk converter automatically (VM runs on Hyper-V).'
    if (-not (Test-Path -LiteralPath $setup) -or (Get-FileHash -LiteralPath $setup -Algorithm SHA512).Hash.ToLowerInvariant() -ne $expected) {
        Invoke-WebRequest -UseBasicParsing -Uri ('https://qemu.weilnetz.de/w64/' + $name) -OutFile $setup
    }
    if ((Get-FileHash -LiteralPath $setup -Algorithm SHA512).Hash.ToLowerInvariant() -ne $expected) { throw 'QEMU installer checksum mismatch.' }
    # NSIS: /S is silent and /D (last argument, without quotes) selects the directory.
    $process = Start-Process -FilePath $setup -ArgumentList ('/S /D=' + (Split-Path $qemu)) -Wait -PassThru
    if ($process.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $qemu)) { throw 'Automatic installation of qemu-img failed.' }
    return $qemu
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
    New-Item -ItemType Directory -Force -Path $script:nativeRoot | Out-Null
    New-Item -ItemType Directory -Force -Path $script:nativeConnection | Out-Null
    if (Test-Path -LiteralPath $script:nativeKey) {
        if (-not (Test-Path -LiteralPath ($script:nativeKey + '.pub'))) { throw 'Existing SSH key has no public key. Nothing was overwritten.' }
        $key = (& $script:keygenPath -y -P '""' -f $script:nativeKey | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) { throw 'Cannot validate the existing VM SSH key.' }
        $storedKey = (Get-Content -LiteralPath ($script:nativeKey + '.pub') -Raw).Trim()
        if ((($key -split '\s+')[0..1] -join ' ') -ne (($storedKey -split '\s+')[0..1] -join ' ')) { throw 'Existing SSH key pair does not match. Nothing was overwritten.' }
        Write-Host 'Reusing the SSH key from the previous preparation attempt.'
    } else {
        if (Test-Path -LiteralPath ($script:nativeKey + '.pub')) { throw 'Existing public SSH key has no private key. Nothing was overwritten.' }
        & $script:keygenPath -q -t ed25519 -f $script:nativeKey -N '""'
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create the VM SSH key.' }
        $key = (Get-Content -LiteralPath ($script:nativeKey + '.pub') -Raw).Trim()
    }
    # Disable inherited permissions; retain only current administrator and SYSTEM.
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $script:nativeKey /inheritance:r /grant:r "${identity}:(F)" 'SYSTEM:(F)' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot restrict SSH key permissions.' }
    $base = 'https://cloud-images.ubuntu.com/releases/noble/release/'
    $file = 'ubuntu-24.04-server-cloudimg-amd64.img'
    $compressed = Join-Path $downloads $file
    Write-Host 'Downloading the standard Ubuntu 24.04 cloud disk (QCOW2, not Azure).'
    # PowerShell 5.1 may expose text/plain Content as bytes. Download to a file
    # and explicitly decode UTF-8; do not depend on the response object's type.
    $checksumFile = Join-Path $downloads ('ubuntu-SHA256SUMS-' + [guid]::NewGuid().ToString('N') + '.txt')
    try {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + 'SHA256SUMS') -OutFile $checksumFile
        $checksumText = [IO.File]::ReadAllText($checksumFile,[Text.Encoding]::UTF8)
        $checksumPattern = '(?im)^([a-f0-9]{64})[ \t]+\*?' + [regex]::Escape($file) + '[ \t]*\r?$'
        $checksumEntries = [regex]::Matches($checksumText,$checksumPattern)
        if ($checksumEntries.Count -ne 1) { throw 'Expected exactly one Ubuntu image SHA256 in the downloaded checksum file.' }
        $expected = $checksumEntries[0].Groups[1].Value.ToLowerInvariant()
    } finally { Remove-Item -LiteralPath $checksumFile -Force -ErrorAction SilentlyContinue }
    if (-not (Test-Path $compressed) -or (Get-FileHash -LiteralPath $compressed -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) {
        Invoke-WebRequest -UseBasicParsing -Uri ($base + $file) -OutFile $compressed
    }
    if ((Get-FileHash -LiteralPath $compressed -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Ubuntu image checksum mismatch.' }
    $qemu = Get-ShtabQemuImg $downloads
    $imageInfoText = (& $qemu info --output=json -f qcow2 $compressed | Out-String)
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect the Ubuntu QCOW2 disk.' }
    $imageInfo = $imageInfoText | ConvertFrom-Json
    if ($imageInfo.'backing-filename' -or $imageInfo.'virtual-size' -gt ($DiskGB * 1073741824)) { throw 'Unexpected Ubuntu disk layout or requested disk too small.' }
    $disk = Join-Path $script:nativeRoot ($VMName + '.vhdx')
    $volume = Get-Volume -FilePath $script:nativeRoot -ErrorAction Stop
    if ($volume.SizeRemaining -lt ([double]$imageInfo.'virtual-size' + 10GB)) { throw 'Not enough free space to convert the Ubuntu disk.' }
    # Convert directly to VHDX; never pass a sparse archive member to Convert-VHD.
    Write-Host 'Converting Ubuntu to a dynamic Hyper-V VHDX disk.'
    & $qemu convert -p -f qcow2 -O vhdx -o subformat=dynamic $compressed $disk
    if ($LASTEXITCODE -ne 0) { throw 'Ubuntu QCOW2 to VHDX conversion failed.' }
    # Hyper-V refuses compressed, encrypted or sparse virtual disk files.
    & compact.exe /U /I $disk | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot clear compression on the VM disk.' }
    & fsutil.exe sparse setflag $disk 0 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot clear SparseFile on the VM disk.' }
    $attributes = (Get-Item -LiteralPath $disk).Attributes
    if ($attributes -band ([IO.FileAttributes]::Compressed -bor [IO.FileAttributes]::Encrypted -bor [IO.FileAttributes]::SparseFile)) { throw 'VM disk still has unsupported file attributes.' }
    Get-VHD -Path $disk -ErrorAction Stop | Out-Null
    Resize-VHD -Path $disk -SizeBytes ($DiskGB * 1073741824)
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
  - [systemctl, enable, --now, 'getty@tty1.service']
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
                if ($LASTEXITCODE -notin @(0,2)) {
                    & $script:sshPath @options "ubuntu@$script:nativeIP" 'sudo tail -n 80 /var/log/cloud-init-output.log'
                    throw 'Ubuntu cloud-init failed. VM preserved for diagnostics.'
                }
                $cloudStatus = (& $script:sshPath @options "ubuntu@$script:nativeIP" 'cloud-init status --long' | Out-String)
                if ($LASTEXITCODE -notin @(0,2)) { throw 'Cannot read cloud-init completion status.' }
                Write-Host $cloudStatus
                if ($cloudStatus -notmatch 'DataSourceNoCloud') { throw 'Ubuntu did not use the generated NoCloud seed. VM preserved for diagnostics.' }
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


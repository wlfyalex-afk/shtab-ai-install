# Standard Ubuntu image on native Hyper-V

Creates a new Ubuntu 24.04 VM on Windows Server 2019+ or Windows 10/11 Pro x64. Uses the standard Canonical QCOW2 disk instead of an Azure VHD. Hyper-V runs the VM; QEMU is installed automatically only for disk conversion, using the Windows distribution linked by qemu.org with a pinned SHA-512 digest. Ubuntu downloads are checked against Canonical SHA256SUMS.

Run in elevated Windows PowerShell 5.1:

```powershell
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$r = (Invoke-RestMethod 'https://api.github.com/repos/wlfyalex-afk/shtab-ai-install/commits/generic-ubuntu-hyperv').sha
$p = Join-Path $env:TEMP 'Install-ShtabAI.ps1'
Invoke-WebRequest -UseBasicParsing "https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/$r/Install-ShtabAI.ps1" -OutFile $p
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -Revision $r -VMName shtab-ai -VMRoot 'C:\ShtabAI\shtab-ai' -HTTPSPort 8445
if ($LASTEXITCODE -ne 0) { throw 'Installation failed; see output above.' }
```

The installer creates the SSH key and NoCloud ISO, converts the image to VHDX, starts the VM, waits for cloud-init, verifies the NoCloud datasource, transfers and verifies the repository package, runs install.sh, displays progress, configures the browser connection and certificate, and opens the application. Administrator account creation remains interactive. If Hyper-V needs enabling, restart Windows and repeat the same command. With multiple external switches, select one using -VMSwitchName. With no external switch, a private NAT is created if no conflicting NAT exists.

Default storage is C:\ShtabAI\<VMName>, outside Temp. Existing VMs and nonempty directories are never overwritten. Failed VMs are preserved for diagnosis. QEMU remains installed under Program Files\qemu for reuse. Normal installation requires no VM console or manual SSH commands.

## Acceptance status

The first guest boot installs Hyper-V tools, records its boot ID, and schedules one
cloud-init reboot. The Windows installer waits for a different boot ID, completed
NoCloud initialization, active KVP and working SSH before transferring the package.
It tolerates the SSH interruption and IP change during that reboot. The wait is
bounded to 25 minutes; errors preserve the VM and print diagnostics when SSH is available.

Full acceptance on Windows Server with Hyper-V is still required. This branch is a candidate for testing, not a verified production release. Acceptance requires a fresh install reaching READY_FOR_ADMIN and opening the application successfully.

Sources:
- https://ubuntu.com/blog/how-to-use-ubuntu-on-windows
- https://ubuntu.com/docs/public-images/public-images-reference/artifacts/
- https://www.qemu.org/download/
- https://www.qemu.org/docs/master/tools/qemu-img.html

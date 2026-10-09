$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '..\windows\Test-ShtabGPU.ps1')
function Assert-Mode($Card,[string]$Expected) {
    $actual=Select-ShtabAcceleration @($Card) auto
    if ($actual.Mode -ne $Expected) { throw ('Expected '+$Expected+', got '+$actual.Mode) }
}
Assert-Mode ([pscustomobject]@{Name='NVIDIA RTX 2080';Vendor='nvidia';CC='7.5';Driver='560.10';Free='6000'}) nvidia
Assert-Mode ([pscustomobject]@{Name='NVIDIA GTX 1050';Vendor='nvidia';CC='6.1';Driver='560.10';Free='6000'}) cpu
Assert-Mode ([pscustomobject]@{Name='NVIDIA GTX 1050';Vendor='nvidia';CC='6.1';Driver='580.10';Free='1000'}) cpu
Assert-Mode ([pscustomobject]@{Name='NVIDIA old GPU';Vendor='nvidia';CC='2.1';Driver='580.10';Free='8000'}) cpu
Assert-Mode ([pscustomobject]@{Name='NVIDIA unknown';Vendor='nvidia';CC='N/A';Driver='580.10';Free='8000'}) cpu
Assert-Mode ([pscustomobject]@{Name='AMD Radeon RX 580';Vendor='other'}) cpu
Assert-Mode ([pscustomobject]@{Name='AMD Radeon RX 7900 XT';Vendor='other'}) amd
if ((Select-ShtabAcceleration @() auto).Mode -ne 'cpu') { throw 'No GPU should use CPU' }
Write-Host 'GPU compatibility selection tests passed.'

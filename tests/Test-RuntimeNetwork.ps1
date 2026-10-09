$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\windows\Start-ShtabRuntime.ps1'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Runtime parse error' }
foreach ($name in @('Current-LAN','Sync-LAN')) {
    $node=$ast.Find({ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name },$true)
    Invoke-Expression $node.Extent.Text
}
function Get-NetIPConfiguration { $script:configs }
function Get-NetConnectionProfile { param($InterfaceIndex,$ErrorAction) [pscustomobject]@{NetworkCategory=$script:category} }
function Get-NetFirewallRule { param($Name,$ErrorAction) }
function Remove-NetFirewallRule { param($InputObject) }
function Start-Service { param($Name) }
function New-NetFirewallRule {
    param($Name,$DisplayName,$Group,$Direction,$Action,$Protocol,$LocalAddress,$LocalPort,$RemoteAddress,$Profile)
    $script:ruleAddress=$LocalAddress
    if ($RemoteAddress -ne 'LocalSubnet' -or $Profile -contains 'Public') { throw 'Firewall scope changed' }
}
function netsh.exe { $script:commands += ($args -join ' '); $global:LASTEXITCODE=0 }
$folder=Join-Path $env:TEMP ('shtab-runtime-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory $folder | Out-Null
try {
    $ManifestPath=Join-Path $folder 'installation.json'
    $manifest=[pscustomobject]@{Network=$true;LANAddress='192.168.1.10';LANInterfaceGuid='adapter-A';HTTPSPort=8445;LANRule='test-rule';DistroName='ShtabAI-Test';CertificateThumbprint='preserve'}
    $manifest | ConvertTo-Json | Set-Content $ManifestPath
    $configs=@([pscustomobject]@{IPv4DefaultGateway='192.168.1.1';NetAdapter=[pscustomobject]@{Status='Up';InterfaceGuid='adapter-A'};IPv4Address=@([pscustomobject]@{IPAddress='192.168.1.20'});InterfaceIndex=7})
    $category='Private'; $commands=@(); $lastForward='__startup__'; $previousLAN=$manifest.LANAddress
    if ((Current-LAN) -ne '192.168.1.20') { throw 'Changed DHCP address not read' }
    Sync-LAN '172.20.1.2'
    if ($ruleAddress -ne '192.168.1.20' -or ($commands -join '|') -notmatch 'delete .*listenaddress=192.168.1.10') { throw 'Old forwarding not replaced' }
    $count=$commands.Count
    Sync-LAN '172.20.1.2'
    if ($commands.Count -ne $count) { throw 'Unchanged settings reapplied' }
    $saved=Get-Content $ManifestPath -Raw | ConvertFrom-Json
    if ($saved.LANAddress -ne '192.168.1.20' -or $saved.CertificateThumbprint -ne 'preserve') { throw 'Manifest update failed' }
    $category='Public'
    if ((Current-LAN) -ne '') { throw 'Public network accepted' }
    Sync-LAN '172.20.1.2'
    if ($manifest.LANAddress -ne '' -or ($commands[-1]) -notmatch 'delete .*192.168.1.20') { throw 'Public network forwarding not removed' }
    $category='Private'; $configs[0].NetAdapter.InterfaceGuid='other-adapter'
    if ((Current-LAN) -ne '') { throw 'Different adapter silently selected' }
    Write-Host 'Runtime network tests passed.'
} finally { Remove-Item -LiteralPath $folder -Recurse -Force }

$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
function Get-CimInstance {
    param($ClassName)
    if ($ClassName -eq 'Win32_ComputerSystem') { return [pscustomobject]@{HypervisorPresent=$script:present} }
    return [pscustomobject]@{VirtualizationFirmwareEnabled=$script:firmware;SecondLevelAddressTranslationExtensions=$script:slat}
}
foreach ($relative in @('windows/Install-WSL.ps1','Install-ShtabAI-RU.ps1')) {
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $relative),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ('Parse failed: '+$relative) }
    $function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-ShtabVirtualizationReady'},$true)
    if (-not $function) { throw 'Preflight missing.' }
    . ([scriptblock]::Create($function.Extent.Text))
    function Invoke-ShtabBootConfiguration {
        param([string[]]$Arguments)
        if ($Arguments[0] -eq '/set') { $script:enabled=$true; return 'ok' }
        return ('hypervisorlaunchtype    '+$script:boot)
    }
    $script:present=$true; $script:firmware=$false; $script:slat=$false; $script:enabled=$false
    if (-not (Test-ShtabVirtualizationReady) -or $script:enabled) { throw 'Active hypervisor must pass without changing boot settings.' }
    $script:present=$false; $script:firmware=$false; $script:slat=$true
    $failed=$false
    try { Test-ShtabVirtualizationReady } catch { $failed=$_.Exception.Message -match 'BIOS/UEFI' }
    if (-not $failed -or $script:enabled) { throw 'Disabled firmware virtualization must stop before changes.' }
    $script:firmware=$true; $script:slat=$false; $failed=$false
    try { Test-ShtabVirtualizationReady } catch { $failed=$_.Exception.Message -match 'SLAT' }
    if (-not $failed) { throw 'Missing SLAT must stop.' }
    $script:slat=$true; $script:boot='Off'
    if ((Test-ShtabVirtualizationReady) -or -not $script:enabled) { throw 'Boot-disabled hypervisor must enable Auto and request reboot.' }
    $script:boot='Auto'; $script:enabled=$false
    if ((Test-ShtabVirtualizationReady) -or $script:enabled) { throw 'Inactive hypervisor must request reboot without changing Auto.' }
    # Assert the main preflight call precedes source archive and application directory creation.
    $text=(Get-Content (Join-Path $repo $relative) -Raw -Encoding UTF8)
    $position=$text.IndexOf('if (-not (Test-ShtabVirtualizationReady))')
    if ($position -lt 0 -or $position -gt $text.IndexOf('$archive = Join-Path')) { throw 'Preflight must precede downloads.' }
}
Write-Host 'Virtualization preflight tests passed.'

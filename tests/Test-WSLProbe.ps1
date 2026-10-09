$ErrorActionPreference = 'Stop'
$source = Join-Path $PSScriptRoot '..\windows\Install-WSL.ps1'
$tokens = $null
$parseErrors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw 'Installer parse failed.' }
$probe = $ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Test-WSLInstalled' }, $true)
if (-not $probe) { throw 'WSL probe function not found.' }
Invoke-Expression $probe.Extent.Text
$script:wsl = Join-Path $env:TEMP ('shtab-probe-' + [guid]::NewGuid().ToString('N') + '.cmd')
try {
    # Reproduce the uninstalled WSL stub: stderr plus a nonzero exit code.
    [IO.File]::WriteAllText($script:wsl, "@echo off`r`necho WSL is not installed 1>&2`r`nexit /b 1`r`n")
    if (Test-WSLInstalled) { throw 'Missing WSL was detected as installed.' }
    if ($ErrorActionPreference -ne 'Stop') { throw 'Error preference was not restored.' }
    # A native diagnostic on stderr must not change a successful exit code.
    [IO.File]::WriteAllText($script:wsl, "@echo off`r`necho WSL diagnostic 1>&2`r`necho WSL version 2`r`nexit /b 0`r`n")
    if (-not (Test-WSLInstalled)) { throw 'Installed WSL was detected as missing.' }
    if ($ErrorActionPreference -ne 'Stop') { throw 'Error preference was not restored.' }
    Write-Host 'WSL probe regression checks passed under Windows PowerShell.'
} finally {
    Remove-Item -LiteralPath $script:wsl -Force -ErrorAction SilentlyContinue
}

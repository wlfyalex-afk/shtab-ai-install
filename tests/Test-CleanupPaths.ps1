$ErrorActionPreference='Stop'
$source=Join-Path $PSScriptRoot '..\Cleanup-ShtabAI-RU.ps1'
$tokens=$null; $errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Cleanup parse failed' }
foreach ($name in @('Assert-ShtabRoot','Remove-OwnedTree')) {
    $node=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq $name},$true)
    Invoke-Expression $node.Extent.Text
}
foreach ($bad in @('D:\',$env:SystemRoot,$env:USERPROFILE,'relative\path')) {
    $blocked=$false
    try { Assert-ShtabRoot $bad | Out-Null } catch { $blocked=$true }
    if (-not $blocked) { throw ('Unsafe path accepted: '+$bad) }
}
$folder=Join-Path $env:TEMP ('shtab-cleanup-test-'+[guid]::NewGuid().ToString('N'))
$outside=$folder+'-preserve'
try {
    New-Item -ItemType Directory -Path $folder,$outside | Out-Null
    Set-Content -LiteralPath (Join-Path $outside 'keep.txt') -Value 'preserve'
    New-Item -ItemType Junction -Path (Join-Path $folder 'link') -Target $outside | Out-Null
    Assert-ShtabRoot $folder | Out-Null
    Remove-OwnedTree $folder
    if (-not (Test-Path -LiteralPath (Join-Path $outside 'keep.txt'))) { throw 'Cleanup followed junction outside installation' }
    Write-Host 'Cleanup path and junction tests passed.'
} finally {
    Remove-Item -LiteralPath $folder,$outside -Recurse -Force -ErrorAction SilentlyContinue
}

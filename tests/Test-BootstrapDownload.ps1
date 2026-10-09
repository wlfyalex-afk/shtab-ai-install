$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$tokens=$null
$errors=$null
$ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo 'Install-ShtabAI-Windows.ps1'),[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Launcher parse failed.' }
$function=$ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Receive-ShtabBootstrap' },$true)
if (-not $function) { throw 'Bootstrap download function missing.' }
. ([scriptblock]::Create($function.Extent.Text))
$script:fixture=Join-Path ([IO.Path]::GetTempPath()) ('shtab-bootstrap-test-'+[guid]::NewGuid().ToString('N'))
try {
    New-Item -ItemType Directory -Path (Join-Path $script:fixture 'source/windows') -Force | Out-Null
    # The fixture installer exercises the same sibling dependency as Install-WSL.
    Set-Content -LiteralPath (Join-Path $script:fixture 'source/windows/Install-WSL.ps1') -Encoding UTF8 -Value '. (Join-Path $PSScriptRoot ''Test-ShtabGPU.ps1''); ''bootstrap-ready'''
    Copy-Item (Join-Path $repo 'windows/Test-ShtabGPU.ps1') (Join-Path $script:fixture 'source/windows/Test-ShtabGPU.ps1')
    $lines=@('windows/Install-WSL.ps1','windows/Test-ShtabGPU.ps1') | ForEach-Object {
        (Get-FileHash (Join-Path $script:fixture ('source/'+$_)) -Algorithm SHA256).Hash.ToLowerInvariant()+'  '+$_
    }
    $sums=Join-Path $script:fixture 'SHA256SUMS'
    Set-Content -LiteralPath $sums -Encoding UTF8 -Value $lines
    function Invoke-WebRequest {
        param([switch]$UseBasicParsing,[string]$Uri,[string]$OutFile,[int]$TimeoutSec)
        if ($TimeoutSec -ne 60) { throw 'Expected bounded download timeout.' }
        $relative=$Uri.Substring('https://fixture.invalid/revision/'.Length)
        Copy-Item -LiteralPath (Join-Path $script:fixture ('source/'+$relative)) -Destination $OutFile
    }
    $installer=Receive-ShtabBootstrap -BaseUri 'https://fixture.invalid/revision/' -Work (Join-Path $script:fixture 'success') -Checksums $sums
    if ((& $installer) -ne 'bootstrap-ready') { throw 'Sibling GPU dependency did not load.' }
    # Missing checksum, corrupt dependency and download failure must all stop bootstrap.
    Set-Content -LiteralPath $sums -Encoding UTF8 -Value $lines[0]
    $failed=$false
    try { Receive-ShtabBootstrap -BaseUri 'https://fixture.invalid/revision/' -Work (Join-Path $script:fixture 'missing-sum') -Checksums $sums } catch { $failed=$_.Exception.Message -like '*checksum missing*Test-ShtabGPU.ps1*' }
    if (-not $failed) { throw 'Missing helper checksum was accepted.' }
    Set-Content -LiteralPath $sums -Encoding UTF8 -Value $lines
    Add-Content -LiteralPath (Join-Path $script:fixture 'source/windows/Test-ShtabGPU.ps1') -Value '# corrupted download'
    $failed=$false
    try { Receive-ShtabBootstrap -BaseUri 'https://fixture.invalid/revision/' -Work (Join-Path $script:fixture 'corrupt') -Checksums $sums } catch { $failed=$_.Exception.Message -like '*checksum mismatch*Test-ShtabGPU.ps1*' }
    if (-not $failed) { throw 'Corrupt helper was accepted.' }
    Remove-Item -LiteralPath (Join-Path $script:fixture 'source/windows/Test-ShtabGPU.ps1')
    $failed=$false
    try { Receive-ShtabBootstrap -BaseUri 'https://fixture.invalid/revision/' -Work (Join-Path $script:fixture 'download-failed') -Checksums $sums } catch { $failed=$true }
    if (-not $failed) { throw 'Missing download was accepted.' }
    Write-Host 'Bootstrap download and failure tests passed.'
} finally {
    Remove-Item -LiteralPath $script:fixture -Recurse -Force -ErrorAction SilentlyContinue
}

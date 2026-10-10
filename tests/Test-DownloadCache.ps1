$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../windows/Download-Cache.ps1')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('shtab-cache-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$script:CacheDir=Join-Path $fixture 'cache'
$script:downloads=0
$script:payload='original'
function Receive-File {
    param($Uri,$OutFile)
    $script:downloads++
    [IO.File]::WriteAllText($OutFile,$script:payload)
}
try {
    $source=Join-Path $fixture 'source'
    [IO.File]::WriteAllText($source,$script:payload)
    $hash=(Get-FileHash $source -Algorithm SHA256).Hash
    $out=Join-Path $fixture 'ollama.zip'
    Receive-CachedFile 'https://fixture.invalid/ollama.zip' $out $hash
    Remove-Item $out
    Receive-CachedFile 'https://fixture.invalid/ollama.zip' $out $hash
    if ($script:downloads -ne 1) { throw 'A verified cache hit downloaded again.' }
    $cached=Get-ChildItem (Join-Path $script:CacheDir 'archives') -Filter '*-ollama.zip'
    [IO.File]::WriteAllText($cached.FullName,'corrupt')
    Receive-CachedFile 'https://fixture.invalid/ollama.zip' $out $hash
    if ($script:downloads -ne 2) { throw 'Corrupt cache was not replaced.' }
    $script:payload='new-version'
    [IO.File]::WriteAllText($source,$script:payload)
    $newHash=(Get-FileHash $source -Algorithm SHA256).Hash
    Receive-CachedFile 'https://fixture.invalid/new.zip' $out $newHash
    if ($script:downloads -ne 3) { throw 'Changed version not downloaded.' }
    $script:payload='bad-download'
    $failed=$false
    try { Receive-CachedFile 'https://fixture.invalid/bad.zip' (Join-Path $fixture 'bad.zip') $hash } catch { $failed=$true }
    if (-not $failed -or (Test-Path (Join-Path $fixture 'bad.zip'))) { throw 'Invalid download was accepted.' }
    Write-Host 'Archive cache hit, corruption, version and invalid download tests passed.'
} finally { Remove-Item -LiteralPath $fixture -Recurse -Force }

$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../windows/Install-Progress.ps1')
. (Join-Path $PSScriptRoot '../windows/Download-Cache.ps1')
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('shtab-resume-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$script:responses=New-Object Collections.Queue
$script:ranges=@()
function New-ShtabDownloadRequest {
    param($Uri)
    $request=[pscustomobject]@{UserAgent='';Timeout=0;ReadWriteTimeout=0}
    $request | Add-Member ScriptMethod AddRange { param($Offset) $script:ranges += $Offset }
    $request | Add-Member ScriptMethod GetResponse { return $script:responses.Dequeue() }
    return $request
}
function Add-Response {
    param([string]$Text,[long]$Length,[int]$Status=200,[string]$Range='')
    $response=[pscustomobject]@{ContentLength=$Length;StatusCode=$Status;Headers=@{'Content-Range'=$Range};Bytes=[Text.Encoding]::UTF8.GetBytes($Text)}
    $response | Add-Member ScriptMethod GetResponseStream { return [IO.MemoryStream]::new($this.Bytes) }
    $response | Add-Member ScriptMethod Close {}
    $script:responses.Enqueue($response)
}
Show-Stage 1 'Проверка докачки'
try {
    $script:CacheDir=Join-Path $fixture 'cache'
    $source=Join-Path $fixture 'expected'
    [IO.File]::WriteAllText($source,'abcdefgh')
    $hash=(Get-FileHash $source -Algorithm SHA256).Hash
    $out=Join-Path $fixture 'ollama.zip'
    Add-Response 'abcd' 8
    $failed=$false
    try { Receive-CachedFile 'https://fixture.invalid/ollama.zip' $out $hash } catch { $failed=$true }
    if (-not $failed -or (Test-Path $out)) { throw 'Truncated file was accepted.' }
    $partial=Get-ChildItem (Join-Path $script:CacheDir 'archives') -Filter '*.part'
    if ($partial.Length -ne 4) { throw 'Interrupted bytes were discarded.' }
    Add-Response 'efgh' 4 206 'bytes 4-7/8'
    Receive-CachedFile 'https://fixture.invalid/ollama.zip' $out $hash
    if ([IO.File]::ReadAllText($out) -ne 'abcdefgh' -or $script:ranges[-1] -ne 4) { throw 'Range resume did not append the missing bytes.' }
    if (Get-ChildItem (Join-Path $script:CacheDir 'archives') -Filter '*.part') { throw 'Completed partial was left behind.' }
    # A server ignoring Range returns a full 200 response: replace, never append.
    $out2=Join-Path $fixture 'full'
    [IO.File]::WriteAllText(($out2+'.part'),'abcd')
    Add-Response 'abcdefgh' 8
    Receive-File 'https://fixture.invalid/full' $out2 -Resume
    if ([IO.File]::ReadAllText($out2) -ne 'abcdefgh') { throw 'Server ignoring Range was appended to the partial.' }
    # Reject an inconsistent Content-Range without losing saved bytes.
    $out3=Join-Path $fixture 'invalid'
    [IO.File]::WriteAllText(($out3+'.part'),'abcd')
    Add-Response 'efgh' 4 206 'bytes 0-3/8'
    $failed=$false
    try { Receive-File 'https://fixture.invalid/invalid' $out3 -Resume } catch { $failed=$true }
    if (-not $failed -or [IO.File]::ReadAllText(($out3+'.part')) -ne 'abcd') { throw 'Invalid range changed the saved partial.' }
    Write-Host 'Interrupted archive, range resume, SHA256 and full-response fallback passed.'
} finally { Remove-Item -LiteralPath $fixture -Recurse -Force }

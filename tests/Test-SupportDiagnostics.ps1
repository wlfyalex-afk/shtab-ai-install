$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('shtab-support-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
try {
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo 'windows/Collect-ShtabDiagnostics.ps1'),[ref]$tokens,[ref]$errors)
    $function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Read-DiagnosticNative'},$true)
    . ([scriptblock]::Create($function.Extent.Text))
    $exe=(Get-Process -Id $PID).Path
    $child=Join-Path $fixture 'child.ps1'
    Set-Content -LiteralPath $child -Value 'Write-Output "native-stdout"; [Console]::Error.WriteLine("native-stderr"); exit 7' -Encoding UTF8
    $result=Read-DiagnosticNative $exe ('-NoProfile -File "'+$child+'"')
    if ($result -notmatch 'native-stdout' -or $result -notmatch 'native-stderr' -or $result -notmatch '7') { throw 'Native diagnostics lost output or exit code.' }
    Set-Content -LiteralPath $child -Value '[Console]::OutputEncoding=[Text.Encoding]::Unicode; Write-Output "WSL-test-output"' -Encoding UTF8
    $result=Read-DiagnosticNative $exe ('-NoProfile -File "'+$child+'"')
    if ($result -notmatch 'WSL-test-output') { throw 'UTF16 native output was not decoded.' }
    Set-Content -LiteralPath $child -Value 'Start-Sleep -Seconds 20' -Encoding UTF8
    $clock=[Diagnostics.Stopwatch]::StartNew()
    $result=Read-DiagnosticNative $exe ('-NoProfile -File "'+$child+'"') -TimeoutMilliseconds 500
    if ($clock.Elapsed.TotalSeconds -gt 10 -or $result -notmatch 'не ответила') { throw 'Diagnostics timeout did not work.' }

    # Run the actual CMD bootstrap command with a failing downloader: its log
    # must exist before any installer script could have been downloaded.
    $line=Get-Content (Join-Path $repo 'Start-ShtabAI-Windows.cmd') -Encoding UTF8 | Where-Object { $_ -match '^powershell.exe .*shtab-install-' }
    $command=[regex]::Match($line, '-Command "(.+)"$').Groups[1].Value
    $child=Join-Path $fixture 'bootstrap-failure.ps1'
    $setup='$env:LOCALAPPDATA='''+$fixture.Replace("'","''")+'''; $env:TEMP=$env:LOCALAPPDATA; function Invoke-WebRequest { throw ''fixture-download-failed'' }; '
    Set-Content -LiteralPath $child -Value ($setup+$command) -Encoding UTF8
    & $exe -NoProfile -File $child | Out-Null
    if ($LASTEXITCODE -ne 1) { throw 'Expected failed bootstrap exit code.' }
    $logs=@(Get-ChildItem (Join-Path $fixture 'ShtabAI/Logs') -Filter 'ShtabAI-bootstrap-*.log')
    if ($logs.Count -ne 1 -or (Get-Content $logs[0].FullName -Raw) -notmatch 'fixture-download-failed') { throw 'CMD did not log an early download failure.' }
    # The failed child is expected; do not return its exit code to the CI shell.
    $global:LASTEXITCODE=0
    Write-Host 'Diagnostic output, timeout and CMD failure logging tests passed.'
} finally { Remove-Item -LiteralPath $fixture -Recurse -Force }

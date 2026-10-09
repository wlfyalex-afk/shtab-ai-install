$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
$savedTemp=$env:TEMP
$savedSystemRoot=$env:SystemRoot
$fixture=Join-Path ([IO.Path]::GetTempPath()) ('shtab-wsl-msi-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $fixture | Out-Null
$env:TEMP=$fixture
$env:SystemRoot=$fixture
try {
    function Invoke-RestMethod {
        param($Uri,$Headers,$TimeoutSec)
        if ($Uri -ne 'https://api.github.com/repos/microsoft/WSL/releases/latest') { throw 'Wrong release endpoint.' }
        [pscustomobject]@{prerelease=$false;draft=$false;assets=@([pscustomobject]@{name='wsl.2.6.0.0.x64.msi';browser_download_url='https://github.com/microsoft/WSL/releases/download/2.6.0/wsl.2.6.0.0.x64.msi'})}
    }
    function Invoke-WebRequest {
        param([switch]$UseBasicParsing,$Uri,$OutFile,$TimeoutSec)
        Set-Content -LiteralPath $OutFile -Value 'test package'
    }
    function Get-AuthenticodeSignature {
        param($LiteralPath)
        [pscustomobject]@{Status=$script:signatureStatus;SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation, C=US'}}
    }
    function Start-Process {
        param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
        if ($ArgumentList -notmatch '/qn /norestart /L\*v' -or $FilePath -notmatch 'msiexec.exe$') { throw 'Incorrect MSI command.' }
        $script:started=$true
        [pscustomobject]@{ExitCode=$script:exitCode}
    }
    foreach ($relative in @('windows/Install-WSL.ps1','Install-ShtabAI-RU.ps1')) {
        $tokens=$null; $errors=$null
        $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $relative),[ref]$tokens,[ref]$errors)
        if ($errors.Count) { throw ('Parse failed: '+$relative) }
        $function=$ast.Find({param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-ShtabWSLRuntime'},$true)
        if (-not $function) { throw 'MSI runtime installation function missing.' }
        . ([scriptblock]::Create($function.Extent.Text))
        $branch=$ast.Find({param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Clauses[0].Item1.Extent.Text -eq '-not (Test-WSLInstalled)'},$true)
        if ($branch.Extent.Text -notmatch 'Install-ShtabWSLRuntime' -or $branch.Extent.Text -match 'Invoke-WSL --install') { throw 'Legacy WSL still uses unsupported install options.' }
        foreach ($code in @(0,3010)) {
            $script:exitCode=$code; $script:signatureStatus='Valid'; $script:started=$false
            Install-ShtabWSLRuntime
            if (-not $script:started) { throw 'MSI was not started.' }
        }
        $script:signatureStatus='NotTrusted'; $script:started=$false; $failed=$false
        try { Install-ShtabWSLRuntime } catch { $failed=$_.Exception.Message -like '*signature verification failed*' }
        if (-not $failed -or $script:started) { throw 'Untrusted MSI was executed.' }
        $script:signatureStatus='Valid'; $script:exitCode=1603; $failed=$false
        try { Install-ShtabWSLRuntime } catch { $failed=$_.Exception.Message -like '*code 1603*MSI log*' }
        if (-not $failed) { throw 'MSI installation failure was accepted.' }
    }
    Write-Host 'Legacy WSL MSI installation tests passed.'
} finally {
    $env:TEMP=$savedTemp
    $env:SystemRoot=$savedSystemRoot
    Remove-Item -LiteralPath $fixture -Recurse -Force -ErrorAction SilentlyContinue
}

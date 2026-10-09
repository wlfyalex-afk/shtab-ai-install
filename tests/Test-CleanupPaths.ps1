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

# Read the saved desktop path even when cleanup runs from another desktop.
foreach ($scriptName in @('Cleanup-ShtabAI-RU.ps1','Uninstall-ShtabAI-Windows.ps1')) {
    $scriptPath=Join-Path $PSScriptRoot ('..\' + $scriptName)
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile($scriptPath,[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ('Shortcut cleanup parse failed: '+$scriptName) }
    $node=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Remove-ShtabShortcuts'},$true)
    if (-not $node) { throw 'Shortcut cleanup function missing' }
    Invoke-Expression $node.Extent.Text
    $desktop=Join-Path $env:TEMP ('shtab-shortcuts-'+[guid]::NewGuid().ToString('N'))
    $root=Join-Path $desktop 'installation'
    $distro='ShtabAI-ShortcutTest-'+[guid]::NewGuid().ToString('N')
    $urlPath=Join-Path $desktop ($distro+'.url')
    $managerPath=Join-Path $desktop ($distro+'-Manager.lnk')
    $manifest=[pscustomobject]@{DistroName=$distro;Shortcut=$urlPath;HTTPSPort=8445;LANAddress='192.168.10.122'}
    try {
        New-Item -ItemType Directory -Path $root -Force | Out-Null
        Set-Content -LiteralPath $urlPath -Value @('[InternetShortcut]','URL=https://localhost:8445/login')
        $shell=New-Object -ComObject WScript.Shell
        $link=$shell.CreateShortcut($managerPath)
        $link.TargetPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $link.Arguments='-NoProfile -File "'+(Join-Path $root 'Manage-ShtabAI.ps1')+'"'
        $link.Save()
        Remove-ShtabShortcuts $manifest $root
        if ((Test-Path -LiteralPath $urlPath) -or (Test-Path -LiteralPath $managerPath)) { throw 'Saved desktop shortcuts were not removed' }
        Set-Content -LiteralPath $urlPath -Value @('[InternetShortcut]','URL=https://192.168.10.122:8445/login')
        Remove-ShtabShortcuts $manifest $root
        if (Test-Path -LiteralPath $urlPath) { throw 'LAN shortcut was not removed' }
        Set-Content -LiteralPath $urlPath -Value @('[InternetShortcut]','URL=https://example.com:8445/login')
        $link=$shell.CreateShortcut($managerPath)
        $link.TargetPath=Join-Path $env:SystemRoot 'System32\notepad.exe'
        $link.Arguments=''
        $link.Save()
        Remove-ShtabShortcuts $manifest $root
        if (-not (Test-Path -LiteralPath $urlPath) -or -not (Test-Path -LiteralPath $managerPath)) { throw 'Unrelated shortcut target was removed' }
        Write-Host ('Saved desktop and ownership tests passed: '+$scriptName)
    } finally {
        Remove-Item -LiteralPath $desktop -Recurse -Force -ErrorAction SilentlyContinue
    }
}

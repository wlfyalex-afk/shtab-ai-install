$ErrorActionPreference='Stop'
$repo=Split-Path -Parent $PSScriptRoot
function Get-CimInstance { param($ClassName) [pscustomobject]@{TotalPhysicalMemory=$script:hostBytes} }
foreach ($relative in @('windows/Install-WSL.ps1','Install-ShtabAI-RU.ps1')) {
    $tokens=$null; $errors=$null
    $ast=[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $repo $relative),[ref]$tokens,[ref]$errors)
    if ($errors.Count) { throw ('Parse failed: '+$relative) }
    $guard=$ast.Find({param($node) $node -is [System.Management.Automation.Language.IfStatementAst] -and $node.Clauses[0].Item1.Extent.Text -match 'TotalPhysicalMemory -lt'},$true)
    $hostAssignment=$ast.Find({param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$hostMemory'},$true)
    $limitAssignment=$ast.Find({param($node) $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and $node.Left.Extent.Text -eq '$memoryGB'},$true)
    if (-not $guard -or -not $hostAssignment -or -not $limitAssignment) { throw ('Memory policy missing: '+$relative) }
    foreach ($case in @(@(8,0,$false),@(10,0,$false),@(11.75,8,$true),@(12,8,$true),@(16,10,$true),@(24,18,$true))) {
        $script:hostBytes=[double]$case[0]*1073741824
        $accepted=$true
        try { . ([scriptblock]::Create($guard.Extent.Text)) } catch { $accepted=$false }
        if ($accepted -ne $case[2]) { throw ('Wrong host memory acceptance: '+$relative+' '+$case[0]) }
        if ($accepted) {
            . ([scriptblock]::Create($hostAssignment.Extent.Text))
            . ([scriptblock]::Create($limitAssignment.Extent.Text))
            if ($memoryGB -ne $case[1]) { throw ('Wrong WSL limit: '+$relative+' '+$case[0]+' -> '+$memoryGB) }
        }
    }
}
Write-Host 'Host memory and WSL limit tests passed.'

$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot '../windows/Install-Progress.ps1')
$script:bars=@{}
$script:messages=@()
function Write-Host { param($Object,$ForegroundColor) $script:messages += [string]$Object }
function Write-Progress {
    param($Id,$Activity,$Status,$PercentComplete,$ParentId,$SecondsRemaining,[switch]$Completed)
    $script:bars[$Id]=@{Activity=$Activity;Status=$Status;Percent=$PercentComplete;Completed=[bool]$Completed;Parent=$ParentId}
}
Show-Stage 3 'Ubuntu'
if ($script:bars[1].Percent -ne 11 -or $script:bars[1].Status -notmatch '3/17') { throw 'Stage progress missing.' }
Show-AppStage 'DOWNLOADING_QWEN'
if ($progressState.StageNumber -ne 11) { throw 'Qwen stage missing.' }
Show-ModelProgress ([pscustomobject]@{model='qwen';phase='download';completed=25;total=100;bytes_per_second=5;eta_seconds=15;stale=$false})
if ($script:bars[3].Percent -ne 25) { throw 'Model download percentage missing.' }
1..5 | ForEach-Object { Show-AppStage 'DOWNLOADING_QWEN'; Show-ModelProgress ([pscustomobject]@{model='qwen';phase='download';completed=25;total=100;bytes_per_second=5;eta_seconds=15;stale=$false}) }
if ($script:messages.Count -ne 0 -or $script:bars[3].Parent -ne 1) { throw 'Progress must update in place below the summary, without repeated console lines.' }
Show-OperationProgress ([pscustomobject]@{title='Слой Python';text='25 MB / 100 MB';percent=25})
if ($script:bars[3].Percent -ne 25 -or $script:bars[3].Parent -ne 1) { throw 'Current operation indicator missing.' }
Show-AppStage 'DOWNLOADING_WHISPER'
if ($progressState.StageNumber -ne 12) { throw 'Whisper stage missing.' }
Show-ModelProgress ([pscustomobject]@{model='whisper';phase='download';completed=25;total=0;bytes_per_second=5;eta_seconds=$null;stale=$false})
if ($script:bars[3].Percent -ne -1) { throw 'Unknown download size must be indeterminate.' }
Show-AppStage 'READY_FOR_ADMIN'
if ($progressState.StageNumber -ne 14 -or $progressState.Finished) { throw 'Administrator setup must remain unfinished.' }
Complete-Stages
if (-not $progressState.Finished -or -not $script:bars[1].Completed) { throw 'Completion missing.' }
Write-Host 'Installation stage and model progress tests passed.'

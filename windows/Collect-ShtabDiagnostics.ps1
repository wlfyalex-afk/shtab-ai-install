#Requires -Version 5.1
param([Parameter(Mandatory=$true)][string]$ManifestPath)
$ErrorActionPreference='Stop'
$manifest=Get-Content -LiteralPath $ManifestPath -Raw | ConvertFrom-Json
if ($manifest.Product -ne 'ShtabAI' -or $manifest.DistroName -notmatch '^ShtabAI-[A-Za-z0-9-]+$') { throw 'Некорректное описание установки.' }
$folder=Join-Path ([Environment]::GetFolderPath('Desktop')) 'ShtabAI-support'
New-Item -ItemType Directory -Path $folder -Force | Out-Null
$file=Join-Path $folder ('ShtabAI-diagnostics-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'-'+[guid]::NewGuid().ToString('N').Substring(0,8)+'.txt')
$encoding=New-Object Text.UTF8Encoding($false)
[IO.File]::WriteAllText($file, ('Штаб.AI — диагностика Windows'+[Environment]::NewLine+'Поддержка: wlfyalex@gmail.com'+[Environment]::NewLine+(Get-Date -Format o)+[Environment]::NewLine), $encoding)
function Read-DiagnosticNative {
    param([string]$Executable,[string]$Arguments,[int]$TimeoutMilliseconds=20000)
    function Decode-Output([byte[]]$Bytes) {
        if ([Array]::IndexOf($Bytes,[byte]0) -ge 0 -or ($Bytes.Length -ge 2 -and $Bytes[0] -eq 255 -and $Bytes[1] -eq 254)) {
            return [Text.Encoding]::Unicode.GetString($Bytes).TrimStart([char]0xfeff)
        }
        return [Text.Encoding]::UTF8.GetString($Bytes).TrimStart([char]0xfeff)
    }
    $info=New-Object Diagnostics.ProcessStartInfo
    $info.FileName=$Executable
    $info.Arguments=$Arguments
    $info.UseShellExecute=$false
    $info.CreateNoWindow=$true
    $info.RedirectStandardOutput=$true
    $info.RedirectStandardError=$true
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$info
    $stdoutBytes=New-Object IO.MemoryStream
    $stderrBytes=New-Object IO.MemoryStream
    try {
        [void]$process.Start()
        $stdout=$process.StandardOutput.BaseStream.CopyToAsync($stdoutBytes)
        $stderr=$process.StandardError.BaseStream.CopyToAsync($stderrBytes)
        if (-not $process.WaitForExit($TimeoutMilliseconds)) {
            try { $process.Kill() } catch { }
            return 'Команда не ответила за отведённое время; сбор диагностики продолжается.'
        }
        if (-not $stdout.Wait(2000) -or -not $stderr.Wait(2000)) { return 'Процесс завершился, но вывод не закрылся; сбор диагностики продолжается.' }
        return ('Код: '+$process.ExitCode+[Environment]::NewLine+(Decode-Output $stdoutBytes.ToArray())+(Decode-Output $stderrBytes.ToArray()))
    } finally { $process.Dispose(); $stdoutBytes.Dispose(); $stderrBytes.Dispose() }
}
function Add-DiagnosticSection {
    param([string]$Title,[scriptblock]$Read)
    Write-Host ('Проверяем: '+$Title)
    $savedPreference=$ErrorActionPreference
    try {
        $ErrorActionPreference='Continue'
        $result=(& $Read 2>&1 | Out-String) -replace "`0",''
    } catch { $result=$_.Exception.Message }
    finally { $ErrorActionPreference=$savedPreference }
    $result=[regex]::Replace($result,'(?i)\b(password|passwd|secret|token|authorization)\b\s*[:=]\s*[^\r\n]+','$1=[скрыто]')
    $result=[regex]::Replace($result,'(?i)\bBearer\s+\S+','Bearer [скрыто]')
    [IO.File]::AppendAllText($file, ([Environment]::NewLine+'=== '+$Title+' ==='+[Environment]::NewLine+$result), $encoding)
}
$wsl=Join-Path $env:SystemRoot 'System32\wsl.exe'
Add-DiagnosticSection 'Windows и память' {
    Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version,BuildNumber,TotalVisibleMemorySize,FreePhysicalMemory | Format-List
}
Add-DiagnosticSection 'Свободное место' {
    $drives=@($manifest.Root.Substring(0,1))
    if ($manifest.CacheDir) { $drives += $manifest.CacheDir.Substring(0,1) }
    $drives | Select-Object -Unique | ForEach-Object { Get-Volume -DriveLetter $_ | Select-Object DriveLetter,FileSystem,Size,SizeRemaining | Format-List }
}
Add-DiagnosticSection 'Версия WSL' { Read-DiagnosticNative $wsl '--version' }
Add-DiagnosticSection 'Дистрибутивы WSL' { Read-DiagnosticNative $wsl '--list --verbose' }
Add-DiagnosticSection 'Автозапуск' {
    Get-ScheduledTask -TaskName $manifest.TaskName | Select-Object TaskName,State | Format-List
    Get-ScheduledTaskInfo -TaskName $manifest.TaskName | Select-Object LastRunTime,LastTaskResult | Format-List
}
Add-DiagnosticSection 'Приложение и журналы Linux' {
    Read-DiagnosticNative $wsl ('--distribution '+$manifest.DistroName+' --user root --exec /usr/bin/python3 /opt/shtab-ai-021/scripts/support-report.py --stdout') -TimeoutMilliseconds 90000
}
foreach ($name in @('runtime-error.log','ollama-error.log','ollama.log')) {
    $path=Join-Path $manifest.Root $name
    if (Test-Path -LiteralPath $path) { Add-DiagnosticSection $name { Get-Content -LiteralPath $path -Tail 150 } }
}
Write-Host ('Диагностика сохранена: '+$file) -ForegroundColor Green
Write-Host 'Просмотрите файл и приложите его вместе со скриншотом к письму на wlfyalex@gmail.com.'

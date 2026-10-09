#Requires -Version 5.1
# Compatibility snapshot: https://docs.ollama.com/gpu, checked 2026-10-09.
# The 4096 MiB free-memory threshold is our conservative installation policy,
# not an official model minimum. Actual inference is still required.
function Select-ShtabAcceleration($Cards,[string]$Requested='auto') {
    if ($Requested -eq 'cpu') { return [pscustomobject]@{Mode='cpu';Reason='Выбран процессор'} }
    foreach ($card in $Cards) {
        if ($Requested -notin @('auto','nvidia')) { continue }
        if ($card.Vendor -ne 'nvidia') { continue }
        $cc=0.0; $driver=0.0; $free=0.0
        $culture=[Globalization.CultureInfo]::InvariantCulture
        $style=[Globalization.NumberStyles]::Float
        if (-not [double]::TryParse([string]$card.CC,$style,$culture,[ref]$cc) -or -not [double]::TryParse([string]$card.Driver,$style,$culture,[ref]$driver) -or -not [double]::TryParse([string]$card.Free,$style,$culture,[ref]$free)) { continue }
        $minimumDriver=550
        if ($cc -lt 6.3) { $minimumDriver=570 }
        if ($cc -ge 5 -and $driver -ge $minimumDriver -and $free -ge 4096) {
            return [pscustomobject]@{Mode='nvidia';Reason=([string]$card.Name+'; CUDA CC '+$cc+'; free MiB '+$free)}
        }
    }
    # Exact Windows ROCm list; no family/prefix guessing, no Linux overrides.
    $amd=@('AMD Radeon RX 7900 XTX','AMD Radeon RX 7900 XT','AMD Radeon RX 7900 GRE','AMD Radeon RX 7800 XT','AMD Radeon RX 7700 XT','AMD Radeon RX 7600 XT','AMD Radeon RX 7600','AMD Radeon PRO W7900','AMD Radeon PRO W7800','AMD Radeon PRO W7700','AMD Radeon PRO W7600','AMD Radeon PRO W7500')
    foreach ($card in $Cards) {
        if ($Requested -in @('auto','amd') -and $card.Name -in $amd) {
            return [pscustomobject]@{Mode='amd';Reason=([string]$card.Name+'; ROCm требует пробного запуска; Whisper работает на CPU')}
        }
    }
    return [pscustomobject]@{Mode='cpu';Reason='Совместимая GPU с подходящим драйвером и запасом памяти не подтверждена; автоматически используем CPU'}
}
function Get-ShtabAcceleration([string]$Requested='auto') {
    $cards=@(Get-CimInstance Win32_VideoController | ForEach-Object {
        [pscustomobject]@{Name=$_.Name;Vendor='other';CC='';Driver=$_.DriverVersion;Total='';Free=''}
    })
    $smi=Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $smi) {
        $path=Join-Path $env:SystemRoot 'System32\nvidia-smi.exe'
        if (Test-Path -LiteralPath $path) { $smi=[pscustomobject]@{Source=$path} }
    }
    if ($smi) {
        $saved=$ErrorActionPreference
        try {
            $ErrorActionPreference='Continue'
            $lines=& $smi.Source --query-gpu=name,compute_cap,driver_version,memory.total,memory.free --format=csv,noheader,nounits 2>$null
            $exit=$LASTEXITCODE
        } finally { $ErrorActionPreference=$saved }
        if ($exit -eq 0) {
            foreach ($item in ($lines | ConvertFrom-Csv -Header Name,CC,Driver,Total,Free)) {
                $cards += [pscustomobject]@{Name=$item.Name.Trim();Vendor='nvidia';CC=$item.CC.Trim();Driver=$item.Driver.Trim();Total=$item.Total.Trim();Free=$item.Free.Trim()}
            }
        }
    }
    Write-Host 'Диагностика GPU: точная модель, драйвер, CUDA, видеопамять и свободная память (МБ)'
    $cards | Format-Table Name,Driver,CC,Total,Free -AutoSize | Out-Host
    $selection=Select-ShtabAcceleration $cards $Requested
    Write-Host ('Выбран режим: '+$selection.Mode+' / '+$selection.Reason)
    return $selection
}

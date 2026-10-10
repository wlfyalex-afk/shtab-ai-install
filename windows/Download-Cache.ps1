function Receive-CachedFile {
    param([string]$Uri, [string]$OutFile, [string]$SHA256)
    if ($SHA256 -notmatch '^[a-fA-F0-9]{64}$') { throw 'Нет SHA256 для файла кэша.' }
    $folder = Join-Path $script:CacheDir 'archives'
    New-Item -ItemType Directory -Path $folder -Force | Out-Null
    $cached = Join-Path $folder ($SHA256.ToLowerInvariant() + '-' + (Split-Path $OutFile -Leaf))
    $lock = [IO.File]::Open(($cached + '.lock'), 'OpenOrCreate', 'ReadWrite', 'None')
    try {
        if (Test-Path -LiteralPath $cached) {
            Write-Host ('Проверяем SHA256 файла из кэша: ' + (Split-Path $OutFile -Leaf)) -ForegroundColor Cyan
            if ((Get-FileHash -LiteralPath $cached -Algorithm SHA256).Hash -ine $SHA256) {
                Remove-Item -LiteralPath $cached -Force
                Write-Host 'Файл повреждён. Скачиваем заново.' -ForegroundColor Yellow
            }
        }
        if (-not (Test-Path -LiteralPath $cached)) {
            # Also accept standard archive names copied here by the user.
            $names = @((Split-Path $OutFile -Leaf), ([Uri]$Uri).Segments[-1]) | Select-Object -Unique
            foreach ($name in $names) {
                foreach ($directory in @($script:CacheDir, $folder)) {
                    $existing = Join-Path $directory $name
                    if ((Test-Path -LiteralPath $existing -PathType Leaf) -and
                        (Get-FileHash -LiteralPath $existing -Algorithm SHA256).Hash -ieq $SHA256) {
                        Copy-Item -LiteralPath $existing -Destination $cached -Force
                        Write-Host ('Проверен и добавлен в кэш: ' + $existing) -ForegroundColor Green
                        break
                    }
                }
                if (Test-Path -LiteralPath $cached) { break }
            }
        }
        if (-not (Test-Path -LiteralPath $cached)) {
            $partial = $cached + '.partial'
            Receive-File -Uri $Uri -OutFile $partial -Resume
            if ((Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash -ine $SHA256) {
                Remove-Item -LiteralPath $partial -Force
                throw 'Контрольная сумма загруженного файла не совпала.'
            }
            Move-Item -LiteralPath $partial -Destination $cached -Force
        } else { Write-Host 'Контрольная сумма совпала — используем кэш, без скачивания.' -ForegroundColor Green }
        Copy-Item -LiteralPath $cached -Destination $OutFile -Force
    } finally { $lock.Dispose() }
}
function Test-CachedOllamaModels {
    param([string]$Models)
    $blobs = Join-Path $Models 'blobs'
    if (Test-Path -LiteralPath $blobs) {
        Get-ChildItem -LiteralPath $blobs -File | Where-Object Name -match '^sha256-[a-f0-9]{64}$' | ForEach-Object {
            Write-Host ('Проверяем SHA256 слоя Qwen: ' + $_.Name) -ForegroundColor Cyan
            if ((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash -ine $_.Name.Substring(7)) {
                Remove-Item -LiteralPath $_.FullName -Force
                Write-Host 'Повреждённый слой удалён; Ollama загрузит его снова.' -ForegroundColor Yellow
            }
        }
    }
}

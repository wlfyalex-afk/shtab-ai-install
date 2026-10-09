@echo off
setlocal
title Shtab.AI - Windows
:menu
cls
echo Shtab.AI
echo.
echo 1. Install - choose installation folder, CPU/GPU and network access
echo 2. Uninstall - remove a selected Shtab.AI installation
echo 0. Exit
echo.
choice /c 120 /n /m "Select [1/2/0]: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto uninstall
goto install
:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Write-Host 'Downloading Shtab.AI installer...'; $p=Join-Path $env:TEMP ('shtab-install-'+[guid]::NewGuid().ToString('N')+'.ps1'); try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Install-ShtabAI-Windows.ps1' -OutFile $p; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -KeepWindowOpen; if ($LASTEXITCODE -ne 0) { throw 'Installation did not finish. See the installation window for details.' } } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 } }"
echo.
echo If Windows requested a restart, restart and open this file again.
pause
goto menu
:uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Write-Host 'Downloading Shtab.AI uninstaller...'; $p=Join-Path $env:TEMP ('shtab-remove-'+[guid]::NewGuid().ToString('N')+'.ps1'); try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Uninstall-ShtabAI-Windows.ps1' -OutFile $p; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -KeepWindowOpen; if ($LASTEXITCODE -ne 0) { throw 'Removal did not finish. See the removal window for details.' } } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 } }"
pause
goto menu

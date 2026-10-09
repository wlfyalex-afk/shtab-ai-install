@echo off
chcp 65001 >nul
setlocal
title Штаб.AI — Windows
:menu
cls
echo Штаб.AI
echo.
echo 1. Установить — выбрать папку, ускорение и доступ по сети
echo 2. Удалить — выбрать установку Штаб.AI для удаления
echo 0. Выход
echo.
choice /c 120 /n /m "Выберите [1/2/0]: "
if errorlevel 3 exit /b 0
if errorlevel 2 goto uninstall
goto install
:install
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Write-Host 'Скачиваем установщик Штаб.AI...'; $p=Join-Path $env:TEMP ('shtab-install-'+[guid]::NewGuid().ToString('N')+'.ps1'); try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Install-ShtabAI-Windows.ps1' -OutFile $p; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -KeepWindowOpen; if ($LASTEXITCODE -ne 0) { throw 'Установка не завершена. Подробности приведены в окне установки.' } } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 } }"
echo.
echo Если установщик попросил перезагрузку, перезагрузите Windows и снова откройте этот файл.
pause
goto menu
:uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "& { $ErrorActionPreference='Stop'; try { [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12; Write-Host 'Скачиваем программу удаления Штаб.AI...'; $p=Join-Path $env:TEMP ('shtab-remove-'+[guid]::NewGuid().ToString('N')+'.ps1'); try { Invoke-WebRequest -UseBasicParsing -TimeoutSec 60 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Uninstall-ShtabAI-Windows.ps1' -OutFile $p; & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p -KeepWindowOpen; if ($LASTEXITCODE -ne 0) { throw 'Удаление не завершено. Подробности приведены в окне удаления.' } } finally { Remove-Item -LiteralPath $p -Force -ErrorAction SilentlyContinue } } catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 } }"
pause
goto menu

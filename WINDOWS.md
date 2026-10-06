# Штаб.AI — свободная тестовая сборка 0.21

Распознавание аудио совещаний, стенограмма, бриф, поручения и отчёты PDF/XLSX.
Установка скачивает приложение, зависимости и модели автоматически. GitHub-токен не нужен.
Сборка предназначена для испытаний; полная проверка установки на чистой Windows ещё не завершена.

## Windows: одна команда

Windows 10/11 Pro/Enterprise/Education или Windows Server 2019+, x64.
Откройте **Windows PowerShell от имени администратора** и вставьте:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP 'Install-ShtabAI.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Install-ShtabAI.ps1' -OutFile $p; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
```

Установщик сам включает Hyper-V, при необходимости скачивает и устанавливает Multipass
по официальной ссылке Canonical, скачивает и распаковывает пакет в Downloads, проверяет
контрольные суммы, создаёт Ubuntu 24.04 LTS и устанавливает всё внутри VM.
При необходимости перезагрузки повторите ту же команду после перезагрузки.

Нужно **15 GB свободной RAM**. VM получает 4 CPU, 12 GB RAM и диск 120 GB;
предусмотрите место под виртуальный диск. Windows Home и ARM64 не поддерживаются.
Совместимость Multipass с Windows Server 2022 проверяется в ходе испытания.

В конце создайте администратора по приглашению установщика. Браузер откроет
**https://shtab-ai-test.local:8443**. Сертификат добавляется в доверенные текущего
пользователя Windows. VM запускается при входе в Windows, смена её IP обрабатывается
раз в минуту. Браузер открывается автоматически в конце установки.

Рабочие VM не удаляются. Повторная установка в уже созданную VM останавливается;
для ещё одной тестовой VM запустите скачанный скрипт с
`-VMName shtab-ai-test2 -HTTPSPort 8444`.

## Ubuntu: одна команда

Чистая **Ubuntu 24.04 LTS amd64**, минимум 4 CPU, 12 GB RAM и 30 GiB свободного
места в /opt. Команда сама установит загрузчик, если его нет:

```bash
sudo bash -c 'set -e; apt-get update; DEBIAN_FRONTEND=noninteractive apt-get install -y ca-certificates curl; f=$(mktemp); trap '\''rm -f "$f"'\'' EXIT; curl --fail --location --retry 3 --proto "=https" --proto-redir "=https" https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/bootstrap-linux.sh -o "$f"; bash "$f"'
```

Установщик дождётся готовности и предложит создать администратора.
Сертификат добавляется в системное доверие этой Ubuntu; браузеру на другом
компьютере потребуется сертификат установки. Другие дистрибутивы пока не поддерживаются.

## Откат ручной установки Multipass

PowerShell администратора:

```powershell
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; $p = Join-Path $env:TEMP 'Uninstall-Multipass.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Uninstall-Multipass.ps1' -OutFile $p; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
```

Удаление выполняется штатным MSI-деинсталлятором только при пустом списке VM
Multipass. Hyper-V и VM других средств управления сохраняются.

## Диагностика

Windows:

```powershell
multipass exec shtab-ai-test -- sudo /opt/shtab-ai-021/shtabctl progress
multipass exec shtab-ai-test -- sudo journalctl -u shtab-ai-install -n 100
```

Ubuntu:

```bash
sudo /opt/shtab-ai-021/shtabctl progress
sudo journalctl -u shtab-ai-install -n 100
```

## Лицензия

Наш код этой тестовой сборки распространяется по MIT. Сторонние компоненты,
шрифты и скачиваемые модели сохраняют собственные лицензии. Будущие версии
и новые функции могут распространяться на других условиях.

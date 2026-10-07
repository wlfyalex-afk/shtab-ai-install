# Штаб.AI — свободная тестовая сборка 0.21

Распознавание аудио совещаний, стенограмма, бриф, поручения и отчёты PDF/XLSX.
Установка скачивает приложение, зависимости и модели автоматически. GitHub-токен не нужен.
Сборка предназначена для испытаний; полная проверка установки на чистой Windows ещё не завершена.

## Windows: одна команда

Windows 10/11 Pro (включая Pro for Workstations и Pro Education) или Windows Server 2019+, x64.
Откройте **Windows PowerShell от имени администратора** и вставьте:

```powershell
$p = Join-Path $env:TEMP 'Install-ShtabAI-Windows.ps1'; Invoke-WebRequest -UseBasicParsing 'https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/generic-ubuntu-hyperv/Install-ShtabAI-Windows.ps1' -OutFile $p; powershell.exe -NoProfile -ExecutionPolicy Bypass -File $p
```

Установщик выбирает ветку автоматически:

| Система | Действия |
| --- | --- |
| Windows Pro | Включает Hyper-V при необходимости; создаёт Ubuntu VM напрямую средствами Hyper-V. |
| Windows Server с Hyper-V | Создаёт Ubuntu VM напрямую средствами Hyper-V. Multipass не устанавливает и не запускает. |
| Windows Server без Hyper-V | Добавляет роль Hyper-V с инструментами управления; после перезагрузки повторная команда продолжает серверную установку. |

Пакет скачивается и распаковывается в Downloads; контрольные суммы проверяются.
Зависимости и модели Штаб.AI устанавливаются автоматически внутри VM.
Если Windows требует перезагрузку, сохраните работу, перезагрузитесь и повторите ту же команду.

На Server автоматически добавляется клиент OpenSSH, если он отсутствует. Используется
единственный существующий внешний коммутатор. Если внешнего коммутатора нет, создаётся
отдельная внутренняя сеть с NAT; физический адаптер Windows не перенастраивается.
При нескольких внешних коммутаторах выбор задаётся параметром `-VMSwitchName`.
По умолчанию диск VM размещается в каталоге дисков Hyper-V; другой каталог можно
задать через `-VMRoot D:\ShtabAI\shtab-ai-test`.

Нужно **15 GB свободной RAM**. VM получает 4 CPU, 12 GB RAM и диск 120 GB;
предусмотрите место под виртуальный диск. Windows Home и ARM64 не поддерживаются.
Серверная ветка реализована; полное испытание на Windows Server ещё не завершено.
Первый запуск Ubuntu требует доступного DHCP во внешней сети; внутренняя NAT-сеть
получает адрес автоматически из свободной подсети.

После первичной настройки Ubuntu автоматически перезагружается один раз. Установщик ждёт завершения перезагрузки, cloud-init и запуска службы Hyper-V KVP; ручной вход в Ubuntu не нужен.

В конце создайте администратора по приглашению установщика. Браузер откроет
**https://shtab-ai.local:8445**. Сертификат добавляется в доверенные текущего
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

Windows Pro (Multipass):

```powershell
multipass exec shtab-ai-test -- sudo /opt/shtab-ai-021/shtabctl progress
multipass exec shtab-ai-test -- sudo journalctl -u shtab-ai-install -n 100
```


Windows Server (Hyper-V):

```powershell
Get-VM -Name shtab-ai-test
Get-VMNetworkAdapter -VMName shtab-ai-test | Select-Object -ExpandProperty IPAddresses
```

Для интерактивной диагностики Ubuntu используйте `vmconnect.exe localhost shtab-ai-test`.
Если запуск завершился ошибкой, VM и её диск сохраняются; установщик не удаляет их автоматически.

Ubuntu:

```bash
sudo /opt/shtab-ai-021/shtabctl progress
sudo journalctl -u shtab-ai-install -n 100
```

## Лицензия

Наш код этой тестовой сборки распространяется по MIT. Сторонние компоненты,
шрифты и скачиваемые модели сохраняют собственные лицензии. Будущие версии
и новые функции могут распространяться на других условиях.

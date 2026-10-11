#!/bin/bash
set -euo pipefail
ctl=/opt/shtab-ai/shtabctl
while [[ -f $ctl ]]; do
    cat <<'MENU'

Штаб.AI — управление установкой
  1  Статус сервисов
  2  Диагностика
  3  Журнал установки
  4  Проверить компоненты
  5  Создать резервную копию
  6  Список резервных копий
  7  Восстановить копию
  8  Очистить всю БД и записи (включая пользователей)
  9  Запустить сервисы
 10  Остановить сервисы
 11  Создать первого администратора
 12  Проверить HTTPS
 13  Экспортировать корневой сертификат
 14  Удалить установку
 15  Сохранить диагностику для поддержки
 16  Наблюдение за установкой: прогресс, кэш и нагрузка
 17  Продолжить установку и завершить настройку
  0  Выход
MENU
    read -r -p 'Команда: ' choice || exit 0
    args=()
    case "$choice" in
      0) exit 0 ;;
      1) args=(status) ;;
      2) args=(diagnose) ;;
      3) args=(logs) ;;
      4) args=(check) ;;
      5) args=(backup) ;;
      6) args=(backups) ;;
      7)
        echo 'Восстановление из КАТАЛОГА РЕЗЕРВНОЙ КОПИИ.'
        echo 'Каталог приложения /opt/shtab-ai указывать не нужно.'
        if ! listing=$("$ctl" backups); then
            echo 'Не удалось получить список копий. Сообщения выше.'
            continue
        fi
        copies=()
        if [[ -n $listing ]]; then
            mapfile -t copies <<< "$listing"
            for i in "${!copies[@]}"; do
                printf '  %d  %s\n' "$((i+1))" "${copies[i]}"
            done
        else
            echo 'В стандартном каталоге завершённых копий нет.'
        fi
        echo 'Можно выбрать номер или ввести полный путь к копии на другом диске.'
        read -r -p 'Номер копии / полный путь (0 или Enter — отмена): ' selection || exit 0
        [[ -n $selection && $selection != 0 ]] || continue
        if [[ $selection =~ ^[0-9]+$ ]]; then
            path=''
            for i in "${!copies[@]}"; do
                if [[ $selection == "$((i+1))" ]]; then path=${copies[i]}; break; fi
            done
            [[ -n $path ]] || { echo 'Нет копии с таким номером.'; continue; }
        elif [[ $selection == /* ]]; then
            path=$selection
        else
            echo 'Введите номер из списка или полный путь, начинающийся с /.'
            continue
        fi
        [[ -d $path && -f $path/manifest.json ]] || {
            echo 'Это не каталог завершённой резервной копии: не найден manifest.json.'
            continue
        }
        printf 'Выбрана копия: %s\n' "$path"
        echo 'Далее: проверка копии → подтверждение → страховочная копия → восстановление.'
        args=(restore "$path") ;;
      8) args=(reset-db) ;;
      9) args=(start) ;;
      10) args=(stop) ;;
      11) args=(bootstrap) ;;
      12) args=(https-check) ;;
      13) args=(certificate) ;;
      15) args=(support) ;;
      16) args=(monitor) ;;
      17) args=(resume) ;;
      14)
        if [[ -f /opt/shtab-ai/storage.json ]]; then
            cd /
            exec bash /opt/shtab-ai/scripts/full-uninstall.sh
        fi
        if [[ -f /opt/shtab-ai/windows-acceleration ]]; then
            echo 'Для полного удаления вернитесь в Windows.'
            echo 'Откройте Start-ShtabAI-Windows.cmd и выберите 2 — Удалить.'
            echo 'Если файла нет, скачайте: https://raw.githubusercontent.com/wlfyalex-afk/shtab-ai-install/main/Start-ShtabAI-Windows.cmd'
            echo 'Программа сама загрузит удаление; отдельный PowerShell-скрипт искать не нужно. Внешний кэш и резервные копии сохранятся.'
            read -r -p 'Enter — вернуться в меню' _ || exit 0
            continue
        fi
        echo '1 — удалить контейнеры, сохранить данные и конфигурацию'
        echo '2 — новая пустая установка, сохранить скачанные модели'
        echo '3 — удалить данные и скачанные модели'
        read -r -p 'Режим (0 — отмена): ' mode || exit 0
        case "$mode" in
          1)
            read -r -p 'Для остановки и удаления контейнеров введите STOP-SHTAB: ' answer || exit 0
            [[ $answer == STOP-SHTAB ]] || continue
            args=(uninstall --keep-data) ;;
          2) args=(uninstall --fresh) ;;
          3) args=(uninstall --purge-all) ;;
          *) continue ;;
        esac ;;
      *) echo 'Неизвестная команда'; continue ;;
    esac
    # Run as a separate process so its errexit/pipefail remain effective.
    if "$ctl" "${args[@]}"; then
        echo 'Команда завершена.'
    else
        echo 'Команда завершилась ошибкой. Сообщения выше; автоматический повтор не выполняется.'
    fi
    [[ ${args[0]} == uninstall ]] && exit 0
    read -r -p 'Enter — вернуться в меню' _ || exit 0
done

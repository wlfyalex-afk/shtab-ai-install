"""Describe the current operation from bounded installer journal output."""
import re


TITLES = {
    'INSTALLING_DEPENDENCIES': ('Установка зависимостей', 'Устанавливаем Docker и системные пакеты Ubuntu.'),
    'BUILDING_APP': ('Сборка образа приложения', 'Готовим Python, ffmpeg и библиотеки приложения. Это может занять несколько минут.'),
    'INITIALIZING_DATABASE': ('Подготовка базы данных', 'Запускаем PostgreSQL и подготавливаем хранилища.'),
    'CHECKING_DATABASE_AND_MODELS': ('Проверка компонентов', 'Проверяем базу, Qwen и пробное распознавание Whisper.'),
    'STARTING_SERVICES': ('Запуск приложения', 'Ожидаем готовности веб-интерфейса и обработчиков.'),
    'READY_FOR_ADMIN': ('Сервисы готовы', 'Переходим к настройке сертификата и созданию администратора.'),
}


def build_operation(journal):
    title, text = TITLES['BUILDING_APP']
    percent = -1
    for line in journal.splitlines():
        # BuildKit identifies layers with long digests; users need the action
        # and transferred bytes, rather than the digest itself.
        amount = re.search(r'sha256:[a-f0-9]+\s+([\d.]+)\s*(B|kB|KB|MB|GB)\s*/\s*([\d.]+)\s*(B|kB|KB|MB|GB)', line)
        if amount:
            done, unit, total, total_unit = amount.groups()
            factors = {'B': 1, 'kB': 1000, 'KB': 1000, 'MB': 1000000, 'GB': 1000000000}
            completed, size = float(done) * factors[unit], float(total) * factors[total_unit]
            percent = min(100, int(completed * 100 / size)) if size else -1
            title = 'Загрузка слоя базового образа Python'
            text = f'{done} {unit} / {total} {total_unit}'
            if percent >= 0:
                text += f' | {percent}% текущего слоя'
            continue
        message = re.sub(r'^#\d+\s+(?:[\d.]+\s+)?', '', line).strip()
        if 'extracting sha256:' in message:
            title, text, percent = 'Распаковка базового образа', 'Распаковываем загруженный слой Python.', -1
        elif message.startswith(('Downloading ', 'Collecting ')):
            package = message.split(maxsplit=1)[1].split(' (')[0].split(' from ')[0]
            package = package.rsplit('/', 1)[-1].split('?')[0][:110]
            title, text, percent = 'Установка библиотек Python', 'Получаем: ' + package, -1
        elif 'Installing collected packages:' in message:
            title, text, percent = 'Установка библиотек Python', 'Файлы получены; устанавливаем библиотеки в образ.', -1
        elif re.search(r'\bRUN .*pip install', message):
            title, text, percent = 'Установка библиотек Python', 'Подготавливаем библиотеки для распознавания и веб-интерфейса.', -1
        elif re.search(r'\bRUN .*apt-get', message) or message.startswith(('Get:', 'Unpacking ', 'Setting up ')):
            title, text, percent = 'Установка ffmpeg и системных библиотек', 'Получаем и устанавливаем пакеты внутри образа.', -1
        elif 'exporting layers' in message or 'exporting to image' in message:
            title, text, percent = 'Сохранение образа приложения', 'Упаковываем готовый образ; далее — база данных и модели.', -1
        elif re.search(r'\bCOPY\b', message):
            title, text, percent = 'Добавление файлов приложения', 'Копируем код и интерфейс в образ.', -1
    return dict(title=title, text=text, percent=percent)


def operation(status, journal=''):
    if status == 'BUILDING_APP':
        return build_operation(journal)
    if status not in TITLES:
        return None
    title, text = TITLES[status]
    return dict(title=title, text=text, percent=-1)

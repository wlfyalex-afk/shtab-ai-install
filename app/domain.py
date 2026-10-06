import hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LABELS = {'YES':'Заявлено исполнение', 'NO':'Не исполнено', 'UNCLEAR':'Неясно'}
TASK_LABELS = {'KEEP':'Не менять', 'CLAIMED_DONE':'Исполнение заявлено',
               'NOT_DONE':'Не исполнено', 'BLOCKED':'Заблокировано', 'UNKNOWN':'Неясно'}

class Conflict(Exception): pass
class Invalid(Exception): pass

def validate_form(form, timezone):
    label = form.get('classification')
    action = form.get('action')
    task_status = form.get('task_status', 'KEEP')
    if label not in LABELS or action not in ('CONFIRM','CORRECT','REJECT') or task_status not in TASK_LABELS:
        raise Invalid('Недопустимое решение.')
    if action != 'REJECT' and form.get('checked_audio') != 'yes':
        raise Invalid('Подтвердите, что прослушали запись и сверили ответ.')
    if action == 'REJECT' and task_status != 'KEEP':
        raise Invalid('При отклонении ответа оставьте статус поручения без изменения.')
    compatible = {'YES':{'KEEP','CLAIMED_DONE'}, 'NO':{'KEEP','NOT_DONE','BLOCKED'},
                  'UNCLEAR':{'KEEP','UNKNOWN'}}
    if task_status not in compatible[label]:
        raise Invalid('Статус поручения противоречит выбранному ответу.')
    data = {}
    for key, maximum in [('corrected_transcript',20000), ('reason',4000), ('due_text',1000), ('comment',4000)]:
        value = form.get(key, '').strip()
        if len(value) > maximum: raise Invalid('Слишком длинный текст: ' + key)
        data[key] = value
    if not data['corrected_transcript'] and action != 'REJECT':
        raise Invalid('Укажите проверенный текст ответа.')
    if action == 'REJECT' and not data['comment']:
        raise Invalid('Укажите причину отклонения.')
    due = form.get('due_at', '').strip()
    data['due_at'] = None
    if due:
        try:
            local = datetime.fromisoformat(due)
            if local.tzinfo is not None: raise ValueError()
            data['due_at'] = local.replace(tzinfo=ZoneInfo(timezone)).isoformat()
        except (ValueError, KeyError):
            raise Invalid('Проверьте дату и время обещанного исполнения.')
    try:
        version = int(form['review_version'])
        if version < 0: raise ValueError()
    except (KeyError, ValueError): raise Invalid('Некорректная версия карточки. Обновите страницу.')
    data.update(classification=label, timezone=timezone, task_status=task_status,
                checked_audio=form.get('checked_audio') == 'yes', action=action)
    return data, version

def verified_audio(row, roots):
    try:
        path = Path(row['audio_path']).resolve(strict=True)
        if not any(path.is_relative_to(Path(root).resolve()) for root in roots):
            raise Invalid('Запись находится вне разрешённых каталогов.')
        if not path.is_file() or path.suffix.lower() != '.wav' or path.stat().st_size > 32*1024*1024:
            raise Invalid('Недопустимый аудиофайл.')
        data = path.read_bytes()
    except (OSError, RuntimeError):
        raise Invalid('Запись недоступна. Проверьте файл и права чтения.')
    if hashlib.sha256(data).hexdigest() != row['audio_sha256'].strip():
        raise Invalid('Хэш записи не совпадает с БД. Проверка ответа остановлена.')
    return data

"""Portable, additive meeting snapshots; never restore accounts or deployment settings."""
import hashlib
import json
import shutil
import tempfile
import uuid
import wave
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json
from flask import Request, Blueprint, abort, current_app, flash, g, redirect, render_template, request, send_file, url_for

from import_common import ROOT, digest_file
from meeting_review009 import audio_path
from store import plain

VERSION = 1
MAX_PACKAGE = 2 * 1024**3
MAX_JSON = 32 * 1024**2
MAX_ROWS = 50000
bp = Blueprint('meeting_transfer', __name__)
FIELDS = {'people': ['id',
            'organization_id',
            'display_name',
            'role_title',
            'aliases',
            'timezone',
            'active',
            'created_at',
            'updated_at'],
 'meetings': ['id',
              'organization_id',
              'title',
              'meeting_at',
              'source_path',
              'source_sha256',
              'duration_seconds',
              'processing_status',
              'created_at',
              'updated_at'],
 'transcripts': ['id',
                 'organization_id',
                 'meeting_id',
                 'engine',
                 'model',
                 'language',
                 'content',
                 'immutable',
                 'created_at'],
 'tasks': ['id',
           'organization_id',
           'meeting_id',
           'instruction',
           'lifecycle',
           'execution_status',
           'primary_assignee_id',
           'due_text',
           'due_at',
           'due_resolution',
           'source_spans',
           'machine_confidence',
           'review_status',
           'approved_by',
           'approved_at',
           'created_at',
           'updated_at'],
 'task_assignees': ['task_id', 'person_id', 'role'],
 'task_comments': ['id', 'organization_id', 'task_id', 'author_id', 'author_role', 'body', 'created_at'],
 'meeting_task_drafts': ['id',
                         'organization_id',
                         'meeting_id',
                         'transcript_id',
                         'segment_index',
                         'instruction',
                         'source_quote',
                         'start_seconds',
                         'end_seconds',
                         'method',
                         'status',
                         'task_id',
                         'version',
                         'reviewed_by',
                         'reviewed_at',
                         'created_at',
                         'candidate_key',
                         'model_analysis',
                         'rejection_reason'],
 'meeting_brief_jobs': ['id',
                        'organization_id',
                        'meeting_id',
                        'transcript_id',
                        'method',
                        'model',
                        'model_digest',
                        'source_hash',
                        'status',
                        'next_chunk',
                        'total_chunks',
                        'evidence_count',
                        'rejected_count',
                        'error_code',
                        'created_by',
                        'created_at',
                        'updated_at',
                        'final_elapsed_seconds',
                        'retry_count',
                        'last_stage',
                        'last_done_reason',
                        'last_prompt_tokens',
                        'last_output_tokens',
                        'last_input_bytes',
                        'last_output_bytes',
                        'last_attempt_at'],
 'meeting_brief_chunks': ['job_id',
                          'chunk_no',
                          'response',
                          'evidence',
                          'elapsed_seconds',
                          'rejected_count',
                          'created_at'],
 'meeting_briefs': ['id',
                    'organization_id',
                    'meeting_id',
                    'transcript_id',
                    'job_id',
                    'method',
                    'model',
                    'model_digest',
                    'content',
                    'review_status',
                    'approved_by',
                    'approved_at',
                    'review_comment',
                    'created_at'],
 'meeting_llm_jobs': ['id',
                      'organization_id',
                      'meeting_id',
                      'transcript_id',
                      'method',
                      'model',
                      'model_digest',
                      'source_hash',
                      'status',
                      'next_chunk',
                      'total_chunks',
                      'created_count',
                      'rejected_count',
                      'error_code',
                      'created_at',
                      'updated_at'],
 'meeting_llm_chunks': ['job_id',
                        'chunk_no',
                        'response',
                        'elapsed_seconds',
                        'created_count',
                        'rejected_count',
                        'created_at']}
JSON_FIELDS = {'people': ['aliases'],
 'meetings': [],
 'transcripts': ['content'],
 'tasks': ['source_spans'],
 'task_assignees': [],
 'task_comments': [],
 'meeting_task_drafts': ['model_analysis'],
 'meeting_brief_jobs': [],
 'meeting_brief_chunks': ['response', 'evidence'],
 'meeting_briefs': ['content'],
 'meeting_llm_jobs': [],
 'meeting_llm_chunks': ['response']}
# Fixed table order satisfies foreign keys. All referenced people are copied as
# contacts with new IDs; login accounts/passwords are never part of a snapshot.
REFS = {
    'meeting_llm_jobs': {'meeting_id': 'meetings', 'transcript_id': 'transcripts'},
    'meeting_llm_chunks': {'job_id': 'meeting_llm_jobs'},
    'transcripts': {'meeting_id': 'meetings'},
    'tasks': {'meeting_id': 'meetings', 'primary_assignee_id': 'people', 'approved_by': 'people'},
    'task_assignees': {'task_id': 'tasks', 'person_id': 'people'},
    'task_comments': {'task_id': 'tasks', 'author_id': 'people'},
    'meeting_task_drafts': {'meeting_id': 'meetings', 'transcript_id': 'transcripts', 'task_id': 'tasks', 'reviewed_by': 'people'},
    'meeting_brief_jobs': {'meeting_id': 'meetings', 'transcript_id': 'transcripts', 'created_by': 'people'},
    'meeting_brief_chunks': {'job_id': 'meeting_brief_jobs'},
    'meeting_briefs': {'meeting_id': 'meetings', 'transcript_id': 'transcripts', 'job_id': 'meeting_brief_jobs', 'approved_by': 'people'},
}


class PackageError(ValueError):
    pass


class TransferRequest(Request):
    def _get_file_stream(self, total_content_length, content_type, filename=None, content_length=None):
        if self.endpoint == 'meeting_transfer.import_meeting':
            # /tmp is tmpfs in the production container. Large archives must be
            # streamed to the persistent data volume, not consume web RAM.
            root = Path(current_app.config.get('IMPORT_ROOT', ROOT))
            root.mkdir(parents=True, exist_ok=True)
            return tempfile.TemporaryFile(dir=root)
        return super()._get_file_stream(total_content_length, content_type, filename, content_length)


def uuid_text(value):
    try:
        if not isinstance(value, str) or str(uuid.UUID(value)) != value:
            raise ValueError()
        return value
    except (ValueError, TypeError, AttributeError):
        raise PackageError('Некорректный идентификатор в архиве.') from None


def validate_records(data):
    if not isinstance(data, dict) or set(data) != {'organization_id', 'records'}:
        raise PackageError('Некорректные данные совещания.')
    org = uuid_text(data['organization_id'])
    records = data['records']
    if not isinstance(records, dict) or set(records) != set(FIELDS):
        raise PackageError('Состав архива не соответствует формату переноса совещания.')
    ids = {}; seen = set(); count = 0
    for table, fields in FIELDS.items():
        rows = records[table]
        if not isinstance(rows, list):
            raise PackageError('Некорректный список записей.')
        count += len(rows)
        if count > MAX_ROWS:
            raise PackageError('Слишком много записей в архиве.')
        ids[table] = set()
        for row in rows:
            if not isinstance(row, dict) or set(row) != set(fields):
                raise PackageError('Версия полей архива не поддерживается.')
            if 'organization_id' in row and row['organization_id'] != org:
                raise PackageError('Архив содержит данные другой организации.')
            if 'id' in fields:
                identifier = uuid_text(row['id'])
                if identifier in seen:
                    raise PackageError('Повторяющийся идентификатор в архиве.')
                seen.add(identifier); ids[table].add(identifier)
    if len(records['meetings']) != 1 or not records['transcripts']:
        raise PackageError('Нужен архив одного распознанного совещания со стенограммой.')
    meeting = records['meetings'][0]
    if meeting['processing_status'] not in ('REVIEW', 'COMPLETED'):
        raise PackageError('Дождитесь завершения обработки совещания.')
    digest = meeting['source_sha256']
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise PackageError('Некорректная контрольная сумма исходной записи.')
    for table, refs in REFS.items():
        for row in records[table]:
            for key, target in refs.items():
                value = row[key]
                if value is not None and uuid_text(value) not in ids[target]:
                    raise PackageError('Нарушена связь между записями архива.')
    for job in records['meeting_brief_jobs'] + records['meeting_llm_jobs']:
        if job['status'] not in ('DONE', 'FAILED', 'CANCELLED'):
            raise PackageError('Дождитесь завершения подготовки брифа.')
    for table, columns in JSON_FIELDS.items():
        for row in records[table]:
            for column in columns:
                if not isinstance(row[column], (dict, list)):
                    raise PackageError('Некорректное содержимое результатов.')
    return records


def snapshot(store, mid, org):
    records = {table: [] for table in FIELDS}
    with store.connection() as connection, connection.cursor() as cur:
        cur.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
        cur.execute('SELECT * FROM meetings WHERE id=%s AND organization_id=%s', (str(mid), org))
        meeting = cur.fetchone()
        if not meeting:
            return None
        records['meetings'] = [meeting]
        # Avoid exporting an in-flight extraction while its result set changes.
        for table in ('meeting_imports', 'meeting_llm_jobs'):
            states = ['UPLOADING', 'QUEUED', 'FETCHING', 'ASSEMBLING', 'CONVERTING', 'TRANSCRIBING'] if table == 'meeting_imports' else ['QUEUED', 'RUNNING']
            cur.execute(sql.SQL('SELECT id FROM {} WHERE meeting_id=%s AND organization_id=%s AND status=ANY(%s) LIMIT 1').format(sql.Identifier(table)), (str(mid), org, states))
            if cur.fetchone():
                raise PackageError('Дождитесь завершения обработки совещания.')
        for table in ('transcripts', 'tasks', 'meeting_task_drafts', 'meeting_brief_jobs', 'meeting_briefs', 'meeting_llm_jobs'):
            cur.execute(sql.SQL('SELECT * FROM {} WHERE meeting_id=%s AND organization_id=%s ORDER BY id').format(sql.Identifier(table)), (str(mid), org))
            records[table] = cur.fetchall()
        task_ids = [str(row['id']) for row in records['tasks']]
        for table in ('task_assignees', 'task_comments'):
            cur.execute(sql.SQL('SELECT * FROM {} WHERE task_id=ANY(%s::uuid[])').format(sql.Identifier(table)), (task_ids,))
            records[table] = cur.fetchall()
        llm_ids = [str(row['id']) for row in records['meeting_llm_jobs']]
        cur.execute('SELECT * FROM meeting_llm_chunks WHERE job_id=ANY(%s::uuid[]) ORDER BY job_id,chunk_no', (llm_ids,))
        records['meeting_llm_chunks'] = cur.fetchall()
        job_ids = [str(row['id']) for row in records['meeting_brief_jobs']]
        cur.execute('SELECT * FROM meeting_brief_chunks WHERE job_id=ANY(%s::uuid[]) ORDER BY job_id,chunk_no', (job_ids,))
        records['meeting_brief_chunks'] = cur.fetchall()
        people = {str(row[key]) for table, refs in REFS.items() for row in records[table] for key, target in refs.items() if target == 'people' and row[key] is not None}
        cur.execute('SELECT * FROM people WHERE id=ANY(%s::uuid[]) AND organization_id=%s ORDER BY id', (list(people), org))
        records['people'] = cur.fetchall()
    data = plain({'organization_id': org, 'records': records})
    validate_records(data)
    return data


def write_package(output, data, audio=None):
    validate_records(data)
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(payload) > MAX_JSON:
        raise PackageError('Результаты совещания превышают допустимый объём архива.')
    files = {'meeting.json': {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}}
    if audio is not None:
        if audio.stat().st_size > MAX_PACKAGE - MAX_JSON:
            raise PackageError('Аудиозапись слишком велика. Экспортируйте результаты без аудио.')
        files['audio-16k.wav'] = {'size': audio.stat().st_size, 'sha256': digest_file(audio)}
    manifest = {'product': 'ShtabAI-Meeting', 'version': VERSION, 'created_at': datetime.now(timezone.utc).isoformat(), 'files': files}
    with zipfile.ZipFile(output, 'w', allowZip64=True) as archive:
        archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False).encode())
        archive.writestr('meeting.json', payload, compress_type=zipfile.ZIP_DEFLATED)
        if audio is not None:
            # WAV is streamed, without a slow compression pass in a web worker.
            archive.write(audio, 'audio-16k.wav', compress_type=zipfile.ZIP_STORED)
    output.seek(0)


def read_package(source, staging):
    try:
        with zipfile.ZipFile(source) as archive:
            entries = archive.infolist(); names = [entry.filename for entry in entries]
            if len(names) != len(set(names)) or not {'manifest.json', 'meeting.json'} <= set(names) or not set(names) <= {'manifest.json', 'meeting.json', 'audio-16k.wav'}:
                raise PackageError('Это не архив переноса совещания или в нём есть лишние файлы.')
            if sum(entry.file_size for entry in entries) > MAX_PACKAGE or any(entry.flag_bits & 1 for entry in entries):
                raise PackageError('Архив слишком велик или зашифрован.')
            if archive.getinfo('manifest.json').file_size > 64000 or archive.getinfo('meeting.json').file_size > MAX_JSON:
                raise PackageError('Описание архива слишком велико.')
            manifest = json.loads(archive.read('manifest.json'))
            if not isinstance(manifest, dict) or manifest.get('product') != 'ShtabAI-Meeting' or type(manifest.get('version')) is not int or manifest['version'] != VERSION:
                raise PackageError('Версия архива не поддерживается.')
            files = manifest.get('files')
            if not isinstance(files, dict) or set(files) != set(names) - {'manifest.json'}:
                raise PackageError('Неполное описание архива.')
            for name, meta in files.items():
                if not isinstance(meta, dict) or meta.get('size') != archive.getinfo(name).file_size:
                    raise PackageError('Размер файла не соответствует описанию.')
                digest = hashlib.sha256()
                with archive.open(name) as src, (staging / name).open('xb') as dst:
                    for chunk in iter(lambda: src.read(1024 * 1024), b''):
                        digest.update(chunk); dst.write(chunk)
                if digest.hexdigest() != meta.get('sha256'):
                    raise PackageError('Контрольная сумма не совпала. Архив повреждён.')
            data = json.loads((staging / 'meeting.json').read_text(encoding='utf-8'))
            validate_records(data)
            audio = staging / 'audio-16k.wav'
            if audio.exists():
                with wave.open(str(audio), 'rb') as media:
                    if (media.getnchannels(), media.getframerate(), media.getsampwidth()) != (1, 16000, 2):
                        raise PackageError('Аудиозапись имеет неподдерживаемый формат.')
                    expected = media.getnframes() * 2
                    actual = 0
                    while chunk := media.readframes(512 * 1024):
                        actual += len(chunk)
                    if actual != expected:
                        raise PackageError('Аудиозапись обрезана.')
            return data
    except (zipfile.BadZipFile, json.JSONDecodeError, UnicodeError, wave.Error, EOFError, RuntimeError, RecursionError, NotImplementedError) as exc:
        raise PackageError('Не удалось прочитать архив переноса совещания.') from exc


def remap_json(value, mapping):
    if isinstance(value, dict):
        return {key: remap_json(item, mapping) for key, item in value.items()}
    if isinstance(value, list):
        return [remap_json(item, mapping) for item in value]
    return mapping.get(value, value) if isinstance(value, str) else value


def import_package(store, source, actor, root=ROOT):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    destination = None
    created = False
    try:
        with tempfile.TemporaryDirectory(prefix='.meeting-transfer-', dir=root) as folder:
            staging = Path(folder)
            data = read_package(source, staging)
            records = data['records']; meeting = records['meetings'][0]
            mapping = {row['id']: str(uuid.uuid4()) for table, rows in records.items() for row in rows if 'id' in FIELDS[table]}
            mid = mapping[meeting['id']]
            destination = root / mid
            with store.connection() as connection, connection.cursor() as cur:
                # Serialise duplicate checks without a new database schema/table.
                cur.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (str(actor['organization_id']) + ':' + meeting['source_sha256'],))
                cur.execute('SELECT id FROM meetings WHERE organization_id=%s AND source_sha256=%s', (actor['organization_id'], meeting['source_sha256']))
                if cur.fetchone():
                    raise PackageError('Эта запись уже есть в текущей организации. Повторный импорт отменён.')
                destination.mkdir(mode=0o700)
                created = True
                if (staging / 'audio-16k.wav').exists():
                    shutil.move(str(staging / 'audio-16k.wav'), destination / 'audio-16k.wav')
                for table, fields in FIELDS.items():
                    rows = records[table]
                    for original in rows:
                        row = dict(original)
                        if 'id' in fields:
                            row['id'] = mapping[row['id']]
                        if 'organization_id' in fields:
                            row['organization_id'] = str(actor['organization_id'])
                        for key in REFS.get(table, {}):
                            if row[key] is not None:
                                row[key] = mapping[row[key]]
                        if table == 'meetings':
                            row['source_path'] = str(destination / 'portable-source')
                        for key in JSON_FIELDS[table]:
                            row[key] = Json(remap_json(row[key], mapping))
                        cur.execute(sql.SQL('INSERT INTO {} ({}) VALUES ({})').format(sql.Identifier(table), sql.SQL(',').join(map(sql.Identifier, fields)), sql.SQL(',').join(sql.Placeholder() for _ in fields)), [row[key] for key in fields])
                cur.execute("INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload) VALUES (%s,%s,'USER',%s,'MEETING_IMPORTED','MEETING',%s,%s)", (str(uuid.uuid4()), actor['organization_id'], actor['person_id'], mid, Json({'source_meeting_id': meeting['id'], 'format_version': VERSION})))
            return mid
    except Exception:
        if created and destination is not None and destination.exists():
            shutil.rmtree(destination)
        raise


def require_editor():
    if not g.user or (not g.user.get('is_admin') and g.user.get('app_role', 'secretary') != 'secretary'):
        abort(403)


@bp.get('/meetings/<uuid:mid>/transfer')
def export_form(mid):
    require_editor()
    with current_app.store.connection() as c, c.cursor() as cur:
        cur.execute('SELECT id,title FROM meetings WHERE id=%s AND organization_id=%s', (str(mid), g.user['organization_id']))
        meeting = cur.fetchone()
        if not meeting:
            abort(404)
    return render_template('meeting_transfer.html', meeting=meeting)


@bp.post('/meetings/<uuid:mid>/transfer')
def export_meeting(mid):
    require_editor()
    root = Path(current_app.config.get('IMPORT_ROOT', ROOT))
    root.mkdir(parents=True, exist_ok=True)
    output = tempfile.TemporaryFile(dir=root)
    try:
        data = snapshot(current_app.store, mid, g.user['organization_id'])
        if data is None:
            abort(404)
        audio = None
        if request.form.get('include_audio') == 'yes':
            try:
                audio = audio_path(data['records']['meetings'][0]['source_path'], Path(current_app.config.get('IMPORT_ROOT', ROOT)))
            except (ValueError, wave.Error, OSError):
                raise PackageError('Аудио недоступно. Можно экспортировать результаты без аудио.') from None
        write_package(output, data, audio)
        response = send_file(output, mimetype='application/zip', as_attachment=True, download_name=f'shtab-meeting-transfer-{mid}.zip', conditional=False)
        response.call_on_close(output.close)
        return response
    except PackageError as exc:
        output.close(); flash(str(exc))
        return redirect(url_for('meeting_transfer.export_form', mid=mid), 303)
    except Exception:
        output.close(); raise


@bp.route('/meetings/import-package', methods=['GET', 'POST'])
def import_meeting():
    require_editor()
    if request.method == 'POST':
        if request.form.get('confirm') != 'yes':
            flash('Подтвердите добавление совещания в текущую организацию.')
        elif not request.files.get('package'):
            flash('Выберите архив переноса совещания.')
        else:
            try:
                mid = import_package(current_app.store, request.files['package'].stream, g.user, current_app.config.get('IMPORT_ROOT', ROOT))
            except PackageError as exc:
                flash(str(exc))
            except OSError:
                flash('Не удалось сохранить файлы. Изменения отменены; проверьте свободное место.')
            except psycopg2.Error:
                flash('Данные архива не удалось записать. Изменения отменены; проверьте совместимость версий приложения.')
            else:
                flash('Совещание добавлено. Существующие записи сохранены; повторное распознавание не требуется.')
                return redirect(url_for('workflow0182.status', mid=mid), 303)
        return redirect(url_for('meeting_transfer.import_meeting'), 303)
    return render_template('meeting_transfer.html', meeting=None)


def register(app):
    app.request_class = TransferRequest
    app.register_blueprint(bp)

"""Administrator-only deletion of exactly one recording or multipart assembly."""
import logging
import shutil
import uuid
from pathlib import Path

from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, url_for
from psycopg2.extras import Json
from import_common import ROOT

bp = Blueprint('meeting_delete', __name__)


class DeleteBusy(Exception):
    pass


def require_admin():
    if not g.get('user') or not g.user.get('is_admin'):
        abort(403)


def selection(cur, kind, uid, organization):
    tables = {'meeting': 'meetings', 'import': 'meeting_imports', 'group': 'meeting_import_groups'}
    if kind not in tables:
        abort(404)
    cur.execute(f'SELECT * FROM {tables[kind]} WHERE id=%s AND organization_id=%s FOR UPDATE', (str(uid), organization))
    row = cur.fetchone()
    if not row:
        abort(404)
    # A fragment is always deleted through its whole assembly.
    if kind == 'import' and row['group_id']:
        return selection(cur, 'group', row['group_id'], organization)
    if kind == 'meeting':
        cur.execute('''SELECT x.group_id FROM meeting_imports x JOIN meeting_import_groups b ON b.primary_import_id=x.id
            WHERE x.meeting_id=%s AND x.organization_id=%s AND x.status<>'DUPLICATE' LIMIT 1''', (str(uid), organization))
        owner = cur.fetchone()
        if owner:
            return selection(cur, 'group', owner['group_id'], organization)
    return kind, row


def delete_recording(store, organization, actor, kind, uid, root, audio_roots=None):
    """Move files aside reversibly, delete dependencies, then remove only those files."""
    root = Path(root).resolve()
    moved = []
    allowed_audio = [Path(p).resolve() for p in (audio_roots or [root, root.parent / 'response-evidence'])]
    try:
        with store.connection() as connection, connection.cursor() as cur:
            # These are the existing recognition/LLM worker locks. No worker can
            # publish a late result or recreate files while deletion is committed.
            for key in (13013013, 8008002, 74004004):
                cur.execute('SELECT pg_try_advisory_xact_lock(%s) AS locked', (key,))
                if not cur.fetchone()['locked']:
                    raise DeleteBusy('Сейчас идёт обработка записей. Повторите удаление после её завершения или приостановки.')
            kind, selected = selection(cur, kind, uid, organization)
            gid = str(selected['id']) if kind == 'group' else None
            mid = str(selected['id']) if kind == 'meeting' else None
            imports = []
            if kind == 'import':
                imports = [selected]
            elif gid:
                cur.execute('SELECT * FROM meeting_imports WHERE group_id=%s AND organization_id=%s ORDER BY group_position FOR UPDATE', (gid, organization))
                imports = cur.fetchall()
            if not mid:
                # A duplicate points to a different, existing recording: retain it.
                owners = [r for r in imports if r['meeting_id'] and r['status'] != 'DUPLICATE']
                if owners:
                    mid = str(owners[0]['meeting_id'])
            meeting = None
            if mid:
                cur.execute('SELECT * FROM meetings WHERE id=%s AND organization_id=%s FOR UPDATE', (mid, organization))
                meeting = cur.fetchone()
                if not meeting:
                    abort(404)
                # Locate the actual source import, not duplicate uploads referring
                # to the same meeting. Portable imports use the meeting ID folder.
                source = Path(meeting['source_path'])
                try:
                    owner = str(uuid.UUID(source.relative_to(root).parts[0]))
                except (ValueError, IndexError):
                    owner = None
                if kind == 'meeting' and owner:
                    cur.execute('SELECT * FROM meeting_imports WHERE id=%s AND organization_id=%s FOR UPDATE', (owner, organization))
                    source_import = cur.fetchone()
                    if source_import:
                        if source_import['group_id']:
                            gid = str(source_import['group_id'])
                            cur.execute('SELECT * FROM meeting_import_groups WHERE id=%s AND organization_id=%s FOR UPDATE', (gid, organization))
                            cur.fetchone()
                            cur.execute('SELECT * FROM meeting_imports WHERE group_id=%s AND organization_id=%s ORDER BY group_position FOR UPDATE', (gid, organization))
                            imports = cur.fetchall()
                        else:
                            imports = [source_import]
                cur.execute('SELECT id FROM tasks WHERE meeting_id=%s AND organization_id=%s FOR UPDATE', (mid, organization))
                tasks = [str(r['id']) for r in cur.fetchall()]
                cur.execute('SELECT id FROM transcripts WHERE meeting_id=%s', (mid,))
                transcripts = [str(r['id']) for r in cur.fetchall()]
                cur.execute('SELECT id FROM meeting_llm_jobs WHERE meeting_id=%s', (mid,))
                llm = [str(r['id']) for r in cur.fetchall()]
                cur.execute('SELECT id FROM meeting_brief_jobs WHERE meeting_id=%s', (mid,))
                briefs = [str(r['id']) for r in cur.fetchall()]
            else:
                tasks, transcripts, llm, briefs = [], [], [], []
            ids = [str(r['id']) for r in imports]
            folders = {root / i for i in ids}
            if meeting:
                source = Path(meeting['source_path'])
                try:
                    source_owner = str(uuid.UUID(source.relative_to(root).parts[0]))
                except (ValueError, IndexError):
                    raise DeleteBusy('Исходная запись находится вне каталога загрузок. Удаление остановлено, чтобы не затронуть посторонние файлы.')
                folders.add(root / source_owner)
                cur.execute('SELECT source_path FROM meetings WHERE id<>%s', (mid,))
            else:
                cur.execute('SELECT source_path FROM meetings')
            for other in cur.fetchall():
                path = Path(other['source_path']).resolve()
                if any(path.is_relative_to(folder) for folder in folders):
                    raise DeleteBusy('Файлы используются другим совещанием. Удаление остановлено.')
            # Never follow symlinks or accept non-UUID directories from a database.
            for folder in sorted(folders):
                if folder.is_symlink() or folder.resolve().parent != root:
                    raise DeleteBusy('Недопустимый каталог записи. Удаление остановлено.')
            # Keep shared call jobs for other meetings; remove exclusive call jobs.
            cur.execute('''SELECT j.id,j.status FROM call_jobs j WHERE EXISTS
                (SELECT 1 FROM call_job_tasks x WHERE x.call_job_id=j.id AND x.task_id=ANY(%s::uuid[])) FOR UPDATE''', (tasks,))
            calls = cur.fetchall()
            if any(r['status'] == 'RUNNING' for r in calls):
                raise DeleteBusy('По поручениям этого совещания идёт обзвон. Дождитесь его завершения.')
            exclusive = []
            for call in calls:
                cur.execute('''SELECT 1 FROM call_job_tasks WHERE call_job_id=%s AND NOT (task_id=ANY(%s::uuid[]))
                    UNION ALL SELECT 1 FROM task_responses WHERE call_job_id=%s AND NOT (task_id=ANY(%s::uuid[])) LIMIT 1''',
                    (str(call['id']), tasks, str(call['id']), tasks))
                if not cur.fetchone():
                    exclusive.append(str(call['id']))
            cur.execute('SELECT id,audio_path FROM task_responses WHERE task_id=ANY(%s::uuid[])', (tasks,))
            response_rows = cur.fetchall()
            responses = [str(r['id']) for r in response_rows]
            evidence = set()
            for response in response_rows:
                path = Path(response['audio_path'])
                if not path.exists():
                    continue
                resolved = path.resolve()
                if path.is_symlink() or not path.is_file() or not any(resolved.is_relative_to(base) for base in allowed_audio):
                    raise DeleteBusy('Недопустимый путь аудиоответа. Удаление остановлено.')
                cur.execute('SELECT 1 FROM task_responses WHERE audio_path=%s AND NOT (id=ANY(%s::uuid[])) LIMIT 1', (str(path), responses))
                if not cur.fetchone() and not any(resolved.is_relative_to(folder) for folder in folders):
                    evidence.add(path)
            cur.execute('SELECT audio_path FROM task_responses WHERE NOT (id=ANY(%s::uuid[]))', (responses,))
            if any(any(Path(r['audio_path']).resolve().is_relative_to(folder) for folder in folders) for r in cur.fetchall()):
                raise DeleteBusy('Аудиофайлы используются другим совещанием. Удаление остановлено.')
            entities = ids + tasks + transcripts + llm + briefs + responses + ([mid] if mid else []) + ([gid] if gid else [])
            cur.execute('DELETE FROM reviews WHERE organization_id=%s AND entity_id=ANY(%s::uuid[])', (organization, entities))
            cur.execute('DELETE FROM operation_controls WHERE organization_id=%s AND entity_id=ANY(%s::uuid[])', (organization, entities))
            cur.execute('DELETE FROM task_responses WHERE task_id=ANY(%s::uuid[])', (tasks,))
            cur.execute('DELETE FROM response_worker_runs WHERE attempt_id IN (SELECT id FROM call_attempts WHERE call_job_id=ANY(%s::uuid[]))', (exclusive,))
            cur.execute('DELETE FROM call_job_tasks WHERE task_id=ANY(%s::uuid[])', (tasks,))
            cur.execute('DELETE FROM call_jobs WHERE id=ANY(%s::uuid[])', (exclusive,))
            cur.execute('DELETE FROM manual_call_sessions WHERE task_id=ANY(%s::uuid[])', (tasks,))
            if mid:
                cur.execute('DELETE FROM meeting_task_drafts WHERE meeting_id=%s', (mid,))
                cur.execute('DELETE FROM tasks WHERE meeting_id=%s', (mid,))
                cur.execute('DELETE FROM meeting_briefs WHERE meeting_id=%s', (mid,))
                cur.execute('DELETE FROM meeting_brief_jobs WHERE meeting_id=%s', (mid,))
                cur.execute('DELETE FROM meeting_llm_chunks WHERE job_id=ANY(%s::uuid[])', (llm,))
                cur.execute('DELETE FROM meeting_llm_jobs WHERE meeting_id=%s', (mid,))
                # Retain other duplicate uploads/assemblies; only remove their link
                # to the deleted result, so they cannot block FK deletion.
                cur.execute("UPDATE meeting_imports SET meeting_id=NULL,transcript_id=NULL,status='CANCELLED',updated_at=now() WHERE meeting_id=%s AND NOT (id=ANY(%s::uuid[]))", (mid, ids))
            if gid:
                cur.execute('UPDATE meeting_import_groups SET primary_import_id=NULL WHERE id=%s', (gid,))
            cur.execute('DELETE FROM meeting_import_metrics WHERE import_id=ANY(%s::uuid[])', (ids,))
            cur.execute('DELETE FROM meeting_imports WHERE id=ANY(%s::uuid[])', (ids,))
            if gid:
                cur.execute('DELETE FROM meeting_import_groups WHERE id=%s', (gid,))
            if mid:
                cur.execute('DELETE FROM meetings WHERE id=%s', (mid,))
            cur.execute('''INSERT INTO audit_events(id,organization_id,actor_type,actor_id,event_type,entity_type,entity_id,payload)
                VALUES (%s,%s,'USER',%s,'MEETING_DELETED',%s,%s,%s)''',
                (str(uuid.uuid4()), organization, actor, 'MEETING' if mid else 'MEETING_IMPORT', mid or str(selected['id']),
                 Json({'kind': kind, 'group_id': gid, 'imports': ids})))
            for path in sorted(folders | evidence):
                if path.exists():
                    destination = path.parent / ('.deleted-' + uuid.uuid4().hex)
                    path.rename(destination)
                    moved.append((path, destination))
        # The DB commit above succeeded; no live record points into these folders.
    except Exception:
        for folder, destination in reversed(moved):
            destination.rename(folder)
        raise
    for folder, destination in moved:
        if destination.is_dir():
            shutil.rmtree(destination)
        else:
            destination.unlink()


@bp.route('/meetings/delete/<kind>/<uuid:uid>', methods=['GET', 'POST'])
def recording(kind, uid):
    require_admin()
    with current_app.store.connection() as c, c.cursor() as cur:
        canonical_kind, row = selection(cur, kind, uid, g.user['organization_id'])
    if canonical_kind != kind or str(row['id']) != str(uid):
        return redirect(url_for('meeting_delete.recording', kind=canonical_kind, uid=row['id']), 303)
    if request.method == 'GET':
        return render_template('meeting_delete.html', row=row, kind=kind)
    if request.form.get('confirm') != str(uid):
        abort(400)
    try:
        delete_recording(current_app.store, g.user['organization_id'], g.user['person_id'], kind, uid,
                         current_app.config.get('IMPORT_ROOT', ROOT), current_app.store.settings.get('audio_roots'))
    except DeleteBusy as error:
        return render_template('meeting_delete.html', row=row, kind=kind, error=str(error)), 409
    except OSError:
        logging.exception('Recording file deletion failed')
        return render_template('error.html', message='Не удалось удалить файлы записи. Проверьте свободное место и права сервера.'), 500
    flash('Выбранная запись удалена целиком.')
    return redirect(url_for('imports.index'), 303)


def register(app):
    app.register_blueprint(bp)

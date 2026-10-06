"""Cooperative, database-backed control for long-running local workers."""
import uuid


TABLES = {
    'IMPORT': 'meeting_imports',
    'EXTRACTION': 'meeting_llm_jobs',
    'BRIEF': 'meeting_brief_jobs',
}


class PauseRequested(Exception):
    pass


class CancelRequested(Exception):
    pass


def effective_state(desired_state, organization_paused):
    if desired_state == 'CANCELLED':
        return 'CANCELLED'
    if desired_state == 'PAUSED' or organization_paused:
        return 'PAUSED'
    return 'RUNNING'


def _identity(kind, entity_id):
    kind = str(kind).upper()
    if kind not in TABLES:
        raise ValueError('Unsupported operation kind')
    return kind, str(uuid.UUID(str(entity_id)))


def _organization(cur, kind, entity_id):
    cur.execute(f'SELECT organization_id FROM {TABLES[kind]} WHERE id=%s', (entity_id,))
    row = cur.fetchone()
    return str(row['organization_id']) if row else None


def checkpoint(store, kind, entity_id):
    """Record a safe point and raise only after the transaction was committed."""
    kind, entity_id = _identity(kind, entity_id)
    state = None
    with store.connection() as connection, connection.cursor() as cur:
        organization_id = _organization(cur, kind, entity_id)
        if not organization_id:
            raise CancelRequested('Operation no longer exists')
        cur.execute('''INSERT INTO operation_controls(kind,entity_id,organization_id)
          VALUES (%s,%s,%s) ON CONFLICT(kind,entity_id) DO NOTHING''',
          (kind, entity_id, organization_id))
        cur.execute('''SELECT c.desired_state,COALESCE(s.paused,false) AS organization_paused
          FROM operation_controls c LEFT JOIN organization_operation_state s
            ON s.organization_id=c.organization_id
          WHERE c.kind=%s AND c.entity_id=%s AND c.organization_id=%s FOR UPDATE OF c''',
          (kind, entity_id, organization_id))
        row = cur.fetchone()
        state = effective_state(row['desired_state'], row['organization_paused'])
        cur.execute('''UPDATE operation_controls SET actual_state=%s,applied_at=now(),
          updated_at=now(),last_error=NULL WHERE kind=%s AND entity_id=%s''',
          (state, kind, entity_id))
    if state == 'PAUSED':
        raise PauseRequested('Paused by administrator')
    if state == 'CANCELLED':
        raise CancelRequested('Cancelled by administrator')


def finish(store, kind, entity_id):
    kind, entity_id = _identity(kind, entity_id)
    with store.connection() as connection, connection.cursor() as cur:
        cur.execute('''UPDATE operation_controls SET actual_state='COMPLETED',applied_at=now(),
          updated_at=now(),last_error=NULL WHERE kind=%s AND entity_id=%s''', (kind, entity_id))


def fail(store, kind, entity_id, error):
    kind, entity_id = _identity(kind, entity_id)
    with store.connection() as connection, connection.cursor() as cur:
        cur.execute('''UPDATE operation_controls SET actual_state='FAILED',applied_at=now(),
          updated_at=now(),last_error=%s WHERE kind=%s AND entity_id=%s''',
          (str(error)[:500], kind, entity_id))

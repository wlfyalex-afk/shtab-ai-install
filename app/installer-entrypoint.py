import json
import os
from pathlib import Path
import sys

os.umask(0o077)
runtime = Path('/run/shtab-ai')
runtime.mkdir(exist_ok=True)
password = Path('/run/secrets/db_password').read_text().strip()
secret = Path('/run/secrets/flask_secret').read_text().strip()
config = {'audio_roots': ['/srv/shtab-ai/meeting-imports', '/srv/shtab-ai/response-evidence'],
          'database': {'host': 'db', 'port': 5432, 'dbname': 'shtab_ai', 'user': 'shtab_ai',
                       'passfile': str(runtime / 'pgpass')}, 'secret_key': secret}
(runtime / 'pgpass').write_text(f'db:5432:shtab_ai:shtab_ai:{password}\n')
Path('/etc/shtab-ai/secretary-web.json').write_text(json.dumps(config))
os.execvp(sys.argv[1], sys.argv[1:])

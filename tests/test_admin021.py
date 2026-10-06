import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
import hashlib
import time
import sys
import gzip
import shutil
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1]/'scripts/admin021.py'


def module():
    spec = importlib.util.spec_from_file_location('maintenance', SCRIPT)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def archive(path, name='recording.txt', content=b'audio'):
    with tarfile.open(path, 'w:gz') as out:
        info = tarfile.TarInfo(name)
        info.size = len(content)
        out.addfile(info, io.BytesIO(content))


class MaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.m = module()
        self.m.ROOT = self.base/'install'
        self.m.STATE = self.base/'state'
        self.m.BACKUPS = self.base/'backups'
        for path in (self.m.ROOT/'database', self.m.STATE, self.m.BACKUPS): path.mkdir(parents=True)
        (self.m.ROOT/'database/schema-ubuntu.sql').write_text('schema021')
        (self.m.ROOT/'.env').write_text('current-settings')
        (self.m.ROOT/'secrets').mkdir()
        (self.m.ROOT/'secrets/db_password').write_text('current-secret')
        (self.m.ROOT/'tls-data').mkdir()
        (self.m.ROOT/'tls-data/root.crt').write_text('ca-preserved')
        self.calls = []

    def tearDown(self): self.temp.cleanup()

    def dc(self, *args, **kwargs):
        self.calls.append(args)
        if args[:1] == ('ps',): return subprocess.CompletedProcess(args,0,'web proxy meeting-worker\n','')
        if 'pg_dump' in args: kwargs['stdout'].write(b'PGDMP mock')
        return subprocess.CompletedProcess(args,0,'','')

    def transfer_dc(self, args, source=None, target=None, progress=None):
        self.m.dc(*args[4:], stdin=source)
        if source is not None:
            count = len(source.read())
            if progress is not None: progress.done = count
            return count

    def snapshot(self):
        def data(path): archive(path/'app-data.tar.gz')
        with patch.object(self.m,'dc',self.dc), patch.object(self.m,'volume_archive',data):
            return self.m.make_backup()

    def test_snapshot_integrity_and_private_permissions(self):
        path = self.snapshot()
        self.m.verify_backup(path)
        self.assertEqual(path.stat().st_mode & 0o777,0o700)
        for name in self.m.PAYLOADS:
            self.assertEqual((path/name).stat().st_mode & 0o777,0o600)
        (path/'database.dump').write_bytes(b'corrupted')
        with self.assertRaisesRegex(ValueError,'integrity'): self.m.verify_backup(path)

    def test_archive_rejects_escape_and_symlinks(self):
        path = self.base/'bad.tar.gz'
        archive(path,'../../escape')
        with self.assertRaises(ValueError): self.m.validate_tar(path)
        with tarfile.open(path,'w:gz') as out:
            item = tarfile.TarInfo('link'); item.type=tarfile.SYMTYPE;item.linkname='/etc';out.addfile(item)
        with self.assertRaises(ValueError): self.m.validate_tar(path)

    def test_cancel_reset_changes_nothing(self):
        with patch('builtins.input',return_value='no'), patch.object(self.m,'dc',self.dc):
            self.m.destructive('reset')
        self.assertEqual(self.calls,[])

    def test_failed_safety_backup_resumes_services_without_reset(self):
        with patch('builtins.input',return_value='CLEAR-SHTAB-021'), patch.object(self.m,'ensure_idle'), patch.object(self.m,'dc',self.dc), patch.object(self.m,'make_backup',side_effect=OSError('disk full')), patch.object(self.m,'sql') as sql, patch.object(self.m,'clear_or_restore_files') as files:
            with self.assertRaises(OSError): self.m.destructive('reset')
        sql.assert_not_called();files.assert_not_called()
        self.assertEqual(self.calls[-1][0],'start')

    def test_failed_restore_keeps_services_stopped(self):
        source = self.snapshot()
        self.calls=[]
        def failing(*args,**kwargs):
            if 'pg_restore' in args and '--single-transaction' in args:
                self.calls.append(args)
                raise subprocess.CalledProcessError(1,args)
            return self.dc(*args,**kwargs)
        with patch('builtins.input',return_value='RESTORE-SHTAB-021'), patch.object(self.m,'ensure_idle'), patch.object(self.m,'dc',failing), patch.object(self.m,'transfer_process',self.transfer_dc), patch.object(self.m,'make_backup',return_value=self.base/'safety'), patch.object(self.m,'clear_or_restore_files') as files:
            with self.assertRaises(subprocess.CalledProcessError): self.m.destructive('restore',source)
        files.assert_not_called()
        self.assertNotIn('start',[args[0] for args in self.calls])
        self.assertIn('--single-transaction',self.calls[-1])

    def test_restore_keeps_current_credentials_and_restores_ca(self):
        source = self.snapshot()
        (self.m.ROOT/'tls-data/root.crt').write_text('different-ca')
        self.calls=[]
        with patch('builtins.input',return_value='RESTORE-SHTAB-021'), patch.object(self.m,'ensure_idle'), patch.object(self.m,'dc',self.dc), patch.object(self.m,'transfer_process',self.transfer_dc), patch.object(self.m,'make_backup',return_value=self.base/'safety'), patch.object(self.m,'clear_or_restore_files') as files:
            self.m.destructive('restore',source)
        files.assert_called_once_with(source)
        self.assertEqual((self.m.ROOT/'tls-data/root.crt').read_text(),'ca-preserved')
        self.assertEqual((self.m.ROOT/'secrets/db_password').read_text(),'current-secret')
        self.assertEqual((self.m.ROOT/'.env').read_text(),'current-settings')
        self.assertEqual(self.calls[-1][0],'start')

    def test_reset_preserves_tls_and_requests_new_admin(self):
        with patch('builtins.input',return_value='CLEAR-SHTAB-021'), patch.object(self.m,'ensure_idle'), patch.object(self.m,'dc',self.dc), patch.object(self.m,'make_backup',return_value=self.base/'safety'), patch.object(self.m,'sql') as sql, patch.object(self.m,'clear_or_restore_files') as files:
            self.m.destructive('reset')
        sql.assert_called_once_with(self.m.RESET_SQL)
        files.assert_called_once_with()
        self.assertEqual((self.m.ROOT/'tls-data/root.crt').read_text(),'ca-preserved')
        self.assertEqual((self.m.STATE/'status').read_text(),'READY_FOR_ADMIN\n')
        self.assertEqual(self.calls[-1][0],'start')

    def test_busy_processing_blocks_backup_before_stop(self):
        with patch.object(self.m,'sql',return_value='1'), patch.object(self.m,'dc',self.dc):
            with self.assertRaisesRegex(ValueError,'незавершённые'):
                with self.m.paused(): pass
        self.assertEqual(self.calls,[])

    def test_progress_human_units_heartbeat_and_failure(self):
        self.assertEqual(self.m.human_size(0), '0 Б')
        self.assertEqual(self.m.human_size(1024), '1,0 КБ')
        self.assertEqual(self.m.human_size(2 * 1024**2), '2,0 МБ')
        self.assertEqual(self.m.human_size(3 * 1024**3), '3,0 ГБ')
        class Terminal(io.StringIO):
            def isatty(self): return True
        output = Terminal()
        with self.m.Progress('Упаковка', stream=output, interval=0.01) as progress:
            time.sleep(0.035)
        self.assertGreaterEqual(output.getvalue().count('выполняется'), 2)
        self.assertIn('готово', output.getvalue())
        self.assertFalse(progress.thread.is_alive())
        output = io.StringIO()
        with self.assertRaises(OSError):
            with self.m.Progress('Упаковка', stream=output) as failed:
                raise OSError('disk full')
        self.assertIn('ошибка / прервано', output.getvalue())
        self.assertNotIn('готово', output.getvalue())
        self.assertFalse(failed.thread.is_alive())

    def test_sha_streams_large_file_with_correct_digest(self):
        path = self.base/'large.dump'
        data = b'0123456789' * 250000
        path.write_bytes(data)
        output = io.StringIO()
        with patch.object(self.m.sys, 'stderr', output):
            self.assertEqual(self.m.sha(path), hashlib.sha256(data).hexdigest())
        self.assertIn('100%', output.getvalue())
        self.assertIn('МБ', output.getvalue())

    def test_restore_rejects_install_directory_before_mutation(self):
        with patch.object(self.m, 'dc', self.dc):
            with self.assertRaisesRegex(ValueError, 'КАТАЛОГ резервной копии'):
                self.m.destructive('restore', self.m.ROOT)
        self.assertEqual(self.calls, [])

    def test_eta_uses_processed_bytes_and_elapsed_time(self):
        output = io.StringIO()
        with patch.object(self.m.time, 'monotonic', return_value=100):
            progress = self.m.Progress('Упаковка', total=1000, stream=output)
        progress.done = 250
        with patch.object(self.m.time, 'monotonic', return_value=110):
            progress.render()
        self.assertIn('25%', output.getvalue())
        self.assertIn('осталось ≈ 00:30', output.getvalue())
        progress.done = 1000
        progress.render()
        self.assertIn('завершение этапа', output.getvalue())
        self.assertNotIn('100%', output.getvalue())
        progress.render('готово')
        self.assertIn('100%', output.getvalue())
        self.assertIn('осталось 00:00', output.getvalue())

    def test_stream_transfer_real_processes_and_failure(self):
        data = b'0123456789' * 300000
        output = io.BytesIO()
        args = [sys.executable, '-c', 'import sys; sys.stdout.buffer.write(b"0123456789"*300000)']
        progress = self.m.Progress('test', total=len(data), stream=io.StringIO())
        self.assertEqual(self.m.transfer_process(args, target=output, progress=progress), len(data))
        self.assertEqual(output.getvalue(), data)
        self.assertEqual(progress.done, len(data))
        dest = self.base/'restored'
        consumer = [sys.executable, '-c', 'import sys; from pathlib import Path; Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read())', str(dest)]
        self.assertEqual(self.m.transfer_process(consumer, source=io.BytesIO(data), progress=progress), len(data))
        self.assertEqual(dest.read_bytes(), data)
        with self.assertRaises(subprocess.CalledProcessError):
            self.m.transfer_process([sys.executable, '-c', 'raise SystemExit(2)'])

    def test_volume_archive_counts_uncompressed_stream_and_keeps_contents(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as archive:
            item = tarfile.TarInfo('recording.txt'); item.size = 5
            archive.addfile(item, io.BytesIO(b'audio'))
        data = raw.getvalue()
        def transfer(args, source=None, target=None, progress=None):
            if target is not None: target.write(data)
            if progress is not None: progress.done = len(data)
            return len(data)
        with patch.object(self.m, 'transfer_process', transfer):
            self.assertEqual(self.m.volume_archive(self.base), len(data))
        with tarfile.open(self.base/'app-data.tar.gz') as archive:
            self.assertEqual(archive.extractfile('recording.txt').read(), b'audio')
        self.m.validate_tar(self.base/'app-data.tar.gz')

    def test_restore_old_archive_measures_full_stream_and_uses_stdin(self):
        archive(self.base/'app-data.tar.gz')
        with gzip.open(self.base/'app-data.tar.gz') as f: raw = f.read()
        def transfer(args, source=None, target=None, progress=None):
            self.assertIn('-i', args)
            self.assertIn('tar -xf - -C /data', args[-1])
            self.assertEqual(progress.total, len(raw))
            data = source.read()
            self.assertEqual(data, raw)
            progress.done = len(data)
            return len(data)
        with patch.object(self.m, 'transfer_process', side_effect=transfer) as operation:
            self.m.clear_or_restore_files(self.base)
        operation.assert_called_once()

    @unittest.skipUnless(shutil.which('tar'), 'tar is required for streaming integration test')
    def test_real_tar_gzip_restore_stream_roundtrip(self):
        folder = self.base/'source'; folder.mkdir()
        payload = bytes(range(256)) * 13000
        (folder/'recording.bin').write_bytes(payload)
        output = self.base/'roundtrip.tar.gz'
        args = ['tar', '-cf', '-', '-C', str(folder), '.']
        total = self.m.transfer_process(args)
        progress = self.m.Progress('pack', total=total, stream=io.StringIO())
        with gzip.open(output, 'wb') as stream:
            self.assertEqual(self.m.transfer_process(args, target=stream, progress=progress), total)
        restored = self.base/'restored'; restored.mkdir()
        with gzip.open(output, 'rb') as stream:
            self.assertEqual(self.m.transfer_process(['tar', '-xf', '-', '-C', str(restored)], source=stream, progress=progress), total)
        self.assertEqual((restored/'recording.bin').read_bytes(), payload)
        self.assertEqual(progress.done, total)


if __name__ == '__main__': unittest.main()

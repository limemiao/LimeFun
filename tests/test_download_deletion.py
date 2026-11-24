"""Deletion safety and HTTP integration, using only temporary files/local HTTP."""
import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from limefun.download_manager import DownloadManager, DownloadPaused
from limefun.player_server import PlayerHandler


class IdleManager(DownloadManager):
    def _ensure_workers_locked(self):
        pass


def task(root, key, status='paused'):
    return dict(id=key, vod_id=1, vod_name='Test', episode_name=key,
                filename=key+'.mp4', target=str(root/(key+'.mp4')), status=status,
                created_at=time.time(), source_url='test', player='r2')


class DeleteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='limefun-delete-')
        self.root = Path(self.temp.name)
        self.manager = IdleManager(self.root, self.root/'state.json')

    def tearDown(self):
        self.manager.shutdown()
        self.manager._supervisor.join(timeout=3)
        self.temp.cleanup()

    def test_batch_preserves_complete_files_and_history(self):
        for key, status in [('a','paused'), ('b','queued'), ('c','failed')]:
            self.manager.tasks[key] = task(self.root, key, status)
            (self.root/(key+'.mp4.part')).write_bytes(b'partial')
        (self.root/'a.mp4').write_bytes(b'complete')
        self.manager.history = [dict(history_id='h1', id='a', status='completed')]
        result = self.manager.delete_tasks(['a','b','c','a','missing'])
        self.assertEqual(result['deleted'], ['a','b','c'])
        self.assertEqual(result['missing'], ['missing'])
        self.assertFalse(list(self.root.glob('*.mp4.part')))
        self.assertEqual((self.root/'a.mp4').read_bytes(), b'complete')
        self.assertEqual(len(self.manager.history), 1)
        self.assertEqual(json.loads(self.manager.state_file.read_text())['tasks'], {})
        result = self.manager.delete_histories(['h1','h1','missing'])
        self.assertEqual(result['deleted'], ['h1'])
        self.assertEqual((self.root/'a.mp4').read_bytes(), b'complete')

    def test_cleanup_failure_can_retry(self):
        self.manager.tasks['a'] = task(self.root, 'a')
        (self.root/'a.mp4.part').write_bytes(b'x')
        with patch.object(Path, 'unlink', side_effect=PermissionError('locked')):
            result = self.manager.delete_tasks(['a'])
        self.assertIn('a', result['errors'])
        self.assertEqual(self.manager.tasks['a']['status'], 'delete_failed')
        self.assertFalse(self.manager.resume('a'))
        self.assertFalse(self.manager.retry('a'))
        self.assertEqual(self.manager.delete_tasks(['a'])['deleted'], ['a'])

    def test_outside_root_is_never_deleted(self):
        with tempfile.TemporaryDirectory() as outside:
            target = Path(outside)/'a.mp4'
            partial = target.with_name('a.mp4.part')
            partial.write_bytes(b'keep')
            self.manager.tasks['a'] = task(Path(outside), 'a')
            self.assertIn('a', self.manager.delete_tasks(['a'])['errors'])
            self.assertEqual(partial.read_bytes(), b'keep')

    def test_restart_finishes_pending_deletion(self):
        self.manager.tasks['a'] = task(self.root, 'a', 'deleting')
        (self.root/'a.mp4.part').write_bytes(b'partial')
        self.manager._persist_locked(force=True)
        restored = IdleManager(self.root, self.manager.state_file)
        try:
            self.assertEqual(restored.tasks, {})
            self.assertFalse((self.root/'a.mp4.part').exists())
        finally:
            restored.shutdown()
            restored._supervisor.join(timeout=3)

    def test_cancellation_before_commit_does_not_publish(self):
        self.manager.tasks['a'] = task(self.root, 'a', 'downloading')
        self.manager.tasks['a']['run_token'] = 'run'
        self.manager._active_runs['run'] = 'a'
        manager = self.manager
        class Response:
            headers = {'Content-Length': '1'}
            status = 200
            reads = 0
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def read(self, size):
                self.reads += 1
                if self.reads == 1: return b'x'
                manager.delete_tasks(['a'])
                return b''
        with patch('urllib.request.urlopen', return_value=Response()):
            with self.assertRaises(DownloadPaused):
                manager._download_url('http://localhost/test', 'a', 'run')
        self.assertFalse((self.root/'a.mp4').exists())
        manager._active_runs.clear()
        self.assertEqual(manager.delete_tasks(['a'])['deleted'], ['a'])

    def test_http_batch_validation_and_partial_result(self):
        self.manager.tasks['a'] = task(self.root, 'a')
        server = ThreadingHTTPServer(('127.0.0.1',0), PlayerHandler)
        server.download_manager = self.manager
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def post(path, payload):
            request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}',
                data=json.dumps(payload).encode(), headers={'Content-Type':'application/json'})
            with urllib.request.urlopen(request) as response:
                return json.load(response)
        try:
            for ids in [[], 'a', [None], ['a']*501]:
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post('/api/download/tasks/delete', {'ids':ids})
                self.assertEqual(error.exception.code, 400)
                self.assertIn('a', self.manager.tasks)
            result = post('/api/download/tasks/delete', {'ids':['a','missing']})
            self.assertEqual(result['deleted'], ['a'])
            self.assertEqual(result['missing'], ['missing'])
            self.manager.history = [dict(history_id='h1', id='a')]
            self.assertEqual(post('/api/download/history/delete', {'ids':['h1']})['deleted'], ['h1'])
        finally:
            server.shutdown(); server.server_close(); thread.join(timeout=3)

    def test_inflight_socket_stops_and_partial_is_cleaned(self):
        release = threading.Event()
        class SlowVideo(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.send_header('Content-Length', str(8*1024*1024))
                self.end_headers()
                self.wfile.write(b'x'*(1024*1024+1024))
                self.wfile.flush()
                release.wait(8)
            def log_message(self, *args): pass
        server = ThreadingHTTPServer(('127.0.0.1',0), SlowVideo)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        manager = DownloadManager(self.root, self.root/'live-state.json', workers=1)
        manager._parse_url = lambda *args: f'http://127.0.0.1:{server.server_port}/video'
        try:
            with manager.condition:
                manager.tasks['live'] = task(self.root, 'live', 'queued')
                manager.condition.notify_all()
            deadline = time.monotonic()+5
            while time.monotonic() < deadline:
                with manager.condition:
                    if manager.tasks.get('live',{}).get('downloaded',0) >= 1024*1024: break
                    manager.condition.wait(.02)
            self.assertGreaterEqual(manager.tasks['live'].get('downloaded',0), 1024*1024)
            self.assertEqual(manager.delete_tasks(['live'])['pending'], ['live'])
            with manager.condition:
                self.assertTrue(manager.condition.wait_for(lambda:'live' not in manager.tasks, timeout=4), repr(manager.tasks))
            self.assertFalse((self.root/'live.mp4.part').exists())
            self.assertFalse((self.root/'live.mp4').exists())
            self.assertEqual(manager.history, [])
        finally:
            release.set()
            manager.shutdown(); manager.pool.shutdown(wait=True)
            manager._supervisor.join(timeout=3)
            server.shutdown(); server.server_close(); thread.join(timeout=3)


if __name__ == '__main__':
    unittest.main()

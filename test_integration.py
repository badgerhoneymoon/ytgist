"""No listeners, YouTube, subprocesses, MLX or model runs: handlers use in-memory IO."""
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch

with patch.object(subprocess, 'run', side_effect=AssertionError('No subprocesses')), \
     patch.object(subprocess, 'Popen', side_effect=AssertionError('No subprocesses')), \
     patch.object(urllib.request, 'urlopen', side_effect=AssertionError('No network')):
    import serve
    import ytgist
from job_store import JobStore, Conflict, TERMINAL

VID = 'abcdefghijk'
OTHER = '12345678901'
URL = f'https://www.youtube.com/watch?v={VID}'
SENTENCES = [{'start': 0, 'end': 3, 'text': 'A real transcript fixture.'}]


def handler(path, body=None, headers=None):
    h = object.__new__(serve.Handler)
    h.path = path
    encoded = json.dumps(body).encode() if body is not None else b''
    h.headers = {'Content-Length': str(len(encoded)), **(headers or {})}
    h.rfile, h.wfile = io.BytesIO(encoded), io.BytesIO()
    h.send_response = Mock()
    h.send_header = Mock()
    h.end_headers = Mock()
    h.send_error = lambda status: (h.send_response(status), h.wfile.write(b'{}'))
    return h


def request(path, body=None, headers=None):
    h = handler(path, body, headers)
    (h.do_POST if body is not None else h.do_GET)()
    return h.send_response.call_args.args[0], json.loads(h.wfile.getvalue())


def wait_until(predicate, timeout=3):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for mocked worker')


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, 'jobs.sqlite3')
        serve._store = JobStore(self.path)
        serve._worker_stop.clear()
        serve._ctl.clear()
        serve._current.update(job=None, url='', video='', native=False, frame={}, result=None, at=0)
        self.worker = None
        self.patches = [
            patch.object(subprocess, 'run', side_effect=AssertionError('No subprocesses')),
            patch.object(subprocess, 'Popen', side_effect=AssertionError('No subprocesses')),
            patch.object(urllib.request, 'urlopen', side_effect=AssertionError('No network')),
            patch.object(serve.gpu, 'Sampler'),
            patch.object(serve.gpu, 'stats', return_value={}),
            patch.object(ytgist, 'CACHE', self.tmp.name),
            patch.object(ytgist, '_parakeet_version', return_value='fixture'),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        serve._worker_stop.set()
        for ctl in list(serve._ctl.values()):
            ctl.stop()
        with serve._store.changed:
            serve._store.changed.notify_all()
        if self.worker:
            self.worker.join(4)
            self.assertFalse(self.worker.is_alive())
        serve._store.close()
        for p in reversed(self.patches):
            p.stop()
        self.tmp.cleanup()
        self.assertFalse(serve._RUN.locked())

    def start(self):
        self.worker = serve._start_worker()

    def submit(self, vid=VID, path='/api/gist', **flags):
        status, value = request(path, {'url': f'https://youtu.be/{vid}', **flags})
        self.assertEqual(status, 200)
        return value['job']

    def finish(self, job):
        wait_until(lambda: serve._store.get(job)['status'] in TERMINAL)
        return serve._store.get(job)

    @staticmethod
    def fake_run(url, *args, progress=None, control=None, **kwargs):
        vid = ytgist.yt.video_id(url)
        progress({'stage': 'summarise', 'pct': 75, 'msg': 'fixture'})
        ytgist.run.last = {'title': vid, 'video_id': vid, 'markdown': '**Point**',
                           'sentences': SENTENCES, 'duration': 3, 'cached': True}

    def test_duplicate_ui_agent_submissions_and_idempotency(self):
        def submit(i):
            return request('/api/jobs' if i % 2 else '/api/gist',
                           {'url': f'https://youtu.be/{VID}'},
                           {'Idempotency-Key': f'client-{i}'})[1]['job']
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(submit, range(8)))
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(serve._store.list()), 1)
        with patch.object(ytgist, 'run', side_effect=self.fake_run) as run:
            self.start()
            self.assertEqual(self.finish(ids[0])['status'], 'succeeded')
            status, repeated = request('/api/jobs', {'url': URL}, {'Idempotency-Key': 'client-0'})
            self.assertEqual(status, 200)
            self.assertEqual(repeated['job'], ids[0])
            self.assertEqual(run.call_count, 1)
        status, _ = request('/api/jobs', {'url': f'https://youtu.be/{OTHER}'},
                            {'Idempotency-Key': 'client-0'})
        self.assertEqual(status, 409)

    def test_restart_recovers_queue_and_results_without_replaying_running(self):
        completed = self.submit()
        serve._store.claim()
        serve._store.emit(completed, {'markdown': '<p>saved</p>', 'raw': 'saved'})
        interrupted = self.submit(OTHER)
        serve._store.claim()
        pending = self.submit(VID, native=True)
        serve._store.close()
        serve._store = JobStore(self.path)
        self.assertEqual(serve._store.get(completed)['result']['raw'], 'saved')
        self.assertEqual(serve._store.get(interrupted)['status'], 'interrupted')
        self.assertEqual(serve._store.get(pending)['status'], 'queued')
        self.assertEqual(request('/api/jobs/' + completed + '/result')[1]['raw'], 'saved')
        with patch.object(ytgist, 'run', side_effect=self.fake_run) as run:
            self.start()
            self.assertEqual(self.finish(pending)['status'], 'succeeded')
            self.assertEqual(run.call_count, 1)

    def test_second_engine_cannot_recover_live_database(self):
        job = self.submit()
        serve._store.claim()
        with self.assertRaisesRegex(RuntimeError, 'Another engine'):
            JobStore(self.path)
        self.assertEqual(serve._store.get(job)['status'], 'running')

    def test_serial_processing_current_identity_and_expansion_share_lock(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        def slow(url, *args, **kwargs):
            calls.append(url)
            if len(calls) == 1:
                entered.set()
                self.assertTrue(release.wait(2))
            self.fake_run(url, *args, **kwargs)
        with patch.object(ytgist, 'run', side_effect=slow), \
             patch.object(ytgist, 'expand', return_value='detail') as expand:
            first = self.submit()
            self.start()
            self.assertTrue(entered.wait(2))
            second = self.submit(OTHER)
            self.assertEqual(request('/api/current')[1]['job'], first)
            self.assertEqual(serve._store.get(second)['status'], 'queued')
            h = handler('/api/expand', {'video': VID, 'start': 0, 'end': 3})
            thread = threading.Thread(target=h.do_POST)
            thread.start()
            self.assertFalse(expand.called)
            release.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(self.finish(first)['result']['title'], VID)
            self.assertEqual(self.finish(second)['result']['title'], OTHER)
            self.assertEqual(json.loads(h.wfile.getvalue()), {'text': 'detail'})
            expand.assert_called_once()

    def test_cancel_queued_and_running_jobs_is_durable(self):
        queued = self.submit()
        self.assertEqual(request('/api/cancel', {'job': queued})[1], {'ok': True})
        self.assertEqual(request('/api/cancel', {'job': queued})[1], {'ok': True})
        self.assertEqual(self.finish(queued)['status'], 'cancelled')
        running = self.submit(OTHER)
        entered = threading.Event()
        def slow(url, *args, control=None, **kwargs):
            entered.set()
            self.assertTrue(control.cancelled.wait(2))
            raise ytgist.Cancelled()
        with patch.object(ytgist, 'run', side_effect=slow) as run:
            self.start()
            self.assertTrue(entered.wait(2))
            self.assertTrue(request('/api/cancel', {'job': running})[1]['ok'])
            self.assertEqual(self.finish(running)['status'], 'cancelled')
            self.assertEqual(run.call_count, 1)
        self.assertIsNone(request('/api/current')[1]['job'])
        self.assertEqual(request('/api/jobs/' + running + '/result')[0], 409)

    def test_cancel_while_waiting_for_expansion_lock_skips_pipeline(self):
        job = self.submit()
        serve._RUN.acquire()
        try:
            with patch.object(ytgist, 'run', side_effect=self.fake_run) as run:
                self.start()
                wait_until(lambda: job in serve._ctl)
                request('/api/cancel', {'job': job})
                serve._RUN.release()
                self.assertEqual(self.finish(job)['status'], 'cancelled')
                run.assert_not_called()
        finally:
            if serve._RUN.locked():
                serve._RUN.release()

    def test_failures_and_empty_speech_do_not_block_next_job(self):
        first = self.submit()
        second = self.submit(OTHER)
        third = self.submit(VID, native=True)
        count = [0]
        def failing(url, *args, **kwargs):
            count[0] += 1
            if count[0] == 1:
                raise ytgist.yt.IngestError('fixture', 'fixture download failure')
            if count[0] == 2:
                ytgist.run.last = None
                return
            self.fake_run(url, *args, **kwargs)
        with patch.object(ytgist, 'run', side_effect=failing):
            self.start()
            self.assertEqual(self.finish(first)['status'], 'failed')
            self.assertIn('fixture download failure', serve._store.get(first)['error'])
            self.assertEqual(self.finish(second)['status'], 'failed')
            self.assertEqual(self.finish(third)['status'], 'succeeded')

    def test_sse_multiple_readers_and_late_reconnect_get_same_result(self):
        job = self.submit()
        readers = [handler('/api/events?job=' + job) for _ in range(2)]
        threads = [threading.Thread(target=h._events) for h in readers]
        for thread in threads:
            thread.start()
        serve._store.claim()
        serve._store.emit(job, {'markdown': '<p>fixture</p>', 'raw': 'fixture', 'sentences': SENTENCES})
        for thread, h in zip(threads, readers):
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertIn(b'"raw": "fixture"', h.wfile.getvalue())
        late = handler('/api/events?job=' + job)
        late._events()
        self.assertIn(b'"raw": "fixture"', late.wfile.getvalue())

    def test_sse_reconnect_preserves_eta_and_cancel_terminal(self):
        job = self.submit()
        serve._store.claim()
        serve._store.emit(job, {'eta': {'summarise': 42}, 'video_minutes': 1})
        serve._store.emit(job, {'stage': 'summarise', 'pct': 75, 'msg': 'fixture'})
        self.assertEqual(serve._store.get(job)['event']['eta'], {'summarise': 42})
        serve._store.cancel(job)
        serve._store.emit(job, {'markdown': 'too late'})
        h = handler('/api/events?job=' + job)
        h._events()
        self.assertIn(b'"stopped": true', h.wfile.getvalue())
        self.assertNotIn(b'too late', h.wfile.getvalue())

    def test_cancel_recovery_and_explicit_retry(self):
        job = self.submit()
        serve._store.claim()
        serve._store.cancel(job)
        serve._store.close()
        serve._store = JobStore(self.path)
        self.assertEqual(serve._store.get(job)['status'], 'interrupted')
        retry = self.submit()
        self.assertNotEqual(job, retry)
        self.assertEqual(serve._store.get(retry)['status'], 'queued')

    def test_running_cancel_stops_control_before_completion_can_handoff(self):
        job = self.submit()
        serve._store.claim()
        stopping, release, completed = threading.Event(), threading.Event(), threading.Event()
        ctl = serve.Control()
        def stop():
            stopping.set()
            self.assertTrue(release.wait(2))
        ctl.stop = stop
        serve._ctl[job] = ctl
        cancel = threading.Thread(target=lambda: request('/api/cancel', {'job': job}))
        cancel.start()
        self.assertTrue(stopping.wait(2))
        def finish():
            serve._store.emit(job, {'markdown': 'too late'})
            completed.set()
        finish_thread = threading.Thread(target=finish)
        finish_thread.start()
        self.assertFalse(completed.wait(.02))
        release.set()
        cancel.join(2)
        finish_thread.join(2)
        self.assertEqual(serve._store.get(job)['status'], 'cancelled')
        serve._ctl.pop(job)

    def test_frames_use_same_queue_and_keep_other_video_snapshot(self):
        job = self.submit(path='/api/frames', video=VID)
        serve._current.update(video=OTHER, result={'frames': []})
        with patch.object(ytgist, 'find_frames', return_value=([{'state': 'none'}], 'fixture')):
            self.start()
            item = self.finish(job)
            self.assertEqual(item['status'], 'succeeded')
            self.assertTrue(item['result']['frames_done'])
            self.assertEqual(serve._current['result']['frames'], [])

    def test_cache_only_retrieval_versions_and_languages(self):
        ytgist.save_cached(VID, {'v': ytgist.CACHE_V, 'model': ytgist.PARAKEET,
            'chunk': ytgist.CHUNK, 'overlap': ytgist.OVERLAP, 'parakeet': 'fixture',
            'title': 'fixture', 'duration': 3, 'sentences': SENTENCES})
        for native, text in ((False, 'English'), (True, 'Native')):
            ytgist.save_summary(VID, {'gist_v': ytgist.GIST_V, 'exp_v': ytgist.EXP_V,
                                     'text': text, 'expansions': {'0': 'detail'}}, native)
        for native, text in ((0, 'English'), (1, 'Native')):
            status, value = request(f'/api/video?v={VID}&native={native}')
            self.assertEqual(status, 200)
            self.assertEqual(value['result']['raw'], text)
            self.assertEqual(value['sentences'], SENTENCES)
            self.assertEqual(value['result']['expansions'], {'0': 'detail'})
        ytgist.save_summary(VID, {'gist_v': -1, 'text': 'stale'})
        self.assertTrue(request(f'/api/video?v={VID}')[1]['summary_stale'])
        self.assertIsNone(request(f'/api/video?v={VID}')[1]['result'])
        with patch.object(ytgist, '_parakeet_version', return_value='different'):
            value = request(f'/api/video?v={VID}')[1]
            self.assertTrue(value['transcript_stale'])
            self.assertEqual(value['sentences'], [])
        self.assertEqual(request(f'/api/video?v={OTHER}')[0], 404)

    def test_bad_requests_and_unknown_jobs(self):
        for body in ([], {'url': 'https://example.com/'}, {'url': URL, 'native': 'false'},
                     {'url': URL, 'model': []}):
            self.assertEqual(request('/api/jobs', body)[0], 400)
        for path in ('/api/video?v=../../etc/passwd', '/api/video?v=' + VID + '&native=x',
                     '/api/jobs?limit=0', '/api/jobs?limit=101'):
            self.assertEqual(request(path)[0], 400)
        self.assertEqual(request('/api/jobs/missing')[0], 404)
        self.assertEqual(request('/api/cancel', {'job': 'missing'})[1], {'ok': False})
        self.assertEqual(len(serve._store.list()), 0)


if __name__ == '__main__':
    unittest.main()

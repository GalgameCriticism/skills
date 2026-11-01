import asyncio
import copy
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import safety
import publish_opus


class SafetyTests(unittest.TestCase):
    def test_image_paths_and_disguised_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'bundle'; root.mkdir()
            Image.new('RGB', (8, 8)).save((root / 'ok.png').resolve())
            outside = Path(directory) / 'outside.png'
            Image.new('RGB', (8, 8)).save(outside)
            (root / 'link.png').symlink_to(outside)
            (root / 'fake.png').write_text('private data')
            self.assertEqual(safety.image_path(root, 'ok.png'), (root / 'ok.png').resolve())
            for name in ('../outside.png', str(outside), 'link.png', 'fake.png'):
                with self.subTest(name=name), self.assertRaises((ValueError, OSError)):
                    safety.image_path(root, name)

    def test_private_write_and_durable_exclusive_claim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'receipt.json'
            safety.claim_receipt(path, {'status': 'submitting'})
            with self.assertRaises(FileExistsError):
                safety.claim_receipt(path, {'status': 'second'})
            self.assertEqual(json.loads(path.read_text())['status'], 'submitting')
            safety.write_private(path, 'secret')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            alias = Path(directory) / 'alias'; alias.symlink_to(path)
            with self.assertRaises(ValueError): safety.write_private(alias, 'changed')
            self.assertEqual(path.read_text(), 'secret')

    def test_preview_symlink_and_sensitive_error_are_rejected(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / 'bundle'; root.mkdir()
            (root / '_preview').symlink_to(Path(d), target_is_directory=True)
            with self.assertRaises(ValueError): safety.preview_directory(root)
        session = Mock(); session.post.side_effect = RuntimeError('csrf=private-token')
        with self.assertRaises(RuntimeError) as error:
            safety.post_draft(session, 'https://api.bilibili.com/test')
        self.assertNotIn('private-token', str(error.exception))

    def test_account_mismatch_stops_writes(self):
        from unittest.mock import Mock
        session = Mock()
        session.get.return_value.json.return_value = {'code': 0, 'data': {'isLogin': True, 'mid': 22}}
        with self.assertRaises(ValueError): safety.require_account(session, '11')
        session.post.assert_not_called()


class OfflineWorkflowTests(unittest.TestCase):
    def test_mac_compatible_dry_run_build_preview_and_draft(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); preview = root / '_preview'; preview.mkdir()
            Image.new('RGB', (16, 12), 'red').save(root / 'cover.png')
            article = {'issue': 1, 'title': 'Synthetic test', 'title_status': 'manual',
                       'credits': ['Synthetic author'], 'warnings': [], 'source': {'cover': 'cover.png'},
                       'sections': [{'heading': 'Test', 'items': [{'heading': 'News', 'body': ['Body'], 'candidates': [], 'selected': []}]}]}
            (preview / 'article.json').write_text(json.dumps(article))
            for script, extra in [('build_opus.py', ['--dry-run']), ('render_preview.py', []),
                                  ('save_draft.py', ['--dry-run']), ('publish_opus.py', ['--dry-run'])]:
                result = subprocess.run([sys.executable, str(SCRIPTS / script), str(root), *extra],
                                        capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((preview / 'preview.html').is_file())
            self.assertTrue((preview / 'draft_arg.json').is_file())
            self.assertFalse((preview / 'publish_receipt.json').exists())
            self.assertFalse((preview / 'draft_receipt.json').exists())



class PublishingTests(unittest.IsolatedAsyncioTestCase):
    async def test_timeout_and_readback_failure_never_repost(self):
        for failure in ('timeout', 'readback'):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                root = Path(directory); preview = root / '_preview'; preview.mkdir()
                article = {'issue': 1, 'title': 'Test', 'title_status': 'manual', 'credits': ['Author'], 'warnings': [], 'sections': []}
                payload = {'raw_content': '{}', 'opus_req': {'upload_id': 'one', 'opus': {'title': 'Test', 'content': {'paragraphs': []}}}}
                payload['_local'] = safety.payload_metadata(article, '11', [])
                payload['_local']['payload_sha256'] = safety.digest({k: v for k, v in payload.items() if k != '_local'})
                (preview / 'article.json').write_text(json.dumps(article))
                (preview / 'opus_payload.json').write_text(json.dumps(payload))
                calls = []
                class Api:
                    def __init__(self, **kwargs): calls.append(kwargs)
                    def update_data(self, **data):
                        self.data = data
                        assert '_local' not in data
                        return self
                    def update_params(self, **params): return self
                    @property
                    def result(self):
                        async def response():
                            if failure == 'timeout': raise TimeoutError()
                            return {'dyn_id_str': '123'}
                        return response()
                module = types.ModuleType('bilibili_api.utils.network'); module.Api = Api
                async def verify(*args):
                    if len(calls) == 1 and not (preview / 'verified').exists():
                        (preview / 'verified').touch()
                        raise RuntimeError('Readback unavailable')
                    return {'title_match': True, 'pics_match': True}
                with patch.dict(sys.modules, {'bilibili_api.utils.network': module}), \
                     patch.object(sys, 'argv', ['publish', str(root), '--account-id', '11', '--yes']), \
                     patch.object(publish_opus, 'load_credential', return_value=(types.SimpleNamespace(bili_jct='private'), {})), \
                     patch.object(publish_opus, 'api_session'), patch.object(publish_opus, 'require_account'), \
                     patch.object(publish_opus, 'verify_opus', side_effect=verify), \
                     patch.object(publish_opus.asyncio, 'sleep', return_value=None):
                    with self.assertRaises((SystemExit, RuntimeError)):
                        await publish_opus.main_async()
                    receipt = json.loads((preview / 'publish_receipt.json').read_text())
                    self.assertEqual(receipt['status'], 'submitting' if failure == 'timeout' else 'submitted')
                    if failure == 'timeout':
                        with self.assertRaises(SystemExit): await publish_opus.main_async()
                    else:
                        self.assertEqual(receipt['dyn_id'], '123')
                        await publish_opus.main_async()
                    self.assertEqual(len(calls), 1)

    async def test_changed_payload_rejected_before_login(self):
        article = {'title': 'Test', 'title_status': 'manual', 'credits': ['Author'], 'warnings': []}
        payload = {'opus_req': {'opus': {'title': 'Test'}}}
        payload['_local'] = safety.payload_metadata(article, '11', [])
        payload['_local']['payload_sha256'] = safety.digest({k: v for k, v in payload.items() if k != '_local'})
        safety.validate_payload(payload, article, '11', [])
        changed = copy.deepcopy(payload); changed['raw_content'] = 'different body'
        with self.assertRaises(ValueError): safety.validate_payload(changed, article, '11', [])
        with self.assertRaises(ValueError): safety.validate_payload(payload, article, '22', [])


if __name__ == '__main__': unittest.main()

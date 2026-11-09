import base64
import contextlib
import hashlib
import io
import os
import shutil
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from security import Settings, RateLimiter, dpapi, password_record, verify_password
from protocol import make_identity
from transfers import TransferPeer, safe_name, MAX_FILE, CHUNK
from engine import WindowsDesktop


@contextlib.contextmanager
def test_directory():
    base = Path(tempfile.gettempdir()).resolve()
    path = base / ('landesk-test-' + uuid.uuid4().hex)
    path.mkdir()  # Inherit test workspace ACL; avoid sandbox mode-0700 token issue.
    try:
        yield path
    finally:
        assert path.parent == base and path.name.startswith('landesk-test-')
        shutil.rmtree(path)


class SecurityTests(unittest.TestCase):
    def test_password_is_salted_and_not_plaintext(self):
        one, two = password_record('long enough phrase'), password_record('long enough phrase')
        self.assertNotEqual(one, two)
        self.assertTrue(verify_password('long enough phrase', one))
        self.assertFalse(verify_password('wrong', one))
        self.assertFalse(verify_password('', None))
        self.assertFalse(verify_password('x' * 1000, one))
        short_four = password_record('1234')
        self.assertTrue(verify_password('1234', short_four))
        with self.assertRaises(ValueError):
            password_record('123')

    def test_global_limit_and_expiry(self):
        clock = [0]
        limit = RateLimiter(lambda: clock[0])
        for i in range(20):
            self.assertTrue(limit.admit(str(i)))
            limit.fail(str(i))
        self.assertFalse(limit.admit('new-address'))
        clock[0] = 61
        self.assertTrue(limit.admit('new-address'))

    @unittest.skipUnless(os.name == 'nt', 'DPAPI requires Windows')
    def test_dpapi_encryption_and_identity_persistence(self):
        with test_directory() as path:
            settings = Settings(path)
            settings.save(password=password_record('long enough phrase'))
            _, first = make_identity(settings=settings)
            settings.trust('127.0.0.1:45888', 'A' * 64)
            stored = settings.path.read_bytes()
            self.assertNotIn(b'PRIVATE KEY', stored)
            self.assertNotIn(b'long enough phrase', stored)
            reloaded = Settings(path)
            _, second = make_identity(settings=reloaded)
            self.assertEqual(first, second)
            self.assertTrue(verify_password('long enough phrase', reloaded.snapshot()['password']))
            self.assertEqual(reloaded.snapshot()['trusted']['127.0.0.1:45888'], 'A' * 64)
            settings.path.write_bytes(b'corrupted')
            with self.assertRaises(ValueError):
                Settings(path)

    def test_lossless_image_and_sharp_resolution(self):
        desktop = WindowsDesktop.__new__(WindowsDesktop)
        original = Image.new('RGB', (1920, 1080), (42, 91, 139))
        with patch('PIL.ImageGrab.grab', return_value=original):
            lossless = Image.open(io.BytesIO(desktop.frame('lossless')))
            self.assertEqual(lossless.size, original.size)
            self.assertEqual(lossless.tobytes(), original.tobytes())
            self.assertEqual(Image.open(io.BytesIO(desktop.frame('sharp'))).size, original.size)
            self.assertLess(Image.open(io.BytesIO(desktop.frame('smooth'))).width, original.width)


class TransferTests(unittest.TestCase):
    def test_path_traversal_and_windows_devices_rejected(self):
        for name in ['../secret', '..', 'C:\\secret', 'a/b', 'a:b', 'NUL.txt', 'CON', 'LPT1.exe', 'trailing.', 'bad\x00name']:
            with self.subTest(name=name), self.assertRaises(ValueError):
                safe_name(name)

    def test_bidirectional_files_hash_and_no_overwrite(self):
        with test_directory() as path:
            source_a, source_b = path / 'a', path / 'b'
            source_a.mkdir()
            source_b.mkdir()
            first, second = source_a / 'same.txt', source_b / 'same.txt'
            first.write_bytes(os.urandom(CHUNK * 2 + 15))
            second.write_bytes(b'other contents')
            completed_a, completed_b = [], []
            a = TransferPeer(path / 'in-a', completed_a.append)
            b = TransferPeer(path / 'in-b', completed_b.append)
            try:
                a.queue_files([first, first])
                b.queue_files([second])
                for _ in range(100):
                    left, right = a.next_message(), b.next_message()
                    if left:
                        b.receive(left)
                    if right:
                        a.receive(right)
                    if len(completed_b) == 2 and len(completed_a) == 1 and not a.outgoing and not b.outgoing:
                        break
                self.assertEqual(len(completed_b), 2)
                self.assertEqual(len(completed_a), 1)
                self.assertNotEqual(completed_b[0], completed_b[1])
                self.assertEqual(completed_b[0].read_bytes(), first.read_bytes())
                self.assertEqual(completed_a[0].read_bytes(), second.read_bytes())
            finally:
                a.cancel()
                b.cancel()

    def test_hash_mismatch_never_finishes_and_cancel_cleans_partial(self):
        with test_directory() as path:
            completed = []
            peer = TransferPeer(path, completed.append)
            try:
                peer.receive({'op': 'begin', 'id': 'a' * 24, 'name': 'file.txt', 'size': 3})
                peer.receive({'op': 'data', 'id': 'a' * 24, 'offset': 0, 'data': base64.b64encode(b'abc').decode()})
                with self.assertRaises(ValueError):
                    peer.receive({'op': 'end', 'id': 'a' * 24, 'sha256': 'bad'})
                self.assertEqual(completed, [])
            finally:
                peer.cancel()
            self.assertFalse(list(path.rglob('*.partial')))

    def test_oversize_and_bad_offsets(self):
        with test_directory() as path:
            peer = TransferPeer(path)
            try:
                with self.assertRaises(ValueError):
                    peer.receive({'op': 'begin', 'id': 'b' * 24, 'name': 'big.txt', 'size': MAX_FILE + 1})
                peer.receive({'op': 'begin', 'id': 'b' * 24, 'name': 'ok.txt', 'size': 1})
                with self.assertRaises(ValueError):
                    peer.receive({'op': 'data', 'id': 'b' * 24, 'offset': 2, 'data': 'YQ=='})
            finally:
                peer.cancel()

    def test_empty_file(self):
        with test_directory() as path:
            completed = []
            peer = TransferPeer(path, completed.append)
            peer.receive({'op': 'begin', 'id': 'e' * 24, 'name': 'empty.txt', 'size': 0})
            peer.receive({'op': 'end', 'id': 'e' * 24, 'sha256': hashlib.sha256(b'').hexdigest()})
            self.assertEqual(completed[0].read_bytes(), b'')
            peer.cancel()

    def test_folder_auto_zip_and_non_destructive_abort(self):
        import zipfile
        with test_directory() as path:
            folder = path / 'docs_bundle'
            folder.mkdir()
            (folder / 'readme.txt').write_text('สวัสดี LanDesk 0.3', encoding='utf-8')
            sub = folder / 'nested'
            sub.mkdir()
            (sub / 'data.bin').write_bytes(b'1234567890' * 100)
            completed = []
            batches_sent = []
            sender = TransferPeer(path / 'sender-recv', on_batch_sent=batches_sent.append)
            receiver = TransferPeer(path / 'receiver-recv', completed.append)
            try:
                sender.queue_files([folder], auto_paste=True)
                for _ in range(40):
                    msg = sender.next_message()
                    if msg:
                        receiver.receive(msg)
                    ack = receiver.next_message()
                    if ack:
                        sender.receive(ack)
                    if completed and not sender.outgoing:
                        break
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0].name, 'docs_bundle.zip')
                self.assertEqual(batches_sent, [True])
                with zipfile.ZipFile(completed[0]) as zf:
                    self.assertIn('docs_bundle/readme.txt', zf.namelist())
                    self.assertIn('docs_bundle/nested/data.bin', zf.namelist())
                # Now test non-destructive abort mid-transfer:
                big_file = path / 'mid.bin'
                big_file.write_bytes(os.urandom(CHUNK * 2))
                sender.queue_files([big_file])
                begin_msg = sender.next_message()
                receiver.receive(begin_msg)
                sender.receive(receiver.next_message())
                sender.abort(notify_peer=True)
                cancel_msg = sender.next_message()
                self.assertEqual(cancel_msg['op'], 'cancel')
                receiver.receive(cancel_msg)
                self.assertIsNone(sender.outgoing)
                self.assertIsNone(receiver.incoming)
                self.assertFalse(list((path / 'receiver-recv').rglob('*.partial')))
            finally:
                sender.cancel()
                receiver.cancel()

    @unittest.skipUnless(os.name == 'nt', 'DPAPI requires Windows')
    def test_address_book_contacts_and_alias(self):
        with test_directory() as path:
            settings = Settings(path)
            settings.save(alias='  แผนกไอที\n01  ')
            settings.upsert_contact('192.168.1.50', name='เครื่องบัญชี', favorite=False, touched=True)
            settings.upsert_contact('192.168.1.10', name='เซิร์ฟเวอร์ภายใน', favorite=True, touched=False)
            reloaded = Settings(path)
            snap = reloaded.snapshot()
            self.assertEqual(snap['contacts'][0]['ip'], '192.168.1.10')
            self.assertTrue(snap['contacts'][0]['favorite'])
            self.assertEqual(snap['contacts'][1]['name'], 'เครื่องบัญชี')
            reloaded.remove_contact('192.168.1.50')
            self.assertEqual(len(reloaded.snapshot()['contacts']), 1)


if __name__ == '__main__':
    unittest.main()


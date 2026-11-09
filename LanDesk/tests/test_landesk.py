import io
import socket
import struct
import sys
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image
from engine import Host, WindowsDesktop
from protocol import connect_pinned, lan_address, receive_json, receive_packet, send_json, MAX_PACKET
from security import password_record, verify_password, RateLimiter


class FakeSettings:
    def __init__(self, **values):
        self.data = {'password': None, 'control': True, 'clipboard': False, 'files': False,
                     'allow_networks': '', 'max_minutes': 60, **values}

    def snapshot(self):
        return dict(self.data)

    def save(self, **values):
        self.data.update(values)


class FakeDesktop:
    instances = []

    def __init__(self):
        self.events = []
        self.released = False
        self.__class__.instances.append(self)

    def frame(self, quality='sharp'):
        out = io.BytesIO()
        Image.new('RGB', (80, 60), '#00695c').save(out, 'JPEG')
        return out.getvalue()

    def apply(self, events):
        self.events.extend(events)

    def release(self):
        self.released = True

    def shortcut(self, code):
        self.events.append({'shortcut': code})


class HostTests(unittest.TestCase):
    def setUp(self):
        FakeDesktop.instances.clear()
        self.answer = (True, {'control': True})
        self.approvals = []
        self.settings = FakeSettings()
        self.host = Host(self.consent, lambda text: None, FakeDesktop, settings=self.settings)
        self.host.start(0)
        self.client = None

    def consent(self, peer, stop):
        self.approvals.append(peer)
        return self.answer

    def tearDown(self):
        if self.client:
            self.client.close()
        self.host.stop()
        self.host.thread.join(3)
        self.assertFalse(self.host.thread.is_alive())

    def connect(self, password=None):
        self.client = connect_pinned('127.0.0.1', self.host.port, self.host.fingerprint)
        send_json(self.client, {'version': 2})
        receive_json(self.client)
        send_json(self.client, {'method': 'password', 'password': password} if password else {'method': 'attended'})
        return receive_json(self.client)

    def frame(self):
        meta = receive_json(self.client, 1024 * 1024)
        if meta.get('frame'):
            image = Image.open(io.BytesIO(receive_packet(self.client)))
            self.assertEqual(image.size, (80, 60))
        return meta

    def test_wrong_password_never_requests_consent_or_captures(self):
        self.assertFalse(self.connect('WRONG')['ok'])
        self.assertEqual(self.approvals, [])
        self.assertEqual(FakeDesktop.instances, [])

    def test_wrong_fingerprint_rejected_before_auth(self):
        with self.assertRaisesRegex(ValueError, 'ลายนิ้วมือไม่ตรง'):
            connect_pinned('127.0.0.1', self.host.port, '00' * 32)
        self.assertEqual(FakeDesktop.instances, [])

    def test_denied_consent_never_captures(self):
        self.answer = (False, {})
        self.assertTrue(self.connect()['waiting'])
        self.assertFalse(receive_json(self.client)['ok'])
        self.assertEqual(FakeDesktop.instances, [])

    def test_view_only_ignores_input(self):
        self.answer = (True, {'control': False})
        self.connect()
        self.assertFalse(receive_json(self.client)['control'])
        self.assertFalse(self.frame()['control'])
        send_json(self.client, {'events': [{'type': 'key', 'code': 65, 'down': True}]})
        self.frame()
        self.assertEqual(FakeDesktop.instances[0].events, [])

    def test_control_revoke_and_disconnect_release(self):
        self.connect()
        self.assertTrue(receive_json(self.client)['control'])
        self.frame()
        command = {'type': 'key', 'code': 65, 'down': True}
        send_json(self.client, {'events': [command]})
        self.frame()
        desktop = FakeDesktop.instances[0]
        self.assertEqual(desktop.events, [command])
        self.host.set_control(False)
        send_json(self.client, {'events': [command]})
        self.assertFalse(self.frame()['control'])
        self.assertEqual(desktop.events, [command])
        self.assertTrue(desktop.released)
        self.host.stop()
        with self.assertRaises((ConnectionError, OSError)):
            receive_json(self.client)

    def test_stop_while_awaiting_consent(self):
        self.host.consent = lambda peer, stop: (stop.wait(3) and False, False)
        self.connect()
        self.host.stop()
        self.host.thread.join(2)
        self.assertFalse(self.host.thread.is_alive())
        self.assertEqual(FakeDesktop.instances, [])

    def test_rate_limit_after_five_failures(self):
        for _ in range(5):
            self.assertFalse(self.connect('WRONG')['ok'])
            self.client.close()
        self.assertFalse(self.host.limiter.admit('127.0.0.1'))

    def test_password_bypasses_consent_only_when_configured(self):
        self.settings.save(password=password_record('correct horse 42'))
        # The host reads current settings on accept.
        self.assertFalse(self.connect('correct horse 42')['waiting'])
        self.assertTrue(receive_json(self.client)['control'])
        self.frame()
        self.assertEqual(self.approvals, [])

    def test_attended_still_available_with_password_set(self):
        self.settings.save(password=password_record('correct horse 42'))
        self.assertTrue(self.connect()['waiting'])
        self.assertTrue(receive_json(self.client)['ok'])
        self.assertEqual(self.approvals, ['127.0.0.1'])

    def test_clipboard_denied_server_side(self):
        self.connect()
        receive_json(self.client)
        self.frame()
        send_json(self.client, {'clipboard': {'op': 'paste_text', 'text': 'forbidden'}})
        with self.assertRaises((ConnectionError, OSError)):
            receive_json(self.client)

    def test_files_denied_server_side(self):
        self.connect()
        receive_json(self.client)
        self.frame()
        send_json(self.client, {'transfer': {'op': 'begin', 'id': 'a' * 24, 'name': 'no.txt', 'size': 1}})
        with self.assertRaises((ConnectionError, OSError)):
            receive_json(self.client)

    def test_unknown_auth_never_captures(self):
        self.client = connect_pinned('127.0.0.1', self.host.port, self.host.fingerprint)
        send_json(self.client, {'version': 2})
        receive_json(self.client)
        send_json(self.client, {'method': 'anonymous'})
        with self.assertRaises((ConnectionError, OSError)):
            receive_json(self.client)
        self.assertEqual(FakeDesktop.instances, [])

    def test_authorized_clipboard_over_tls(self):
        from unittest.mock import Mock
        clipboard = Mock()
        clipboard.read.return_value = {'text': 'ภาษาไทย copied'}
        self.host.clipboard = clipboard
        self.answer = (True, {'control': True, 'clipboard': True})
        self.connect()
        receive_json(self.client)
        self.frame()
        send_json(self.client, {'clipboard': {'op': 'paste_text', 'text': 'hello ไทย'}})
        self.frame()
        clipboard.text.assert_called_with('hello ไทย')
        self.assertIn({'shortcut': 86}, FakeDesktop.instances[0].events)
        send_json(self.client, {'clipboard': {'op': 'copy'}})
        self.assertEqual(self.frame()['clipboard_text'], 'ภาษาไทย copied')

    def test_file_download_over_tls_matches_hash(self):
        from test_security_transfer import test_directory
        from transfers import TransferPeer
        from unittest.mock import Mock
        with test_directory() as path:
            source = path / 'sample.txt'
            source.write_bytes(b'test payload ' * 45000)
            clipboard = Mock()
            clipboard.read.return_value = {'files': [str(source)]}
            self.host.clipboard = clipboard
            self.answer = (True, {'control': True, 'files': True})
            completed = []
            transfer = TransferPeer(path / 'received', completed.append)
            try:
                self.connect()
                receive_json(self.client)
                self.frame()
                send_json(self.client, {'clipboard': {'op': 'copy'}})
                for _ in range(50):
                    meta = self.frame()
                    if meta.get('transfer'):
                        transfer.receive(meta['transfer'])
                    send_json(self.client, {'transfer': transfer.next_message()})
                    if completed:
                        break
                self.assertEqual(len(completed), 1)
                self.assertEqual(completed[0].read_bytes(), source.read_bytes())
                self.client.close()
                self.host.stop()
                self.host.thread.join(3)
            finally:
                transfer.cancel()
                self.host.stop()
                self.host.thread.join(3)


class ProtocolTests(unittest.TestCase):
    def test_network_allowlist(self):
        for value in ['192.168.1.20', '10.2.3.4', '172.16.1.2', '127.0.0.1']:
            self.assertEqual(lan_address(value), value)
        for value in ['8.8.8.8', '172.32.0.1', '100.64.0.1', '::1', 'example.com']:
            with self.assertRaises(ValueError):
                lan_address(value)

    def test_oversize_packet_rejected_without_body(self):
        a, b = socket.socketpair()
        try:
            a.sendall(struct.pack('!I', MAX_PACKET + 1))
            with self.assertRaises(ValueError):
                receive_packet(b)
        finally:
            a.close()
            b.close()


class InputTests(unittest.TestCase):
    def setUp(self):
        from unittest.mock import Mock
        self.desktop = WindowsDesktop.__new__(WindowsDesktop)
        self.desktop.user = Mock()
        self.desktop.keys, self.desktop.buttons = set(), set()
        self.desktop.width, self.desktop.height = 1920, 1080

    def test_release_held_keys_and_mouse(self):
        self.desktop.apply([{'type': 'key', 'code': 17, 'down': True},
                            {'type': 'button', 'button': 1, 'down': True, 'x': .5, 'y': .5},
                            {'type': 'button', 'button': 2, 'down': True, 'x': .5, 'y': .5}])
        self.desktop.release()
        self.assertEqual(self.desktop.keys, set())
        self.assertEqual(self.desktop.buttons, set())
        self.desktop.user.keybd_event.assert_called_with(17, 0, 2, 0)
        self.desktop.user.mouse_event.assert_any_call(4, 0, 0, 0, 0)
        self.desktop.user.mouse_event.assert_any_call(0x0040, 0, 0, 0, 0)

    def test_v3_chat_live_permissions_and_non_destructive_cancel(self):
        chats = []
        settings = FakeSettings(alias='PC-Support')
        host = Host(lambda peer, stop: (True, {'control': True, 'clipboard': False, 'files': True}),
                    lambda text: None, FakeDesktop, settings=settings,
                    on_chat=lambda peer, text: chats.append((peer, text)))
        host.start(0)
        client = None
        try:
            client = connect_pinned('127.0.0.1', host.port, host.fingerprint)
            send_json(client, {'version': 3})
            hello_reply = receive_json(client)
            self.assertEqual(hello_reply['alias'], 'PC-Support')
            send_json(client, {'method': 'attended'})
            self.assertTrue(receive_json(client)['waiting'])
            session_info = receive_json(client)
            self.assertTrue(session_info['ok'])
            self.assertEqual(session_info['monitors'], 1)
            meta1 = receive_json(client, 1024 * 1024)
            if meta1.get('frame'):
                receive_packet(client)
            # Send chat from viewer to host and cancel_transfer without dropping session
            host.send_chat('สวัสดีจากเครื่องปลายทาง')
            host.set_permission('clipboard', True)
            send_json(client, {'chat': 'สวัสดีครับ กำลังตรวจสอบให้', 'cancel_transfer': True, 'quality': 'sharp'})
            meta2 = receive_json(client, 1024 * 1024)
            if meta2.get('frame'):
                receive_packet(client)
            self.assertEqual(chats, [('127.0.0.1', 'สวัสดีครับ กำลังตรวจสอบให้')])
            self.assertEqual(meta2.get('chat'), ['สวัสดีจากเครื่องปลายทาง'])
            self.assertTrue(meta2.get('clipboard'))
        finally:
            if client:
                client.close()
            host.stop()
            host.thread.join(3)

    def test_out_of_bounds_and_unknown_input_rejected(self):
        for event in [{'type': 'move', 'x': 2, 'y': 0}, {'type': 'shell', 'command': 'invalid'},
                      {'type': 'key', 'code': 9999, 'down': True}, {'type': 'move', 'x': float('nan'), 'y': 0}]:
            with self.assertRaises(ValueError):
                self.desktop.apply([event])
        self.desktop.user.SetCursorPos.assert_not_called()

    def test_json_must_be_object(self):
        a, b = socket.socketpair()
        try:
            a.sendall(struct.pack('!I', 2) + b'[]')
            with self.assertRaises(ValueError):
                receive_json(b)
        finally:
            a.close()
            b.close()


if __name__ == '__main__':
    unittest.main()

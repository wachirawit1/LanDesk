"""Attended, single-session host and Windows input adapter."""
import collections
import ctypes
from ctypes import wintypes
import hmac
import io
import ipaddress
import json
import secrets
import socket
import threading
import time
from pathlib import Path

from protocol import lan_address, make_identity, receive_json, send_json, send_packet
from security import RateLimiter, sanitize_alias, verify_password
from transfers import TransferPeer

MAX_CHAT = 1000


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [('bmiHeader', BITMAPINFOHEADER), ('bmiColors', wintypes.DWORD * 3)]


def enable_dpi_awareness(user=None):
    user = user or ctypes.windll.user32
    try:
        user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        return
    except Exception:
        pass
    try:
        user.SetProcessDPIAware()
    except Exception:
        pass


def enumerate_monitors(user=None):
    user = user or ctypes.windll.user32
    rects = []
    try:
        cb_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HMONITOR, wintypes.HDC,
                                     ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)
        def callback(hmon, hdc, lprect, lparam):
            if lprect:
                r = lprect.contents
                if r.right > r.left and r.bottom > r.top:
                    rects.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
            return True
        cb = cb_type(callback)
        user.EnumDisplayMonitors(None, None, cb, 0)
    except Exception:
        pass
    if not rects:
        w = max(1, int(user.GetSystemMetrics(0)))
        h = max(1, int(user.GetSystemMetrics(1)))
        return [(0, 0, w, h)]
    rects.sort(key=lambda r: (0 if (r[0] == 0 and r[1] == 0) else 1, r[0], r[1]))
    return rects


CURSOR_ROWS = (
    "X",
    "XX",
    "X.X",
    "X..X",
    "X...X",
    "X....X",
    "X.....X",
    "X......X",
    "X.......X",
    "X........X",
    "X.....XXXXX",
    "X..X..X",
    "X.X X..X",
    "XX  X..X",
    "X    X..X",
    "     X..X",
    "      XX",
)


def draw_cursor_pointer(image, cx, cy):
    w, h = image.size
    for dy, row in enumerate(CURSOR_ROWS):
        py = cy + dy
        if not 0 <= py < h:
            continue
        for dx, ch in enumerate(row):
            if ch == ' ':
                continue
            px = cx + dx
            if 0 <= px < w:
                image.putpixel((px, py), (0, 77, 64) if ch == 'X' else (255, 255, 255))


class WindowsDesktop:
    def __init__(self):
        self.user = ctypes.windll.user32
        self.gdi = getattr(ctypes.windll, 'gdi32', None)
        enable_dpi_awareness(self.user)
        self.keys = set()
        self.buttons = set()
        self.offset_x = 0
        self.offset_y = 0
        self.width = max(1, int(self.user.GetSystemMetrics(0)))
        self.height = max(1, int(self.user.GetSystemMetrics(1)))
        self.monitors = enumerate_monitors(self.user)
        try:
            self.user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
            self.user.PostMessageW.restype = wintypes.BOOL
            self.user.LoadKeyboardLayoutW.argtypes = [wintypes.LPCWSTR, wintypes.UINT]
            self.user.LoadKeyboardLayoutW.restype = wintypes.HKL
        except Exception:
            pass

    def _capture_gdi(self, left, top, width, height):
        if not getattr(self, 'user', None) or not getattr(self, 'gdi', None):
            return None
        w, h = max(1, width), max(1, height)
        h_screen = self.user.GetDC(0)
        if not h_screen:
            return None
        h_mem = self.gdi.CreateCompatibleDC(h_screen)
        if not h_mem:
            self.user.ReleaseDC(0, h_screen)
            return None
        bmi = BITMAPINFO()
        bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.bmiHeader.biWidth = w
        bmi.bmiHeader.biHeight = -h
        bmi.bmiHeader.biPlanes = 1
        bmi.bmiHeader.biBitCount = 32
        bmi.bmiHeader.biCompression = 0
        p_bits = ctypes.c_void_p()
        h_bmp = self.gdi.CreateDIBSection(h_mem, ctypes.byref(bmi), 0, ctypes.byref(p_bits), None, 0)
        if not h_bmp or not p_bits.value:
            if h_bmp:
                self.gdi.DeleteObject(h_bmp)
            self.gdi.DeleteDC(h_mem)
            self.user.ReleaseDC(0, h_screen)
            return None
        old = self.gdi.SelectObject(h_mem, h_bmp)
        try:
            if not self.gdi.BitBlt(h_mem, 0, 0, w, h, h_screen, left, top, 0x40CC0020):
                return None
            from PIL import Image
            buf = ctypes.string_at(p_bits.value, w * h * 4)
            return Image.frombuffer('RGB', (w, h), buf, 'raw', 'BGRX', 0, 1).copy()
        except Exception:
            return None
        finally:
            self.gdi.SelectObject(h_mem, old)
            self.gdi.DeleteObject(h_bmp)
            self.gdi.DeleteDC(h_mem)
            self.user.ReleaseDC(0, h_screen)

    def frame(self, quality='sharp', monitor=0, draw_cursor=False):
        from PIL import Image, ImageGrab
        monitors = getattr(self, 'monitors', None)
        image = None
        try:
            if monitor == -1 and getattr(self, 'user', None):
                vx = int(self.user.GetSystemMetrics(76))
                vy = int(self.user.GetSystemMetrics(77))
                vw = max(1, int(self.user.GetSystemMetrics(78)))
                vh = max(1, int(self.user.GetSystemMetrics(79)))
                self.offset_x, self.offset_y = vx, vy
                image = self._capture_gdi(vx, vy, vw, vh)
                if image is None:
                    image = ImageGrab.grab(all_screens=True).convert('RGB')
            elif monitors and isinstance(monitor, int) and 0 < monitor < len(monitors):
                left, top, right, bottom = monitors[monitor]
                self.offset_x, self.offset_y = left, top
                image = self._capture_gdi(left, top, right - left, bottom - top)
                if image is None:
                    image = ImageGrab.grab(bbox=(left, top, right, bottom), all_screens=True).convert('RGB')
            elif getattr(self, 'user', None):
                w = max(1, int(self.user.GetSystemMetrics(0)))
                h = max(1, int(self.user.GetSystemMetrics(1)))
                self.offset_x, self.offset_y = 0, 0
                image = self._capture_gdi(0, 0, w, h)
                if image is None:
                    image = ImageGrab.grab(all_screens=False).convert('RGB')
            else:
                self.offset_x, self.offset_y = 0, 0
                image = ImageGrab.grab(all_screens=False).convert('RGB')
        except Exception:
            try:
                self.offset_x, self.offset_y = 0, 0
                image = ImageGrab.grab(all_screens=False).convert('RGB')
            except Exception:
                w = max(1, getattr(self, 'width', 1280))
                h = max(1, getattr(self, 'height', 720))
                image = Image.new('RGB', (w, h), (17, 28, 39))
        self.width, self.height = image.size
        if draw_cursor and getattr(self, 'user', None):
            try:
                pt = wintypes.POINT()
                if self.user.GetCursorPos(ctypes.byref(pt)):
                    cx = pt.x - getattr(self, 'offset_x', 0)
                    cy = pt.y - getattr(self, 'offset_y', 0)
                    if 0 <= cx < self.width and 0 <= cy < self.height:
                        draw_cursor_pointer(image, cx, cy)
            except Exception:
                pass
        if quality == 'smooth':
            from PIL import Image as _PIL_Image
            image = image.resize((1600, 900), _PIL_Image.Resampling.NEAREST)
        elif quality != 'lossless' and (image.width > 3840 or image.height > 2160):
            from PIL import Image as _PIL_Image
            image = image.resize((3840, 2160), _PIL_Image.Resampling.NEAREST)
        out = io.BytesIO()
        if quality == 'lossless':
            image.save(out, format='PNG', compress_level=1)
        elif quality == 'smooth':
            image.save(out, format='JPEG', quality=70, subsampling=2)
        else:
            image.save(out, format='JPEG', quality=82, subsampling=1)
        return out.getvalue()

    def shortcut(self, code):
        self.release()
        self.apply([{'type': 'key', 'code': 17, 'down': True}, {'type': 'key', 'code': code, 'down': True},
                    {'type': 'key', 'code': code, 'down': False}, {'type': 'key', 'code': 17, 'down': False}])

    def switch_language(self):
        if not getattr(self, 'user', None):
            return
        hwnd = self.user.GetForegroundWindow()
        switched = False
        if hwnd:
            try:
                tid = self.user.GetWindowThreadProcessId(hwnd, None)
                curr = self.user.GetKeyboardLayout(tid)
                num = self.user.GetKeyboardLayoutList(0, None)
                if num > 1:
                    hkls = (wintypes.HKL * num)()
                    self.user.GetKeyboardLayoutList(num, hkls)
                    hkl_list = [h for h in hkls]
                    idx = 0
                    for i, h in enumerate(hkl_list):
                        if (h & 0xFFFF) == (curr & 0xFFFF):
                            idx = (i + 1) % len(hkl_list)
                            break
                    target_hkl = hkl_list[idx]
                else:
                    target_code = '0000041E' if (curr & 0xFFFF) == 0x0409 else '00000409'
                    target_hkl = self.user.LoadKeyboardLayoutW(target_code, 1)

                if target_hkl:
                    self.user.PostMessageW(hwnd, 0x0050, 0, target_hkl)
                    switched = True
            except Exception:
                pass

        if not switched or not hwnd:
            try:
                self.user.keybd_event(0xC0, 0x29, 0, 0)
                self.user.keybd_event(0xC0, 0x29, 2, 0)
            except Exception:
                pass

    def apply(self, events):
        if not isinstance(events, list) or len(events) > 256:
            raise ValueError('รายการคำสั่งไม่ถูกต้อง')
        ox = getattr(self, 'offset_x', 0)
        oy = getattr(self, 'offset_y', 0)
        for event in events:
            if not isinstance(event, dict):
                raise ValueError('คำสั่งไม่ถูกต้อง')
            kind = event.get('type')
            if kind in ('move', 'button'):
                x, y = event.get('x'), event.get('y')
                if (type(x) not in (float, int) or type(y) not in (float, int)
                        or not 0 <= x <= 1 or not 0 <= y <= 1):
                    raise ValueError('ตำแหน่งเมาส์ไม่ถูกต้อง')
                px = round(x * (self.width - 1))
                py = round(y * (self.height - 1))
                self.user.SetCursorPos(ox + px if ox else px, oy + py if oy else py)
            if kind == 'button':
                button, down = event.get('button'), event.get('down')
                if button not in (1, 2, 3) or type(down) is not bool:
                    raise ValueError('ปุ่มเมาส์ไม่ถูกต้อง')
                flags = {1: (0x0002, 0x0004), 2: (0x0020, 0x0040), 3: (0x0008, 0x0010)}
                self.user.mouse_event(flags[button][0 if down else 1], 0, 0, 0, 0)
                (self.buttons.add if down else self.buttons.discard)(button)
            elif kind == 'key':
                code, down = event.get('code'), event.get('down')
                if type(code) is not int or not 8 <= code <= 254 or type(down) is not bool:
                    raise ValueError('รหัสคีย์ไม่ถูกต้อง')
                extended = code in {33, 34, 35, 36, 37, 38, 39, 40, 45, 46, 91, 92, 111, 163, 165}
                scan = 0x29 if code == 192 else (self.user.MapVirtualKeyW(code, 0) if getattr(self, 'user', None) else 0)
                self.user.keybd_event(code, scan, (1 if extended else 0) | (0 if down else 2), 0)
                (self.keys.add if down else self.keys.discard)(code)
            elif kind == 'lang':
                self.switch_language()
            elif kind == 'wheel':
                delta = event.get('delta')
                if type(delta) is not int or not -1200 <= delta <= 1200:
                    raise ValueError('การเลื่อนไม่ถูกต้อง')
                self.user.mouse_event(0x0800, 0, 0, delta, 0)
            elif kind == 'release':
                self.release()
            elif kind != 'move':
                raise ValueError('ไม่รองรับคำสั่งนี้')

    def release(self):
        for code in list(self.keys):
            self.apply([{'type': 'key', 'code': code, 'down': False}])
        for button in list(self.buttons):
            self.user.mouse_event({1: 0x0004, 2: 0x0040, 3: 0x0010}[button], 0, 0, 0, 0)
        self.keys.clear()
        self.buttons.clear()


class Host:
    def __init__(self, consent, notify, desktop_factory=WindowsDesktop, audit_path=None,
                 settings=None, clipboard=None, receive_directory=None,
                 on_chat=lambda peer, text: None, on_session=lambda active, peer, perms: None,
                 identity=None):
        self.consent, self.notify, self.desktop_factory = consent, notify, desktop_factory
        self.audit_path = Path(audit_path) if audit_path else None
        self.stop_event = threading.Event()
        self.listener = self.active = None
        self.control = False
        self.input_lock = threading.Lock()
        self.desktop = None
        self.failures = 0
        self.locked_until = 0
        self.settings, self.clipboard = settings, clipboard
        self.receive_directory = receive_directory
        self.limiter = RateLimiter()
        self.permissions = {'control': False, 'clipboard': False, 'files': False}
        self.on_chat = on_chat
        self.on_session = on_session
        self.identity = identity
        self.chat_out = collections.deque(maxlen=32)
        self.transfer = None
        self.connected_peer = None

    def config(self):
        return self.settings.snapshot() if self.settings else {'password': None, 'control': True,
                'clipboard': False, 'files': False, 'max_minutes': 60, 'allow_networks': '', 'alias': ''}

    def audit(self, action, peer='', detail=''):
        if self.audit_path:
            try:
                if self.audit_path.exists() and self.audit_path.stat().st_size > 5 * 1024 * 1024:
                    self.audit_path.replace(self.audit_path.with_suffix('.previous.jsonl'))
                with self.audit_path.open('a', encoding='utf-8') as file:
                    file.write(json.dumps({'time': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
                                           'action': action, 'peer': peer, 'detail': detail},
                                          ensure_ascii=False) + '\n')
            except OSError:
                self.notify('ไม่สามารถเขียนบันทึกการใช้งานได้')

    def start(self, port=45888):
        if self.identity:
            self.context, self.fingerprint = self.identity
        else:
            self.context, self.fingerprint = make_identity(settings=self.settings)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
            self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            self.listener.bind(('0.0.0.0', port))
            self.listener.listen(2)
            self.listener.settimeout(.5)
        except Exception:
            self.listener.close()
            raise
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.run, daemon=True)
        self.thread.start()
        self.audit('host_started')

    def set_permission(self, key, allowed):
        if key not in self.permissions:
            return
        with self.input_lock:
            flag = bool(allowed)
            self.permissions[key] = flag
            if key == 'control':
                self.control = flag
                if not flag and self.desktop:
                    self.desktop.release()
            elif key == 'files' and not flag and self.transfer:
                self.transfer.abort(notify_peer=True)

    def set_control(self, allowed):
        self.set_permission('control', allowed)

    def send_chat(self, text):
        if not isinstance(text, str):
            return False
        cleaned = text.strip()
        if not cleaned or len(cleaned) > MAX_CHAT or '\x00' in cleaned:
            raise ValueError('ข้อความแชทยาวเกินกำหนด (สูงสุด 1,000 ตัวอักษร)')
        if not self.connected_peer:
            raise ValueError('ยังไม่มีผู้เชื่อมต่ออยู่ในขณะนี้')
        self.chat_out.append(cleaned)
        return True

    def cancel_transfer(self):
        if self.transfer:
            self.transfer.abort(notify_peer=True)
            self.notify('ยกเลิกการโอนไฟล์แล้ว')

    def disconnect(self):
        self.set_control(False)
        active = self.active
        if active:
            try:
                active.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            active.close()

    def stop(self):
        self.stop_event.set()
        self.disconnect()
        if self.listener:
            self.listener.close()

    def run(self):
        while not self.stop_event.is_set():
            try:
                raw, address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            peer = address[0]
            try:
                raw.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
            connection = raw
            transfer = None
            try:
                lan_address(peer)
                config = self.config()
                networks = [ipaddress.ip_network(n.strip()) for n in config.get('allow_networks', '').split(',') if n.strip()]
                if networks and not any(ipaddress.ip_address(peer) in n for n in networks):
                    self.audit('network_denied', peer)
                    continue
                raw.settimeout(10)
                if not self.limiter.admit(peer):
                    self.audit('rate_limited', peer)
                    continue
                self.active = raw
                connection = self.context.wrap_socket(raw, server_side=True)
                self.active = connection
                connection.settimeout(65)
                hello = receive_json(connection, 4096)
                if hello.get('version') not in (2, 3):
                    raise ValueError('กรุณาใช้ LanDesk รุ่นที่รองรับ (0.2.0 ขึ้นไป) ทั้งสองฝั่ง')
                alias = sanitize_alias(config.get('alias') or '') or socket.gethostname()
                send_json(connection, {'ok': True, 'version': 3, 'alias': alias,
                                       'password_available': bool(config.get('password'))})
                connection.settimeout(60)
                allowed = False
                permissions = {}
                method = 'attended'
                for attempt in range(3):
                    auth = receive_json(connection, 4096)
                    method = auth.get('method')
                    if method == 'password':
                        if verify_password(auth.get('password'), config.get('password')):
                            allowed = True
                            permissions = {k: bool(config.get(k)) for k in self.permissions}
                            send_json(connection, {'ok': True, 'waiting': False})
                            break
                        else:
                            self.limiter.fail(peer)
                            self.audit('authentication_failed', peer)
                            remaining = 2 - attempt
                            if remaining > 0:
                                send_json(connection, {
                                    'ok': False,
                                    'retry': True,
                                    'remaining': remaining,
                                    'error': f'รหัสผ่านไม่ถูกต้อง (เหลือโอกาสอีก {remaining} ครั้ง)'
                                })
                            else:
                                send_json(connection, {
                                    'ok': False,
                                    'retry': False,
                                    'error': 'รหัสผ่านไม่ถูกต้องครบ 3 ครั้ง ระบบตัดการเชื่อมต่อเพื่อความปลอดภัย'
                                })
                                break
                    elif method == 'attended':
                        send_json(connection, {'ok': True, 'waiting': True})
                        allowed, permissions = self.consent(peer, self.stop_event)
                        break
                    else:
                        raise ValueError('วิธียืนยันตัวตนไม่รองรับ')
                if not allowed or self.stop_event.is_set():
                    self.audit('consent_denied', peer)
                    if method == 'attended':
                        send_json(connection, {'ok': False, 'error': 'ปลายทางไม่อนุญาต หรือหมดเวลารอ'})
                    continue
                self.desktop = self.desktop_factory()
                monitors_count = len(getattr(self.desktop, 'monitors', [(0, 0, 1920, 1080)]))
                self.permissions = {k: permissions.get(k) is True for k in self.permissions}
                self.set_control(self.permissions['control'])
                self.connected_peer = peer
                self.chat_out.clear()
                self.audit('session_started', peer, method + ':' + json.dumps(self.permissions))
                self.notify('เชื่อมต่อกับ ' + peer)
                self.on_session(True, peer, dict(self.permissions))
                send_json(connection, {'ok': True, 'monitors': monitors_count, 'alias': alias, **self.permissions})
                connection.settimeout(10)
                received_paths = []
                received_batch = [None]
                def complete(path):
                    if received_batch[0] != transfer.received_batch:
                        received_paths.clear()
                        received_batch[0] = transfer.received_batch
                    received_paths.append(path)
                    self.audit('file_received', peer, path.name)
                    if self.clipboard and self.permissions['files']:
                        self.clipboard.files(received_paths[-500:])
                transfer = TransferPeer(self.receive_directory or Path.cwd() / 'Received', complete, self.notify)
                self.transfer = transfer
                quality, monitor, draw_cursor, previous, extra = 'sharp', 0, False, None, {}
                deadline = time.monotonic() + min(240, max(1, int(config.get('max_minutes', 60)))) * 60
                while not self.stop_event.is_set() and time.monotonic() < deadline:
                    started = time.monotonic()
                    try:
                        frame = self.desktop.frame(quality, monitor=monitor, draw_cursor=draw_cursor)
                    except TypeError:
                        frame = self.desktop.frame(quality)
                    changed = frame != previous
                    xfer = transfer.next_message() if (self.permissions['files'] or transfer.replies) else None
                    if self.chat_out:
                        extra['chat'] = list(self.chat_out)
                        self.chat_out.clear()
                    # Auto-detect remote clipboard changes (e.g. user right-clicks Copy on host)
                    if self.clipboard and (self.permissions['clipboard'] or self.permissions['files']):
                        try:
                            h_seq = self.clipboard.sequence_number()
                            if h_seq and h_seq != getattr(self, 'last_h_seq', 0):
                                self.last_h_seq = h_seq
                                c_data = self.clipboard.read()
                                if 'text' in c_data and self.permissions['clipboard']:
                                    extra['clipboard_text'] = c_data['text']
                                elif c_data.get('files') and self.permissions['files']:
                                    transfer.queue_files(c_data['files'])
                                    self.audit('files_sent', peer, str(len(c_data['files'])))
                        except Exception:
                            pass
                    send_json(connection, {**self.permissions, 'monitors': monitors_count,
                                           'frame': changed, 'transfer': xfer, **extra})
                    extra = {}
                    if changed:
                        send_packet(connection, frame)
                        previous = frame
                    request = receive_json(connection, 1024 * 1024)
                    if request.get('stop') or self.stop_event.is_set():
                        break
                    # Apply mouse and keyboard input immediately to reduce input lag to minimum
                    with self.input_lock:
                        if self.control and not self.stop_event.is_set():
                            self.desktop.apply(request.get('events', []))
                    requested_quality = request.get('quality', quality)
                    if requested_quality not in ('sharp', 'smooth', 'lossless'):
                        raise ValueError('โหมดภาพไม่รองรับ')
                    quality = requested_quality
                    req_mon = request.get('monitor', monitor)
                    if type(req_mon) is int and -1 <= req_mon < max(1, monitors_count):
                        if req_mon != monitor:
                            monitor = req_mon
                            previous = None
                    draw_cursor = bool(request.get('draw_cursor', draw_cursor))
                    # Every privileged operation is checked at the receiving endpoint.
                    if request.get('switch_lang'):
                        with self.input_lock:
                            if self.control and hasattr(self.desktop, 'switch_language'):
                                self.desktop.switch_language()
                    if request.get('cancel_transfer'):
                        transfer.abort(notify_peer=False)
                        self.notify('ยกเลิกการโอนไฟล์แล้ว')
                    if request.get('transfer'):
                        msg = request['transfer']
                        if isinstance(msg, dict) and msg.get('op') == 'cancel':
                            transfer.receive(msg)
                        elif not self.permissions.get('files'):
                            transfer.abort(notify_peer=True)
                            self.notify('ไม่มีสิทธิ์โอนไฟล์ ยกเลิกการส่ง')
                        else:
                            transfer.receive(msg)
                    if 'chat' in request and request['chat'] is not None:
                        chat_msg = request['chat']
                        if not isinstance(chat_msg, str) or not 1 <= len(chat_msg.strip()) <= MAX_CHAT or '\x00' in chat_msg:
                            raise ValueError('ข้อความแชทไม่ถูกต้อง')
                        cleaned_chat = chat_msg.strip()
                        self.audit('chat_received', peer)
                        self.on_chat(peer, cleaned_chat)
                    clip = request.get('clipboard')
                    if clip:
                        if not self.clipboard:
                            raise ValueError('Clipboard ไม่พร้อม')
                        op = clip.get('op') if isinstance(clip, dict) else None
                        if op == 'paste_text':
                            if not self.permissions['clipboard']:
                                raise ValueError('ไม่มีสิทธิ์ Clipboard')
                            self.clipboard.text(clip.get('text'))
                            with self.input_lock:
                                if self.control:
                                    self.desktop.shortcut(86)
                        elif op == 'set_text':
                            if not self.permissions['clipboard']:
                                raise ValueError('ไม่มีสิทธิ์ Clipboard')
                            self.clipboard.text(clip.get('text'))
                        elif op == 'paste_files':
                            if not self.permissions['files']:
                                raise ValueError('ไม่มีสิทธิ์ไฟล์')
                            with self.input_lock:
                                if self.control and self.clipboard.read().get('files'):
                                    self.desktop.shortcut(86)
                        elif op == 'copy':
                            if not (self.permissions['clipboard'] or self.permissions['files']):
                                raise ValueError('ไม่มีสิทธิ์ Clipboard หรือไฟล์')
                            with self.input_lock:
                                if self.control:
                                    self.desktop.shortcut(67)
                            self.stop_event.wait(.2)
                            content = self.clipboard.read()
                            if 'text' in content and self.permissions['clipboard']:
                                extra['clipboard_text'] = content['text']
                            elif content.get('files') and self.permissions['files']:
                                transfer.queue_files(content['files'])
                                self.audit('files_sent', peer, str(len(content['files'])))
                        else:
                            raise ValueError('คำสั่ง Clipboard ไม่รองรับ')
                    target_delay = 0.020 if quality == 'smooth' else (0.040 if quality == 'sharp' else 0.08)
                    self.stop_event.wait(max(0, target_delay - (time.monotonic() - started)))
            except Exception as error:
                if not self.stop_event.is_set():
                    self.notify('สิ้นสุดการเชื่อมต่อ: ' + str(error))
                    self.audit('connection_error', peer, type(error).__name__)
            finally:
                if transfer:
                    transfer.cancel()
                self.transfer = None
                self.connected_peer = None
                self.set_control(False)
                self.desktop = None
                connection.close()
                raw.close()
                self.active = None
                self.on_session(False, peer, dict(self.permissions))
                self.audit('connection_closed', peer)
                if not self.stop_event.is_set():
                    self.notify('พร้อมรับการเชื่อมต่อ')

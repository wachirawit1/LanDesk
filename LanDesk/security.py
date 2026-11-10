"""Local security settings: user-bound DPAPI encryption and scrypt verifiers."""
import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from pathlib import Path
from ctypes import wintypes

VERSION = '0.3.2'
DEFAULT_PORT = 45888
STARTUP_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
STARTUP_VALUE = 'LanDesk'


def password_record(password):
    if not isinstance(password, str) or not 4 <= len(password) <= 128:
        raise ValueError('รหัสผ่านต้องมีความยาวอย่างน้อย 4 ตัวอักษร')
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1,
                            maxmem=64 * 1024 * 1024, dklen=32)
    return {'salt': salt.hex(), 'digest': digest.hex()}


def verify_password(password, record):
    if not record or not isinstance(password, str) or len(password) > 128:
        return False
    try:
        salt = bytes.fromhex(record['salt'])
        expected = bytes.fromhex(record['digest'])
        if len(salt) != 16 or len(expected) != 32:
            return False
        actual = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=32768, r=8, p=1,
                                maxmem=64 * 1024 * 1024, dklen=32)
        return hmac.compare_digest(actual, expected)
    except (KeyError, TypeError, ValueError):
        return False


def dpapi(data, decrypt=False):
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = Blob()
    crypt = ctypes.WinDLL('crypt32', use_last_error=True)
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p,
                       ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), 'LanDesk', None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        kernel.LocalFree(target.data)


def sanitize_alias(name):
    if not isinstance(name, str):
        return ''
    cleaned = ''.join(ch for ch in name.strip() if ch.isprintable() and ch not in '\r\n\t')
    return cleaned[:48]


def startup_command(background=True):
    import sys
    flag = ' --background' if background else ''
    if getattr(sys, 'frozen', False):
        return f'"{Path(sys.executable).resolve()}"{flag}'
    app_script = Path(__file__).resolve().with_name('app.py')
    return f'"{Path(sys.executable).resolve()}" "{app_script}"{flag}'


def set_startup_enabled(enabled, command=None, background=True):
    if os.name != 'nt':
        return False
    import winreg
    cmd = command or startup_command(background=background)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, STARTUP_VALUE, 0, winreg.REG_SZ, cmd)
            return True
        try:
            winreg.DeleteValue(key, STARTUP_VALUE)
        except FileNotFoundError:
            pass
        return False


def is_startup_enabled():
    if os.name != 'nt':
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ) as key:
            value, _ = winreg.QueryValueEx(key, STARTUP_VALUE)
            return bool(value)
    except OSError:
        return False


class Settings:
    def __init__(self, directory=None, protect=dpapi):
        self.directory = Path(directory or Path(os.environ['LOCALAPPDATA']) / 'LanDesk')
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'settings-v2.dat'
        self.protect = protect
        self.lock = threading.RLock()
        self.data = {'password': None, 'port': DEFAULT_PORT, 'trusted': {},
                     'control': True, 'clipboard': True, 'files': True,
                     'auto_listen': True, 'allow_networks': '', 'max_minutes': 60,
                     'alias': '', 'contacts': [], 'startup': True,
                     'close_to_tray': True, 'service_migrated_v03': True,
                     'github_repo': 'wachirawit1/LanDesk', 'auto_check_update': True}
        if self.path.exists():
            try:
                loaded = json.loads(protect(self.path.read_bytes(), decrypt=True))
                if not loaded.get('service_migrated_v03'):
                    loaded['auto_listen'] = True
                    loaded['startup'] = True
                    loaded['close_to_tray'] = True
                    loaded['clipboard'] = True
                    loaded['files'] = True
                    loaded['service_migrated_v03'] = True
                if not loaded.get('github_repo') or loaded.get('github_repo') == 'owner/landesk':
                    loaded['github_repo'] = 'wachirawit1/LanDesk'
                loaded['control'] = True
                loaded['clipboard'] = True
                loaded['files'] = True
                self.data.update(loaded)
            except Exception as error:
                raise ValueError('อ่านการตั้งค่าที่เข้ารหัสไม่ได้ กรุณาใช้บัญชี Windows เดิม ห้ามเปิดรับด้วยค่าที่ไม่ตรวจสอบ') from error

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.data))

    def save(self, **changes):
        with self.lock:
            updated = {**self.data, **changes}
            protected = self.protect(json.dumps(updated, ensure_ascii=False).encode('utf-8'))
            temporary = self.path.with_suffix('.new')
            temporary.write_bytes(protected)
            os.replace(temporary, self.path)
            self.data = updated

    def trust(self, address, fingerprint):
        with self.lock:
            trusted = dict(self.data['trusted'])
            trusted[address] = fingerprint
            self.save(trusted=trusted)

    def upsert_contact(self, ip, name=None, favorite=None, touched=False):
        from protocol import lan_address
        ip = lan_address(ip.strip())
        with self.lock:
            contacts = [dict(item) for item in self.data.get('contacts', []) if isinstance(item, dict)]
            existing = next((item for item in contacts if item.get('ip') == ip), None)
            now_str = time.strftime('%Y-%m-%d %H:%M') if touched else ''
            if existing:
                if name is not None:
                    existing['name'] = sanitize_alias(name)
                if favorite is not None:
                    existing['favorite'] = bool(favorite)
                if touched:
                    existing['last_connected'] = now_str
            else:
                if len(contacts) >= 100:
                    contacts.pop(-1)
                contacts.insert(0, {
                    'ip': ip,
                    'name': sanitize_alias(name or ''),
                    'favorite': bool(favorite) if favorite is not None else False,
                    'last_connected': now_str,
                })
            contacts.sort(key=lambda c: (not c.get('favorite', False), -(1 if c.get('last_connected') else 0), c.get('ip', '')))
            self.save(contacts=contacts)
            return contacts

    def remove_contact(self, ip):
        with self.lock:
            contacts = [dict(item) for item in self.data.get('contacts', [])
                        if isinstance(item, dict) and item.get('ip') != ip]
            self.save(contacts=contacts)
            return contacts


class RateLimiter:
    """Global bounds prevent rotating source IPs from bypassing throttles."""
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.failed = []
        self.attempts = []

    def admit(self, peer):
        now = self.clock()
        self.failed = [(t, p) for t, p in self.failed if now - t < 60]
        self.attempts = [t for t in self.attempts if now - t < 60]
        if len(self.failed) >= 20 or sum(p == peer for _, p in self.failed) >= 5 or len(self.attempts) >= 30:
            return False
        self.attempts.append(now)
        return True

    def fail(self, peer):
        self.failed.append((self.clock(), peer))

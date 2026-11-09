import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import time

import re

root = Path.cwd()
version = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", (root / 'LanDesk/security.py').read_text('utf-8')).group(1)
env = dict(os.environ, LOCALAPPDATA=str(root / 'work/smoke-profile'))
startup = subprocess.STARTUPINFO()
startup.dwFlags |= subprocess.STARTF_USESHOWWINDOW
startup.wShowWindow = 0
process = subprocess.Popen([str(root / f'outputs/v{version}/Windows/LanDesk/LanDesk.exe')], env=env, startupinfo=startup)
user = ctypes.WinDLL('user32', use_last_error=True)
callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
windows = []
@callback_type
def collect(hwnd, unused):
    pid = wintypes.DWORD()
    user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == process.pid:
        text = ctypes.create_unicode_buffer(512)
        user.GetWindowTextW(hwnd, text, 512)
        windows.append((hwnd, text.value))
    return True

try:
    for attempt in range(30):
        windows.clear()
        user.EnumWindows(collect, 0)
        matches = [(hwnd, title) for hwnd, title in windows if version in title]
        if matches:
            print('Executable main window:', matches[0][1].encode('ascii', 'backslashreplace').decode('ascii'))
            user.PostMessageW(matches[0][0], 0x10, 0, 0)
            process.wait(timeout=10)
            assert process.returncode == 0
            print('Clean shutdown: OK')
            break
        if process.poll() is not None:
            raise RuntimeError(f'Exited early: {process.returncode}')
        time.sleep(.25)
    else:
        raise RuntimeError(f'Main window missing: {windows}')
finally:
    if process.poll() is None:
        process.kill()
        process.wait()

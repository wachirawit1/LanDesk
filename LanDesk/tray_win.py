"""Windows System Tray agent, single-instance wakeup, and window icon helpers."""
import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import sys
import threading

WM_USER = 0x0400
WM_TRAYICON = WM_USER + 20
WM_COMMAND = 0x0111
WM_DESTROY = 0x0002

WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_INFO = 0x00000010

IMAGE_ICON = 1
LR_LOADFROMFILE = 0x00000010
LR_DEFAULTSIZE = 0x00000040

TPM_LEFTALIGN = 0x0000
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080
MF_STRING = 0x0000
MF_SEPARATOR = 0x0800

SINGLE_INSTANCE_EVENT = 'Local\\LanDeskShowWindowEvent_v03'
SINGLE_INSTANCE_MUTEX = 'Local\\LanDeskSingleInstanceMutex_v03'


def icon_file_path():
    candidates = []
    if getattr(sys, 'frozen', False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / 'landesk.ico')
        candidates.append(exe_dir / '_internal' / 'landesk.ico')
    here = Path(__file__).resolve().parent
    candidates.append(here / 'landesk.ico')
    for path in candidates:
        if path.exists():
            return path
    return None


def set_app_user_model_id():
    if os.name != 'nt':
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('LanDesk.IntranetRemote.0.3')
    except Exception:
        pass


def apply_window_icon(window):
    ico = icon_file_path()
    if ico and ico.exists():
        try:
            window.iconbitmap(default=str(ico))
            return
        except Exception:
            try:
                window.iconbitmap(str(ico))
            except Exception:
                pass


class SingleInstanceGuard:
    """Ensures only one LanDesk instance runs per session; second launch wakes the existing window."""
    def __init__(self, on_wakeup=None):
        self.on_wakeup = on_wakeup
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.mutex = None
        self.event = None
        self.already_running = False
        self.stop_event = threading.Event()

        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateEventW.restype = wintypes.HANDLE
        self.kernel.SetEvent.argtypes = [wintypes.HANDLE]
        self.kernel.SetEvent.restype = wintypes.BOOL
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]

        self.mutex = self.kernel.CreateMutexW(None, False, SINGLE_INSTANCE_MUTEX)
        last_err = ctypes.get_last_error()
        self.event = self.kernel.CreateEventW(None, False, False, SINGLE_INSTANCE_EVENT)
        if last_err == 183:  # ERROR_ALREADY_EXISTS
            self.already_running = True
            if self.event:
                self.kernel.SetEvent(self.event)
        elif self.event and self.on_wakeup:
            threading.Thread(target=self._watch, daemon=True).start()

    def _watch(self):
        while not self.stop_event.is_set():
            res = self.kernel.WaitForSingleObject(self.event, 400)
            if res == 0 and not self.stop_event.is_set():
                try:
                    self.on_wakeup()
                except Exception:
                    pass

    def close(self):
        self.stop_event.set()
        if self.event:
            self.kernel.CloseHandle(self.event)
            self.event = None
        if self.mutex:
            self.kernel.CloseHandle(self.mutex)
            self.mutex = None


WNDPROCTYPE = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ('style', wintypes.UINT),
        ('lpfnWndProc', WNDPROCTYPE),
        ('cbClsExtra', ctypes.c_int),
        ('cbWndExtra', ctypes.c_int),
        ('hInstance', wintypes.HINSTANCE),
        ('hIcon', wintypes.HICON),
        ('hCursor', wintypes.HANDLE),
        ('hbrBackground', wintypes.HBRUSH),
        ('lpszMenuName', wintypes.LPCWSTR),
        ('lpszClassName', wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('hWnd', wintypes.HWND),
        ('uID', wintypes.UINT),
        ('uFlags', wintypes.UINT),
        ('uCallbackMessage', wintypes.UINT),
        ('hIcon', wintypes.HICON),
        ('szTip', wintypes.WCHAR * 128),
        ('dwState', wintypes.DWORD),
        ('dwStateMask', wintypes.DWORD),
        ('szInfo', wintypes.WCHAR * 256),
        ('uTimeoutOrVersion', wintypes.UINT),
        ('szInfoTitle', wintypes.WCHAR * 64),
        ('dwInfoFlags', wintypes.DWORD),
    ]


class SystemTrayIcon:
    """Lightweight Win32 System Tray icon so LanDesk runs in the background like AnyDesk/VNC."""
    def __init__(self, tooltip, on_open, on_quit):
        self.tooltip = tooltip[:120]
        self.on_open = on_open
        self.on_quit = on_quit
        self.hwnd = None
        self.hicon = None
        self.nid = None
        self._closed = threading.Event()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=2.0)

    def _run(self):
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        shell32 = ctypes.WinDLL('shell32', use_last_error=True)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

        user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        user32.DefWindowProcW.restype = ctypes.c_ssize_t
        user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT]
        user32.LoadImageW.restype = wintypes.HANDLE
        user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
        user32.LoadIconW.restype = wintypes.HICON
        user32.CreatePopupMenu.argtypes = []
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
        user32.TrackPopupMenu.restype = wintypes.BOOL
        user32.DestroyMenu.argtypes = [wintypes.HMENU]
        user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
        user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        user32.PostQuitMessage.argtypes = [ctypes.c_int]
        shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
        shell32.Shell_NotifyIconW.restype = wintypes.BOOL

        def wnd_proc(hwnd, msg, wparam, lparam):
            if msg == WM_TRAYICON:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self.on_open()
                elif event == WM_RBUTTONUP:
                    pt = wintypes.POINT()
                    user32.GetCursorPos(ctypes.byref(pt))
                    menu = user32.CreatePopupMenu()
                    user32.AppendMenuW(menu, MF_STRING, 1001, 'เปิดหน้าต่าง LanDesk (Open)')
                    user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
                    user32.AppendMenuW(menu, MF_STRING, 1002, 'ปิดเซอร์วิสและออกจากโปรแกรม (Quit)')
                    user32.SetForegroundWindow(hwnd)
                    cmd = user32.TrackPopupMenu(menu, TPM_LEFTALIGN | TPM_RETURNCMD | TPM_NONOTIFY, pt.x, pt.y, 0, hwnd, None)
                    user32.DestroyMenu(menu)
                    if cmd == 1001:
                        self.on_open()
                    elif cmd == 1002:
                        self.on_quit()
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

        self._wnd_proc_ref = WNDPROCTYPE(wnd_proc)
        hinst = kernel32.GetModuleHandleW(None)
        class_name = f'LanDeskTrayWnd_{os.getpid()}_{id(self)}'
        wc = WNDCLASSW()
        wc.lpfnWndProc = self._wnd_proc_ref
        wc.hInstance = hinst
        wc.lpszClassName = class_name
        user32.RegisterClassW(ctypes.byref(wc))

        self.hwnd = user32.CreateWindowExW(0, class_name, 'LanDeskTray', 0, 0, 0, 0, 0, None, None, hinst, None)
        ico_path = icon_file_path()
        if ico_path and ico_path.exists():
            self.hicon = user32.LoadImageW(None, str(ico_path), IMAGE_ICON, 0, 0, LR_LOADFROMFILE | LR_DEFAULTSIZE)
        if not self.hicon:
            self.hicon = user32.LoadIconW(None, ctypes.cast(32512, wintypes.LPCWSTR))

        self.nid = NOTIFYICONDATAW()
        self.nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        self.nid.hWnd = self.hwnd
        self.nid.uID = 1
        self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        self.nid.uCallbackMessage = WM_TRAYICON
        self.nid.hIcon = self.hicon
        self.nid.szTip = self.tooltip
        shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(self.nid))
        self._ready.set()

        msg = wintypes.MSG()
        while not self._closed.is_set() and user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self.nid))

    def notify_balloon(self, title, message):
        if self._closed.is_set() or not self.nid:
            return
        try:
            shell32 = ctypes.WinDLL('shell32', use_last_error=True)
            self.nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_INFO
            self.nid.szInfoTitle = title[:60]
            self.nid.szInfo = message[:250]
            self.nid.dwInfoFlags = 1  # NIIF_INFO
            shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(self.nid))
        except Exception:
            pass

    def close(self):
        if self._closed.is_set():
            return
        self._closed.set()
        if self.hwnd:
            try:
                user32 = ctypes.WinDLL('user32', use_last_error=True)
                user32.PostMessageW(self.hwnd, WM_DESTROY, 0, 0)
            except Exception:
                pass

"""Explicit clipboard operations only; no background clipboard monitoring."""
import ctypes
import struct
import time
from ctypes import wintypes

MAX_TEXT = 64 * 1024


class Clipboard:
    def __init__(self, hwnd=0):
        self.hwnd = hwnd
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.shell = ctypes.WinDLL('shell32', use_last_error=True)
        self.user.OpenClipboard.argtypes = [wintypes.HWND]
        self.user.GetClipboardSequenceNumber.argtypes = []
        self.user.GetClipboardSequenceNumber.restype = wintypes.DWORD
        self.user.GetClipboardData.argtypes = [wintypes.UINT]
        self.user.GetClipboardData.restype = wintypes.HANDLE
        self.user.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user.SetClipboardData.restype = wintypes.HANDLE
        self.kernel.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalLock.restype = ctypes.c_void_p
        self.kernel.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalFree.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalSize.argtypes = [wintypes.HGLOBAL]
        self.kernel.GlobalSize.restype = ctypes.c_size_t
        self.shell.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR, wintypes.UINT]
        self.shell.DragQueryFileW.restype = wintypes.UINT

    def sequence_number(self):
        try:
            return int(self.user.GetClipboardSequenceNumber())
        except Exception:
            return 0

    def open(self):
        for _ in range(10):
            if self.user.OpenClipboard(self.hwnd):
                return
            time.sleep(.02)
        raise ValueError('Clipboard ถูกใช้งานอยู่ กรุณาลองอีกครั้ง')

    def read(self):
        self.open()
        try:
            if self.user.IsClipboardFormatAvailable(15):
                handle = self.user.GetClipboardData(15)
                count = self.shell.DragQueryFileW(handle, 0xffffffff, None, 0)
                if count > 50:
                    raise ValueError('คัดลอกได้ไม่เกิน 50 ไฟล์')
                paths = []
                for index in range(count):
                    length = self.shell.DragQueryFileW(handle, index, None, 0)
                    if length > 32767:
                        raise ValueError('ชื่อไฟล์ยาวเกินไป')
                    buffer = ctypes.create_unicode_buffer(length + 1)
                    self.shell.DragQueryFileW(handle, index, buffer, length + 1)
                    paths.append(buffer.value)
                return {'files': paths}
            if self.user.IsClipboardFormatAvailable(13):
                handle = self.user.GetClipboardData(13)
                size = self.kernel.GlobalSize(handle)
                if size > 2 * MAX_TEXT + 2:
                    raise ValueError('ข้อความใน Clipboard ยาวเกิน 64K ตัวอักษร')
                pointer = self.kernel.GlobalLock(handle)
                if not pointer:
                    raise ctypes.WinError(ctypes.get_last_error())
                try:
                    text = ctypes.string_at(pointer, size).decode('utf-16-le').split('\x00')[0]
                finally:
                    self.kernel.GlobalUnlock(handle)
                return {'text': text}
            return {}
        finally:
            self.user.CloseClipboard()

    def write(self, fmt, data):
        handle = self.kernel.GlobalAlloc(0x0002, len(data))
        if not handle:
            raise MemoryError('Clipboard allocation failed')
        try:
            pointer = self.kernel.GlobalLock(handle)
            if not pointer:
                raise ctypes.WinError(ctypes.get_last_error())
            ctypes.memmove(pointer, data, len(data))
            self.kernel.GlobalUnlock(handle)
            self.open()
            try:
                if not self.user.EmptyClipboard() or not self.user.SetClipboardData(fmt, handle):
                    raise ctypes.WinError(ctypes.get_last_error())
                handle = None  # Ownership transferred to Windows.
            finally:
                self.user.CloseClipboard()
        finally:
            if handle:
                self.kernel.GlobalFree(handle)

    def text(self, value):
        if not isinstance(value, str) or len(value) > MAX_TEXT or '\x00' in value:
            raise ValueError('ข้อความ Clipboard ไม่ถูกต้อง')
        self.write(13, (value + '\x00').encode('utf-16-le'))

    def files(self, paths):
        self.write(15, struct.pack('<IiiII', 20, 0, 0, 0, 1) +
                   ('\x00'.join(str(p) for p in paths) + '\x00\x00').encode('utf-16-le'))

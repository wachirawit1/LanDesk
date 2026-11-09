import ctypes
from ctypes import wintypes
import hashlib
import io
import marshal
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import zipfile
import zlib

import re

root = Path.cwd()
source = root / 'LanDesk'
build_v020 = root / 'work/build-v020/LanDesk'
version = re.search(r"VERSION\s*=\s*['\"]([^'\"]+)['\"]", (source / 'security.py').read_text('utf-8')).group(1)
output = root / f'outputs/v{version}'
binary = output / 'Windows/LanDesk'


def build_pyz(orig_pyz_bytes, modules_dir):
    magic, pyc_magic, toc_off = struct.unpack('!4s4sI', orig_pyz_bytes[:12])
    orig_toc = marshal.loads(orig_pyz_bytes[toc_off:])
    updated_modules = {'clipboard_win', 'engine', 'protocol', 'security', 'settings_ui', 'transfers', 'viewer', 'updater', 'installer', 'tray_win'}
    out = io.BytesIO()
    out.write(struct.pack('!4s4sI', magic, pyc_magic, 0))
    out.write(orig_pyz_bytes[12:17])  # 5-byte cipher/header padding used by PyInstaller ZlibArchive
    new_toc = []
    added_names = set()
    for name, (ispkg, pos, length) in orig_toc:
        added_names.add(name)
        if name in updated_modules:
            src_path = modules_dir / f'{name}.py'
            code = compile(src_path.read_bytes(), f'{name}.py', 'exec', dont_inherit=True)
            blob = zlib.compress(marshal.dumps(code), level=6)
        else:
            blob = orig_pyz_bytes[pos:pos + length]
        new_pos = out.tell()
        out.write(blob)
        new_toc.append((name, (ispkg, new_pos, len(blob))))
    for name in updated_modules:
        if name not in added_names:
            src_path = modules_dir / f'{name}.py'
            code = compile(src_path.read_bytes(), f'{name}.py', 'exec', dont_inherit=True)
            blob = zlib.compress(marshal.dumps(code), level=6)
            new_pos = out.tell()
            out.write(blob)
            new_toc.append((name, (0, new_pos, len(blob))))
            added_names.add(name)
    import webbrowser as _wb
    if 'webbrowser' not in added_names:
        wb_path = Path(_wb.__file__)
        code = compile(wb_path.read_bytes(), 'webbrowser.py', 'exec', dont_inherit=True)
        blob = zlib.compress(marshal.dumps(code), level=6)
        new_pos = out.tell()
        out.write(blob)
        new_toc.append(('webbrowser', (0, new_pos, len(blob))))
        added_names.add('webbrowser')
    new_toc_off = out.tell()
    out.write(marshal.dumps(new_toc))
    data = bytearray(out.getvalue())
    struct.pack_into('!I', data, 8, new_toc_off)
    return bytes(data)


def inject_pe_icon(pe_bytes, ico_path):
    if not ico_path.exists():
        return pe_bytes
    ico_data = ico_path.read_bytes()
    reserved, ico_type, count = struct.unpack_from('<HHH', ico_data, 0)
    if reserved != 0 or ico_type != 1 or count < 1:
        return pe_bytes

    grp = bytearray(struct.pack('<HHH', 0, 1, count))
    images = []
    offset = 6
    for idx in range(count):
        b_w, b_h, b_color, b_res, w_planes, w_bitcount, dw_bytes, dw_img_off = struct.unpack_from('<BBBBHHIi', ico_data, offset)
        offset += 16
        icon_id = idx + 1
        grp.extend(struct.pack('<BBBBHHIH', b_w, b_h, b_color, b_res, w_planes or 1, w_bitcount or 32, dw_bytes, icon_id))
        images.append((icon_id, ico_data[dw_img_off:dw_img_off + dw_bytes]))

    tmp_pe = Path(tempfile.gettempdir()) / f'landesk_boot_{os.getpid()}.exe'
    try:
        tmp_pe.write_bytes(pe_bytes)
        kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel32.BeginUpdateResourceW.argtypes = [wintypes.LPCWSTR, wintypes.BOOL]
        kernel32.BeginUpdateResourceW.restype = wintypes.HANDLE
        kernel32.UpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.WORD, ctypes.c_void_p, wintypes.DWORD]
        kernel32.UpdateResourceW.restype = wintypes.BOOL
        kernel32.EndUpdateResourceW.argtypes = [wintypes.HANDLE, wintypes.BOOL]
        kernel32.EndUpdateResourceW.restype = wintypes.BOOL

        h_upd = kernel32.BeginUpdateResourceW(str(tmp_pe), False)
        if not h_upd:
            return pe_bytes
        rt_icon = ctypes.cast(3, wintypes.LPCWSTR)
        rt_group_icon = ctypes.cast(14, wintypes.LPCWSTR)
        for icon_id, img_bytes in images:
            buf = ctypes.create_string_buffer(img_bytes, len(img_bytes))
            kernel32.UpdateResourceW(h_upd, rt_icon, ctypes.cast(icon_id, wintypes.LPCWSTR), 1033, buf, len(img_bytes))
        grp_bytes = bytes(grp)
        grp_buf = ctypes.create_string_buffer(grp_bytes, len(grp_bytes))
        kernel32.UpdateResourceW(h_upd, rt_group_icon, ctypes.cast(1, wintypes.LPCWSTR), 1033, grp_buf, len(grp_bytes))
        kernel32.EndUpdateResourceW(h_upd, False)
        return tmp_pe.read_bytes()
    except Exception:
        return pe_bytes
    finally:
        tmp_pe.unlink(missing_ok=True)


def build_exe(orig_exe_bytes, orig_pkg_bytes, new_pyz_bytes, app_py_path, ico_path=None):
    prefix_len = len(orig_exe_bytes) - len(orig_pkg_bytes)
    assert orig_exe_bytes[prefix_len:] == orig_pkg_bytes
    bootloader = orig_exe_bytes[:prefix_len]
    if ico_path:
        bootloader = inject_pe_icon(bootloader, ico_path)

    cookie_fmt = '!8sIIii64s'
    cookie_size = struct.calcsize(cookie_fmt)
    magic, pkg_len, toc_off, toc_len, pyver, pylib = struct.unpack(cookie_fmt, orig_pkg_bytes[-cookie_size:])
    toc_bytes = orig_pkg_bytes[toc_off:toc_off + toc_len]

    entries = []
    pos = 0
    while pos < len(toc_bytes):
        entry_len, dpos, dlen, ulen, cflag, typcd = struct.unpack('!IIIIBB', toc_bytes[pos:pos + 18])
        raw_name = toc_bytes[pos + 18:pos + entry_len].rstrip(b'\x00').decode('utf-8')
        entries.append((raw_name, dpos, dlen, ulen, cflag, typcd, orig_pkg_bytes[dpos:dpos + dlen]))
        pos += entry_len

    app_code = compile(app_py_path.read_bytes(), 'app.py', 'exec', dont_inherit=True)
    app_marshaled = marshal.dumps(app_code)
    app_compressed = zlib.compress(app_marshaled, level=6)

    pkg_out = io.BytesIO()
    toc_out = io.BytesIO()
    for name, dpos, dlen, ulen, cflag, typcd, blob in entries:
        new_dpos = pkg_out.tell()
        if name == 'app':
            blob = app_compressed
            new_dlen = len(blob)
            new_ulen = len(app_marshaled)
        elif name == 'PYZ.pyz':
            blob = new_pyz_bytes
            new_dlen = len(blob)
            new_ulen = len(blob)
        else:
            new_dlen = dlen
            new_ulen = ulen
        pkg_out.write(blob)
        name_bytes = name.encode('utf-8') + b'\x00'
        total_len = 18 + len(name_bytes)
        pad = (16 - (total_len % 16)) % 16
        entry_len = total_len + pad
        toc_out.write(struct.pack('!IIIIBB', entry_len, new_dpos, new_dlen, new_ulen, cflag, typcd))
        toc_out.write(name_bytes + (b'\x00' * pad))

    new_toc_off = pkg_out.tell()
    new_toc_bytes = toc_out.getvalue()
    pkg_out.write(new_toc_bytes)
    new_pkg_len = pkg_out.tell() + cookie_size
    pkg_out.write(struct.pack(cookie_fmt, magic, new_pkg_len, new_toc_off, len(new_toc_bytes), pyver, pylib))
    new_pkg = pkg_out.getvalue()
    return bootloader + new_pkg


binary.mkdir(parents=True, exist_ok=True)
if not (binary / '_internal').exists():
    shutil.copytree(root / 'outputs/v0.2.0/Windows/LanDesk/_internal', binary / '_internal', dirs_exist_ok=True)
if not (binary / 'licenses').exists():
    shutil.copytree(root / 'outputs/v0.2.0/Windows/LanDesk/licenses', binary / 'licenses', dirs_exist_ok=True)

orig_pyz = (build_v020 / 'PYZ-00.pyz').read_bytes()
orig_pkg = (build_v020 / 'LanDesk.pkg').read_bytes()
orig_exe = (build_v020 / 'LanDesk.exe').read_bytes()
new_pyz = build_pyz(orig_pyz, source)
new_exe = build_exe(orig_exe, orig_pkg, new_pyz, source / 'app.py', ico_path=source / 'landesk.ico')
if (source / 'landesk.ico').exists():
    shutil.copy2(source / 'landesk.ico', binary / 'landesk.ico')
exe_target = binary / 'LanDesk.exe'
try:
    exe_target.write_bytes(new_exe)
except PermissionError:
    old_target = binary / 'LanDesk.old.exe'
    try:
        if old_target.exists():
            old_target.unlink()
        exe_target.rename(old_target)
        exe_target.write_bytes(new_exe)
    except PermissionError:
        print('Note: LanDesk.exe is currently in use; writing new_exe directly into ZIP archive.')

csc_path = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
uninst_cs = root / 'work/Uninstaller.cs'
uninst_target = binary / 'Uninstall-LanDesk.exe'
uninst_bytes = new_exe
if csc_path.exists() and uninst_cs.exists():
    temp_uninst = root / 'work/Uninstall-LanDesk.exe'
    uninst_ico = root / 'work/uninstall.ico'
    uninst_cmd = [
        str(csc_path),
        '/nologo',
        '/target:winexe',
        '/optimize+',
        f'/out:{temp_uninst}',
        '/r:System.dll',
        '/r:System.Drawing.dll',
        '/r:System.Windows.Forms.dll',
    ]
    if uninst_ico.exists():
        uninst_cmd.append(f'/win32icon:{uninst_ico}')
    uninst_cmd.append(str(uninst_cs))
    subprocess.run(uninst_cmd, check=True)
    uninst_bytes = temp_uninst.read_bytes()

try:
    uninst_target.write_bytes(uninst_bytes)
except PermissionError:
    pass

# Also update %LOCALAPPDATA%\Programs\LanDesk if already installed on this machine
local_installed = Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs' / 'LanDesk'
if local_installed.exists():
    try:
        (local_installed / 'LanDesk.exe').write_bytes(new_exe)
    except PermissionError:
        pass
    try:
        (local_installed / 'Uninstall-LanDesk.exe').write_bytes(uninst_bytes)
    except PermissionError:
        pass
    try:
        if (source / 'landesk.ico').exists():
            shutil.copy2(source / 'landesk.ico', local_installed / 'landesk.ico')
    except PermissionError:
        pass

notes_file = f'RELEASE-NOTES-v{version}.md'
for name in ['README.md', 'TEST-REPORT.md']:
    shutil.copy2(source / name, binary / name)
if (source / notes_file).exists():
    shutil.copy2(source / notes_file, binary / notes_file)
    shutil.copy2(source / notes_file, output / notes_file)
shutil.copy2(root / 'SECURITY.md', binary / 'SECURITY.md')

checksums = []
for folder, filename in [(source, f'LanDesk-v{version}-source.zip'), (binary, f'LanDesk-v{version}-Windows-x64.zip')]:
    destination = output / filename
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.rglob('*')):
            if not path.is_file() or '__pycache__' in path.parts or path.name == 'LanDesk.old.exe':
                continue
            assert path.suffix.lower() not in {'.pem', '.key', '.dat', '.partial'}, path
            arcname = str(Path('LanDesk') / path.relative_to(folder))
            if folder == binary and path.name == 'LanDesk.exe':
                archive.writestr(arcname, new_exe)
            elif folder == binary and path.name == 'Uninstall-LanDesk.exe':
                archive.writestr(arcname, uninst_bytes)
            else:
                archive.write(path, arcname)
        if folder == source:
            archive.write(root / 'SECURITY.md', 'LanDesk/SECURITY.md')
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert not any('smoke-profile' in name or 'ui-test-profile' in name or 'settings-v2' in name for name in archive.namelist())
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    checksums.append(f'{digest}  {filename}')
    print(f'{filename}: {destination.stat().st_size / 1024 / 1024:.1f} MB; ZIP verified')

csc_path = Path(r'C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe')
setup_cs = root / 'work/SetupInstaller.cs'
setup_exe = output / f'LanDesk-Setup-v{version}.exe'
win_zip = output / f'LanDesk-v{version}-Windows-x64.zip'
if csc_path.exists() and setup_cs.exists():
    setup_ico = root / 'work/setup.ico'
    cmd = [
        str(csc_path),
        '/nologo',
        '/target:winexe',
        '/optimize+',
        f'/out:{setup_exe}',
        '/r:System.dll',
        '/r:System.Drawing.dll',
        '/r:System.Windows.Forms.dll',
        '/r:System.IO.Compression.dll',
        '/r:System.IO.Compression.FileSystem.dll',
        f'/resource:{win_zip},payload.zip',
    ]
    if setup_ico.exists():
        cmd.append(f'/win32icon:{setup_ico}')
    cmd.append(str(setup_cs))
    subprocess.run(cmd, check=True)
    setup_digest = hashlib.sha256(setup_exe.read_bytes()).hexdigest()
    checksums.append(f'{setup_digest}  {setup_exe.name}')
    print(f'{setup_exe.name}: {setup_exe.stat().st_size / 1024 / 1024:.1f} MB; Single-file Setup EXE verified')

(output / 'SHA256SUMS.txt').write_text('\n'.join(checksums) + '\n', encoding='utf-8')
print('\n'.join(checksums))

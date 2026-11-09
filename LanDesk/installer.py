"""Built-in Windows Installer and Uninstaller for LanDesk."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import winreg

from security import VERSION, set_startup_enabled

UNINSTALL_KEY = r'Software\Microsoft\Windows\CurrentVersion\Uninstall\LanDesk'


def default_install_dir():
    local_app_data = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData/Local')
    return Path(local_app_data) / 'Programs' / 'LanDesk'


def current_app_dir():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_installed_dir():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ) as key:
            loc, _ = winreg.QueryValueEx(key, 'InstallLocation')
            if loc and Path(loc).exists():
                return Path(loc).resolve()
    except OSError:
        pass
    default_dir = default_install_dir()
    if (default_dir / 'LanDesk.exe').exists():
        return default_dir.resolve()
    app_dir = current_app_dir()
    if (app_dir / 'LanDesk.exe').exists():
        return app_dir.resolve()
    return default_dir.resolve()


def is_installed():
    target = default_install_dir() / 'LanDesk.exe'
    if target.exists():
        return True
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY, 0, winreg.KEY_READ):
            return True
    except OSError:
        return False


def create_shortcut(shortcut_path, target_path, arguments='', description='LanDesk รีโมทภายในองค์กร', working_dir=None, icon_path=None):
    shortcut_path = Path(shortcut_path)
    try:
        shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    work_dir = str(Path(working_dir or Path(target_path).parent).resolve())
    target_str = str(Path(target_path).resolve())
    icon_str = str(Path(icon_path or target_path).resolve())
    vbs_content = f'''Set oWS = WScript.CreateObject("WScript.Shell")
Set oLink = oWS.CreateShortcut("{shortcut_path}")
oLink.TargetPath = "{target_str}"
oLink.Arguments = "{arguments}"
oLink.WorkingDirectory = "{work_dir}"
oLink.Description = "{description}"
oLink.IconLocation = "{icon_str},0"
oLink.Save
'''
    vbs_file = Path(tempfile.gettempdir()) / f'landesk_sc_{int(time.time() * 1000)}.vbs'
    try:
        vbs_file.write_text(vbs_content, encoding='utf-16')
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        subprocess.run(
            ['wscript.exe', '//B', '//Nologo', str(vbs_file)],
            startupinfo=startupinfo,
            cwd=tempfile.gettempdir(),
            check=False,
            timeout=10,
        )
    except Exception:
        pass
    finally:
        try:
            vbs_file.unlink(missing_ok=True)
        except OSError:
            pass


def register_uninstaller(install_dir):
    install_dir = Path(install_dir).resolve()
    exe_path = install_dir / 'LanDesk.exe'
    uninst_exe = install_dir / 'Uninstall-LanDesk.exe'
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            winreg.SetValueEx(key, 'DisplayName', 0, winreg.REG_SZ, f'LanDesk {VERSION} (รีโมทภายในองค์กร)')
            winreg.SetValueEx(key, 'DisplayVersion', 0, winreg.REG_SZ, VERSION)
            winreg.SetValueEx(key, 'Publisher', 0, winreg.REG_SZ, 'LanDesk Intranet Remote')
            winreg.SetValueEx(key, 'DisplayIcon', 0, winreg.REG_SZ, str(exe_path))
            winreg.SetValueEx(key, 'InstallLocation', 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, 'UninstallString', 0, winreg.REG_SZ, f'"{uninst_exe}"')
            winreg.SetValueEx(key, 'QuietUninstallString', 0, winreg.REG_SZ, f'"{uninst_exe}" --silent')
            winreg.SetValueEx(key, 'NoModify', 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, 'NoRepair', 0, winreg.REG_DWORD, 1)
    except OSError:
        pass


def unregister_uninstaller():
    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY)
    except OSError:
        pass


def install_landesk(source_dir=None, install_dir=None, desktop_shortcut=True, start_menu_shortcut=True):
    src = Path(source_dir or current_app_dir()).resolve()
    dst = Path(install_dir or default_install_dir()).resolve()
    dst.mkdir(parents=True, exist_ok=True)

    excluded_suffixes = {'.dat', '.pem', '.key', '.partial', '.jsonl', '.bak'}
    if src != dst:
        for item in src.iterdir():
            if item.name in ('__pycache__', 'smoke-profile', 'ui-test-profile', 'Received', 'LanDesk.old.exe'):
                continue
            if item.is_file() and item.suffix.lower() in excluded_suffixes:
                continue
            target_item = dst / item.name
            if item.is_dir():
                shutil.copytree(item, target_item, dirs_exist_ok=True)
            else:
                try:
                    shutil.copy2(item, target_item)
                except PermissionError:
                    pass

    main_exe = dst / 'LanDesk.exe'
    uninst_exe = dst / 'Uninstall-LanDesk.exe'
    src_uninst = src / 'Uninstall-LanDesk.exe'
    if src_uninst.exists() and src_uninst != uninst_exe:
        try:
            shutil.copy2(src_uninst, uninst_exe)
        except PermissionError:
            pass
    elif main_exe.exists() and not uninst_exe.exists():
        try:
            shutil.copy2(main_exe, uninst_exe)
        except PermissionError:
            pass

    register_uninstaller(dst)
    ico_file = dst / 'landesk.ico'
    icon_arg = ico_file if ico_file.exists() else main_exe

    if desktop_shortcut and main_exe.exists():
        desktop_dir = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop'
        create_shortcut(desktop_dir / 'LanDesk.lnk', main_exe, description=f'LanDesk {VERSION} รีโมทภายในองค์กร', icon_path=icon_arg)

    if start_menu_shortcut and main_exe.exists():
        programs_dir = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Microsoft/Windows/Start Menu/Programs/LanDesk'
        create_shortcut(programs_dir / 'LanDesk.lnk', main_exe, description=f'LanDesk {VERSION} รีโมทภายในองค์กร', icon_path=icon_arg)
        if uninst_exe.exists():
            create_shortcut(
                programs_dir / 'Uninstall LanDesk (ถอนการติดตั้ง).lnk',
                uninst_exe,
                description='ถอนการติดตั้ง LanDesk',
                icon_path=uninst_exe,
            )
    return dst


def remove_shortcuts():
    desktop_lnk = Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop' / 'LanDesk.lnk'
    try:
        desktop_lnk.unlink(missing_ok=True)
    except OSError:
        pass
    programs_dir = Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Microsoft/Windows/Start Menu/Programs/LanDesk'
    try:
        if programs_dir.exists():
            shutil.rmtree(programs_dir, ignore_errors=True)
    except OSError:
        pass


def uninstall_landesk(install_dir=None, remove_user_data=False):
    target_dir = Path(install_dir).resolve() if install_dir else get_installed_dir()

    try:
        set_startup_enabled(False)
    except Exception:
        pass
    unregister_uninstaller()
    remove_shortcuts()

    data_dir = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData/Local'))) / 'LanDesk'
    if remove_user_data:
        try:
            if data_dir.exists():
                shutil.rmtree(data_dir, ignore_errors=True)
        except OSError:
            pass

    # Spawn a background cleanup script from %TEMP% (never from inside target_dir!)
    # Use ping instead of timeout.exe because timeout.exe fails when stdin is redirected in a GUI process.
    temp_dir = Path(tempfile.gettempdir())
    script_path = temp_dir / f'landesk_uninstall_{int(time.time() * 1000)}.cmd'
    target_str = str(target_dir)
    desktop_lnk_str = str(Path(os.environ.get('USERPROFILE', str(Path.home()))) / 'Desktop' / 'LanDesk.lnk')
    start_menu_str = str(Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'Microsoft/Windows/Start Menu/Programs/LanDesk')
    purge_line = f'rmdir /s /q "{data_dir}" 2>nul' if remove_user_data else ''

    content = f"""@echo off
setlocal
cd /d "%TEMP%"
ping 127.0.0.1 -n 2 > nul
taskkill /f /im LanDesk.exe > nul 2>&1
taskkill /f /im LanDesk.old.exe > nul 2>&1
ping 127.0.0.1 -n 2 > nul
del /f /q "{desktop_lnk_str}" 2>nul
rmdir /s /q "{start_menu_str}" 2>nul
{purge_line}
rmdir /s /q "{target_str}" 2>nul
if exist "{target_str}" (
    ping 127.0.0.1 -n 2 > nul
    rmdir /s /q "{target_str}" 2>nul
)
(goto) 2>nul & del "%~f0"
"""
    script_path.write_text(content, encoding='utf-8')
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    subprocess.Popen(
        ['cmd.exe', '/c', str(script_path)],
        startupinfo=startupinfo,
        cwd=str(temp_dir),
        close_fds=True,
    )
    return True


def show_installer_dialog(parent=None, on_quit=None):
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk
    from tray_win import apply_window_icon

    win = tk.Toplevel(parent) if parent else tk.Tk()
    apply_window_icon(win)
    win.title(f'ติดตั้ง / ถอนการติดตั้ง LanDesk v{VERSION}')
    win.geometry('590x450')
    win.resizable(False, False)
    if parent:
        win.transient(parent)
        win.grab_set()

    frame = ttk.Frame(win, padding=22)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text=f'ติดตั้ง LanDesk v{VERSION} พร้อมตัวถอนการติดตั้ง', font=('Leelawadee UI', 15, 'bold'), foreground='#00695c').pack(anchor='w')
    ttk.Label(
        frame,
        text='เมื่อกดติดตั้ง ระบบจะคัดลอกโปรแกรมพร้อมสร้างตัวถอนการติดตั้ง (Uninstall-LanDesk.exe)\nสร้างไอคอนบนหน้าจอ Desktop / Start Menu และลงทะเบียนใน Windows Settings -> Apps ให้อัตโนมัติ',
        wraplength=540,
    ).pack(anchor='w', pady=(6, 14))

    dir_box = ttk.LabelFrame(frame, text=' โฟลเดอร์สำหรับติดตั้ง (รวมตัวถอนการติดตั้ง Uninstall-LanDesk.exe) ', padding=10)
    dir_box.pack(fill='x', pady=(0, 12))
    dir_var = tk.StringVar(value=str(default_install_dir()))
    dir_row = ttk.Frame(dir_box)
    dir_row.pack(fill='x')
    ttk.Entry(dir_row, textvariable=dir_var).pack(side='left', fill='x', expand=True)

    def browse_dir():
        chosen = filedialog.askdirectory(parent=win, initialdir=dir_var.get())
        if chosen:
            dir_var.set(str(Path(chosen) / 'LanDesk') if not chosen.lower().endswith('landesk') else chosen)

    ttk.Button(dir_row, text='เลือกโฟลเดอร์…', command=browse_dir).pack(side='right', padx=(8, 0))

    opt_box = ttk.LabelFrame(frame, text=' ตัวเลือกการติดตั้ง ', padding=10)
    opt_box.pack(fill='x', pady=(0, 14))
    desktop_var = tk.BooleanVar(value=True)
    startmenu_var = tk.BooleanVar(value=True)
    startup_var = tk.BooleanVar(value=True)
    ttk.Checkbutton(opt_box, text='สร้างไอคอนทางลัดบนหน้าจอ Desktop (Desktop Shortcut)', variable=desktop_var).pack(anchor='w', pady=2)
    ttk.Checkbutton(opt_box, text='สร้างทางลัดในเมนู Start Menu พร้อมตัวถอนการติดตั้ง (Uninstall LanDesk)', variable=startmenu_var).pack(anchor='w', pady=2)
    ttk.Checkbutton(opt_box, text='รันเซอร์วิสพร้อมรับการเชื่อมต่ออัตโนมัติเมื่อเปิดเครื่อง (เหมือน AnyDesk/VNC)', variable=startup_var).pack(anchor='w', pady=2)

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill='x', side='bottom')

    def do_install():
        try:
            dst = install_landesk(
                install_dir=Path(dir_var.get().strip()),
                desktop_shortcut=desktop_var.get(),
                start_menu_shortcut=startmenu_var.get(),
            )
            if startup_var.get() and (dst / 'LanDesk.exe').exists():
                set_startup_enabled(True, f'"{dst / "LanDesk.exe"}" --background')
            else:
                set_startup_enabled(False)
            messagebox.showinfo(
                'ติดตั้งเสร็จสมบูรณ์',
                f'ติดตั้ง LanDesk v{VERSION} เรียบร้อยแล้วที่:\n{dst}\n\n• มาพร้อมตัวถอนการติดตั้ง: {dst / "Uninstall-LanDesk.exe"}\n• สร้างไอคอนบน Desktop และ Start Menu แล้ว\n• ถอนการติดตั้งได้ทั้งจากปุ่มในโปรแกรม, ไฟล์ Uninstall-LanDesk.exe หรือใน Windows Settings -> Apps',
                parent=win,
            )
            win.destroy()
        except Exception as err:
            messagebox.showerror('ติดตั้งไม่สำเร็จ', str(err), parent=win)

    def do_uninstall_from_here():
        win.destroy()
        show_uninstaller_dialog(parent=parent, on_quit=on_quit)

    ttk.Button(btn_row, text='ติดตั้งลงเครื่องทันที', style='Accent.TButton', command=do_install).pack(side='left')
    ttk.Button(btn_row, text='ถอนการติดตั้ง (Uninstall)…', command=do_uninstall_from_here).pack(side='left', padx=8)
    ttk.Button(btn_row, text='ปิด', command=win.destroy).pack(side='right')
    return win


def show_uninstaller_dialog(parent=None, on_quit=None):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tray_win import apply_window_icon

    target_dir = get_installed_dir()
    win = tk.Toplevel(parent) if parent else tk.Tk()
    apply_window_icon(win)
    win.title('ถอนการติดตั้ง LanDesk (Uninstall)')
    win.geometry('540x320')
    win.resizable(False, False)
    win.attributes('-topmost', True)
    if parent:
        win.transient(parent)
        win.grab_set()

    frame = ttk.Frame(win, padding=22)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='ถอนการติดตั้ง LanDesk (Uninstall)', font=('Leelawadee UI', 16, 'bold'), foreground='#b71c1c').pack(anchor='w')
    ttk.Label(
        frame,
        text=f'คุณต้องการลบโปรแกรม LanDesk ทางลัดบนหน้าจอ Desktop และ Start Menu ออกจากเครื่องนี้หรือไม่?\nโฟลเดอร์ที่จะถูกลบ: {target_dir}',
        wraplength=490,
    ).pack(anchor='w', pady=(8, 14))

    remove_data_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(
        frame,
        text='ลบข้อมูลการตั้งค่า สมุดรายชื่อ และไฟล์ที่เคยรับทั้งหมดด้วย (%LOCALAPPDATA%\\LanDesk)',
        variable=remove_data_var,
    ).pack(anchor='w', pady=(0, 18))

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill='x', side='bottom')

    def confirm_uninstall():
        purge = bool(remove_data_var.get())
        try:
            set_startup_enabled(False)
        except Exception:
            pass
        unregister_uninstaller()
        remove_shortcuts()
        messagebox.showinfo(
            'ถอนการติดตั้งสำเร็จ',
            f'ถอนการติดตั้ง LanDesk เรียบร้อยแล้ว!\nเมื่อกด OK โปรแกรมจะปิดตัวลงและลบโฟลเดอร์ {target_dir} ออกจากเครื่องโดยอัตโนมัติครับ',
            parent=win,
        )
        try:
            uninstall_landesk(install_dir=target_dir, remove_user_data=purge)
        except Exception:
            pass
        os._exit(0)

    ttk.Button(btn_row, text='ยืนยันถอนการติดตั้งทันที (Uninstall)', command=confirm_uninstall).pack(side='left')
    ttk.Button(btn_row, text='ยกเลิก', command=win.destroy).pack(side='right')
    return win

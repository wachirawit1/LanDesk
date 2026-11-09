"""GitHub release checker and in-place updater for LanDesk."""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

DEFAULT_GITHUB_REPO = 'owner/landesk'  # Can be customized in settings


def clean_repo_slug(value):
    if not isinstance(value, str):
        return ''
    val = value.strip()
    if val.startswith('https://github.com/'):
        val = val[len('https://github.com/'):]
    elif val.startswith('http://github.com/'):
        val = val[len('http://github.com/'):]
    elif val.startswith('github.com/'):
        val = val[len('github.com/'):]
    val = val.strip().strip('/')
    parts = val.split('/')
    if len(parts) >= 2 and parts[0] and parts[1]:
        return f'{parts[0]}/{parts[1]}'
    return val


def parse_version(ver_str):
    if not isinstance(ver_str, str):
        return (0, 0, 0)
    match = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', ver_str.strip())
    if not match:
        return (0, 0, 0)
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3)) if match.group(3) else 0
    return (major, minor, patch)


def is_newer_version(latest_str, current_str):
    return parse_version(latest_str) > parse_version(current_str)


def fetch_latest_release(repo_slug, timeout=8, opener=None):
    slug = clean_repo_slug(repo_slug)
    if not slug or '/' not in slug or slug == 'owner/landesk':
        raise ValueError('กรุณาระบุที่อยู่ GitHub Repository เช่น your-name/landesk ในหน้าตั้งค่า')
    url = f'https://api.github.com/repos/{slug}/releases/latest'
    req = urllib.request.Request(
        url,
        headers={
            'User-Agent': 'LanDesk-Updater',
            'Accept': 'application/vnd.github.v3+json',
        }
    )
    req_opener = opener or urllib.request.urlopen
    with req_opener(req, timeout=timeout) as resp:
        charset = resp.headers.get_content_charset() or 'utf-8'
        data = json.loads(resp.read().decode(charset))

    tag = data.get('tag_name', '')
    title = data.get('name') or tag
    body = data.get('body') or 'ไม่มีรายละเอียดการเปลี่ยนแปลง'
    html_url = data.get('html_url') or f'https://github.com/{slug}/releases'
    assets = data.get('assets', [])

    download_url = None
    asset_name = None
    asset_size = 0
    for asset in assets:
        name = asset.get('name', '')
        if name.endswith('.zip') and ('windows' in name.lower() or 'x64' in name.lower()):
            download_url = asset.get('browser_download_url')
            asset_name = name
            asset_size = asset.get('size', 0)
            break
    if not download_url and assets:
        for asset in assets:
            name = asset.get('name', '')
            if name.endswith('.zip') or (name.lower().endswith('.exe') and 'setup' in name.lower()):
                download_url = asset.get('browser_download_url')
                asset_name = name
                asset_size = asset.get('size', 0)
                break

    return {
        'tag': tag,
        'version': tag.lstrip('vV'),
        'title': title,
        'body': body,
        'html_url': html_url,
        'download_url': download_url,
        'asset_name': asset_name or f'LanDesk-{tag}-Windows-x64.zip',
        'asset_size': asset_size,
    }


def download_file(url, target_path, progress_callback=None, cancel_event=None, chunk_size=128 * 1024):
    target_path = Path(target_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={'User-Agent': 'LanDesk-Updater'})
    temp_target = target_path.with_suffix('.part')
    try:
        with urllib.request.urlopen(req, timeout=30) as response, temp_target.open('wb') as out_file:
            total_size = int(response.headers.get('Content-Length') or 0)
            downloaded = 0
            while True:
                if cancel_event and cancel_event.is_set():
                    raise InterruptedError('ยกเลิกการดาวน์โหลด')
                chunk = response.read(chunk_size)
                if not chunk:
                    break
                out_file.write(chunk)
                downloaded += len(chunk)
                if progress_callback:
                    pct = (downloaded * 100 // total_size) if total_size > 0 else 0
                    progress_callback(pct, downloaded, total_size)
        if temp_target.stat().st_size == 0:
            raise ValueError('ไฟล์ที่ดาวน์โหลดว่างเปล่า')
        if target_path.exists():
            target_path.unlink()
        temp_target.rename(target_path)
        return target_path
    finally:
        if temp_target.exists():
            temp_target.unlink(missing_ok=True)


def extract_update_zip(zip_path, staging_dir):
    staging = Path(staging_dir)
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, 'r') as archive:
        if archive.testzip() is not None:
            raise ValueError('ไฟล์ ZIP ที่ดาวน์โหลดไม่สมบูรณ์หรือเสียหาย')
        archive.extractall(staging)
    extracted_items = list(staging.iterdir())
    if len(extracted_items) == 1 and extracted_items[0].is_dir() and (extracted_items[0] / 'LanDesk.exe').exists():
        return extracted_items[0]
    return staging


def create_restart_script(staging_folder, app_folder, target_exe='LanDesk.exe'):
    script_path = Path(tempfile.gettempdir()) / f'landesk_update_{int(time.time())}.cmd'
    staging_str = str(Path(staging_folder).resolve())
    app_str = str(Path(app_folder).resolve())
    exe_name = target_exe

    # Batch script waits for the old LanDesk process to exit, copies files over, launches new exe, and cleans up
    content = f"""@echo off
setlocal
cd /d "%TEMP%"
ping 127.0.0.1 -n 3 > nul
robocopy "{staging_str}" "{app_str}" /E /IS /IT /NP /NJH /NJS
cd /d "{app_str}"
start "" "{app_str}\\{exe_name}"
cd /d "%TEMP%"
ping 127.0.0.1 -n 2 > nul
rmdir /s /q "{staging_str}" 2>nul
(goto) 2>nul & del "%~f0"
"""
    script_path.write_text(content, encoding='ansi')
    return script_path


def apply_update_and_restart(package_path, app_dir, target_exe='LanDesk.exe'):
    package_path = Path(package_path)
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    if package_path.suffix.lower() == '.exe':
        subprocess.Popen([str(package_path), '--silent', '--launch', f'--dir={Path(app_dir).resolve()}'], startupinfo=startupinfo, cwd=tempfile.gettempdir(), close_fds=True)
        return True
    staging_root = Path(tempfile.gettempdir()) / f'landesk_stage_{int(time.time())}'
    extracted_source = extract_update_zip(package_path, staging_root)
    script_file = create_restart_script(extracted_source, app_dir, target_exe)
    subprocess.Popen(['cmd.exe', '/c', str(script_file)], startupinfo=startupinfo, cwd=tempfile.gettempdir(), close_fds=True)
    return True


def show_update_dialog(root, release, current_version, on_quit=None):
    import tkinter as tk
    from tkinter import messagebox, ttk
    from tray_win import apply_window_icon

    dialog = tk.Toplevel(root)
    apply_window_icon(dialog)
    dialog.title('LanDesk · พบเวอร์ชันใหม่พร้อมอัปเดต')
    dialog.geometry('640x530')
    dialog.minsize(560, 480)
    dialog.transient(root)
    dialog.attributes('-topmost', True)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=20)
    frame.pack(fill='both', expand=True)

    header = ttk.Frame(frame)
    header.pack(fill='x')
    ttk.Label(header, text='🚀 พบเวอร์ชันใหม่! คุณต้องการอัปเดตเลยหรือไม่?', font=('Leelawadee UI', 15, 'bold'), foreground='#00695c').pack(anchor='w')

    ver_text = f"เวอร์ชันปัจจุบัน: v{current_version}   ➔   เวอร์ชันใหม่: {release.get('tag', '')}"
    ttk.Label(header, text=ver_text, font=('Leelawadee UI', 11, 'bold')).pack(anchor='w', pady=(4, 2))
    ttk.Label(header, text=f"หัวข้อ: {release.get('title', '')}", font=('Leelawadee UI', 10)).pack(anchor='w', pady=(0, 10))

    ttk.Label(frame, text='บันทึกการเปลี่ยนแปลง (What’s New):', font=('Leelawadee UI', 10, 'bold')).pack(anchor='w')

    notes_frame = ttk.Frame(frame)
    notes_frame.pack(fill='both', expand=True, pady=(4, 10))
    notes_text = tk.Text(notes_frame, wrap='word', height=8, font=('Leelawadee UI', 10), bg='#ffffff', fg='#172d40')
    notes_scroll = ttk.Scrollbar(notes_frame, orient='vertical', command=notes_text.yview)
    notes_text.configure(yscrollcommand=notes_scroll.set)
    notes_text.pack(side='left', fill='both', expand=True)
    notes_scroll.pack(side='right', fill='y')
    notes_text.insert('1.0', release.get('body', ''))
    notes_text.configure(state='disabled')

    status_var = tk.StringVar()
    size_mb = (release.get('asset_size', 0) / (1024 * 1024))
    if size_mb > 0:
        status_var.set(f"พร้อมดาวน์โหลด ({release.get('asset_name', '')} · {size_mb:.1f} MB)")
    else:
        status_var.set('พร้อมดาวน์โหลดอัปเดต')

    progress_var = tk.DoubleVar(value=0)
    prog_bar = ttk.Progressbar(frame, variable=progress_var, maximum=100)
    prog_bar.pack(fill='x', pady=(0, 4))

    ttk.Label(frame, textvariable=status_var, font=('Leelawadee UI', 9)).pack(anchor='w', pady=(0, 12))

    cancel_event = threading.Event()

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill='x')

    def open_browser():
        url = release.get('html_url') or 'https://github.com'
        if hasattr(os, 'startfile'):
            os.startfile(url)
        else:
            import webbrowser
            webbrowser.open(url)

    def start_auto_update():
        d_url = release.get('download_url')
        if not d_url:
            messagebox.showinfo('เปิดหน้าดาวน์โหลด', 'ไม่พบไฟล์ติดตั้งอัตโนมัติใน Release นี้ กำลังเปิดหน้าเว็บดาวน์โหลดให้แทนครับ', parent=dialog)
            open_browser()
            return

        update_btn.configure(state='disabled')
        browser_btn.configure(state='disabled')
        cancel_btn.configure(text='ยกเลิกดาวน์โหลด', command=lambda: cancel_event.set())

        def worker():
            ext = '.exe' if str(release.get('asset_name', '')).lower().endswith('.exe') else '.zip'
            temp_pkg = Path(tempfile.gettempdir()) / f"landesk_update_{release.get('tag', 'new')}{ext}"
            def on_progress(pct, downloaded, total):
                if not dialog.winfo_exists():
                    return
                dialog.after(0, lambda: progress_var.set(pct))
                d_mb = downloaded / (1024 * 1024)
                t_mb = total / (1024 * 1024)
                dialog.after(0, lambda: status_var.set(f"กำลังดาวน์โหลด {pct}% ({d_mb:.1f} MB / {t_mb:.1f} MB)..."))

            try:
                download_file(d_url, temp_pkg, progress_callback=on_progress, cancel_event=cancel_event)
                dialog.after(0, lambda: status_var.set('ดาวน์โหลดสำเร็จ! กำลังติดตั้งและเตรียมรีสตาร์ท...'))
                time.sleep(0.5)

                if getattr(sys, 'frozen', False):
                    app_dir = Path(sys.executable).parent
                    apply_update_and_restart(temp_pkg, app_dir, Path(sys.executable).name)
                else:
                    dialog.after(0, lambda: messagebox.showinfo('ดาวน์โหลดสำเร็จ', f'ดาวน์โหลดชุดอัปเดตเรียบร้อยแล้วที่:\n{temp_pkg}\n\nเนื่องจากกำลังรันในโหมด Python Source ให้แตกไฟล์และรันเวอร์ชันใหม่ได้เลยครับ', parent=dialog))
                    dialog.destroy()
                    return

                if on_quit:
                    dialog.after(100, on_quit)
                else:
                    dialog.after(100, dialog.destroy)
            except InterruptedError:
                dialog.after(0, lambda: status_var.set('ยกเลิกการดาวน์โหลดแล้ว'))
                dialog.after(0, lambda: update_btn.configure(state='normal'))
                dialog.after(0, lambda: browser_btn.configure(state='normal'))
                dialog.after(0, lambda: cancel_btn.configure(text='ไว้คราวหน้า', command=dialog.destroy))
            except Exception as err:
                dialog.after(0, lambda: messagebox.showerror('อัปเดตไม่สำเร็จ', f'เกิดข้อผิดพลาดระหว่างอัปเดต:\n{err}', parent=dialog))
                dialog.after(0, lambda: status_var.set(f'เกิดข้อผิดพลาด: {err}'))
                dialog.after(0, lambda: update_btn.configure(state='normal'))
                dialog.after(0, lambda: browser_btn.configure(state='normal'))
                dialog.after(0, lambda: cancel_btn.configure(text='ไว้คราวหน้า', command=dialog.destroy))

        threading.Thread(target=worker, daemon=True).start()

    update_btn = ttk.Button(btn_row, text='⚡ อัปเดตและรีสตาร์ททันที', style='Accent.TButton', command=start_auto_update)
    update_btn.pack(side='left', padx=(0, 6))

    browser_btn = ttk.Button(btn_row, text='🌐 เปิดหน้า GitHub', command=open_browser)
    browser_btn.pack(side='left', padx=(0, 6))

    cancel_btn = ttk.Button(btn_row, text='ไว้คราวหน้า', command=dialog.destroy)
    cancel_btn.pack(side='right')
    return dialog


def show_repo_prompt_dialog(root, settings, current_version, on_check_now, on_quit=None):
    import tkinter as tk
    from tkinter import messagebox, ttk

    dialog = tk.Toplevel(root)
    dialog.title('LanDesk · ตั้งค่าระบบเช็คอัปเดต GitHub')
    dialog.geometry('560x310')
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=20)
    frame.pack(fill='both', expand=True)

    ttk.Label(frame, text='ตั้งค่าการตรวจหาอัปเดตอัตโนมัติ (GitHub Releases)', font=('Leelawadee UI', 13, 'bold'), foreground='#00695c').pack(anchor='w')
    ttk.Label(
        frame,
        text='ระบุชื่อ GitHub Repository ที่ใช้เก็บไฟล์ Release ของ LanDesk (เช่น your-name/LanDesk)\nเมื่อมีเวอร์ชันใหม่ โปรแกรมจะเด้งป๊อปอัปถามให้อัปเดตอัตโนมัติเหมือน FanControl',
        wraplength=510,
    ).pack(anchor='w', pady=(6, 12))

    cfg = settings.snapshot()
    repo_var = tk.StringVar(value=cfg.get('github_repo', ''))
    auto_var = tk.BooleanVar(value=cfg.get('auto_check_update', True))

    ttk.Label(frame, text='GitHub Repository (owner/repo หรือ URL เต็ม):').pack(anchor='w')
    entry = ttk.Entry(frame, textvariable=repo_var, font=('Consolas', 11))
    entry.pack(fill='x', pady=(4, 10))
    entry.focus_set()

    ttk.Checkbutton(frame, text='เช็คเวอร์ชันใหม่อัตโนมัติทุกครั้งที่เปิดโปรแกรมแล้วป๊อปอัปถาม', variable=auto_var).pack(anchor='w', pady=(0, 14))

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill='x', side='bottom')

    def save_and_check():
        slug = clean_repo_slug(repo_var.get())
        if not slug or '/' not in slug:
            messagebox.showwarning('ระบุชื่อ Repository', 'กรุณากรอกในรูปแบบ owner/repo เช่น my-org/landesk', parent=dialog)
            return
        settings.save(github_repo=slug, auto_check_update=bool(auto_var.get()))
        dialog.destroy()
        on_check_now()

    def preview_popup():
        dialog.destroy()
        demo_rel = {
            'tag': 'v0.4.0',
            'version': '0.4.0',
            'title': 'LanDesk v0.4.0 — อัปเดตฟีเจอร์และการเชื่อมต่อ',
            'body': '• รองรับการคลิกขวา Copy ไฟล์จากเครื่องเรา แล้วไปคลิกขวา Paste (วาง) ที่เครื่องปลายทางได้ทันที\n• ระบบเช็คอัปเดตอัตโนมัติผ่าน GitHub Releases พร้อมป๊อปอัปถามและอัปเดตในคลิกเดียว\n• ตัวติดตั้ง (Installer) และตัวถอนการติดตั้ง (Uninstaller) แบบครบวงจร',
            'html_url': 'https://github.com/' + (clean_repo_slug(repo_var.get()) or 'owner/landesk') + '/releases',
            'download_url': None,
            'asset_name': 'LanDesk-v0.4.0-Windows-x64.zip',
            'asset_size': 22 * 1024 * 1024,
        }
        show_update_dialog(root, demo_rel, current_version, on_quit)

    ttk.Button(btn_row, text='บันทึกและเช็คอัปเดตทันที', style='Accent.TButton', command=save_and_check).pack(side='left')
    ttk.Button(btn_row, text='ดูตัวอย่างป๊อปอัปอัปเดต', command=preview_popup).pack(side='left', padx=8)
    ttk.Button(btn_row, text='ปิด', command=dialog.destroy).pack(side='right')
    return dialog

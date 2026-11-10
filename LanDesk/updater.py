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

DEFAULT_GITHUB_REPO = 'wachirawit1/LanDesk'


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


def fetch_latest_release(repo_slug=None, timeout=8, opener=None):
    slug = clean_repo_slug(repo_slug or DEFAULT_GITHUB_REPO)
    if not slug or '/' not in slug or slug == 'owner/landesk':
        slug = DEFAULT_GITHUB_REPO
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

    # Batch script forcibly closes old LanDesk processes, copies files over, launches new exe, and cleans up
    content = f"""@echo off
setlocal
cd /d "%TEMP%"
taskkill /F /IM LanDesk.exe >nul 2>&1
ping 127.0.0.1 -n 2 > nul
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
    dialog.title('อัปเดตโปรแกรม LanDesk')
    dialog.geometry('480x235')
    dialog.resizable(False, False)
    dialog.transient(root)
    dialog.attributes('-topmost', True)
    dialog.grab_set()

    frame = ttk.Frame(dialog, padding=20)
    frame.pack(fill='both', expand=True)

    ttk.Label(
        frame,
        text=f"พบเวอร์ชันใหม่ {release.get('tag', '')} พร้อมอัปเดต",
        font=('Leelawadee UI', 14, 'bold'),
        foreground='#00695c',
    ).pack(anchor='w')

    ver_text = f"เวอร์ชันในเครื่อง: v{current_version}   ➔   เวอร์ชันใหม่: {release.get('tag', '')}"
    ttk.Label(frame, text=ver_text, font=('Leelawadee UI', 10)).pack(anchor='w', pady=(6, 14))

    status_var = tk.StringVar()
    size_mb = (release.get('asset_size', 0) / (1024 * 1024))
    if size_mb > 0:
        status_var.set(f'กดอัปเดตเพื่อดาวน์โหลดและติดตั้งอัตโนมัติ ({size_mb:.1f} MB)')
    else:
        status_var.set('กดอัปเดตเพื่อดาวน์โหลดและติดตั้งอัตโนมัติ')

    progress_var = tk.DoubleVar(value=0)
    prog_bar = ttk.Progressbar(frame, variable=progress_var, maximum=100)
    prog_bar.pack(fill='x', pady=(0, 6))

    ttk.Label(frame, textvariable=status_var, font=('Leelawadee UI', 9)).pack(anchor='w', pady=(0, 12))

    cancel_event = threading.Event()

    btn_row = ttk.Frame(frame)
    btn_row.pack(fill='x', side='bottom')

    def start_auto_update():
        d_url = release.get('download_url')
        if not d_url:
            messagebox.showwarning('ยังไม่มีไฟล์อัปเดต', 'ยังไม่พบไฟล์แพ็กเกจใน Release ล่าสุดนี้ กรุณาลองใหม่อีกครั้งภายหลัง', parent=dialog)
            return

        update_btn.configure(state='disabled')
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
                dialog.after(0, lambda: status_var.set(f"กำลังดาวน์โหลด {pct}% ({d_mb:.1f} / {t_mb:.1f} MB)..."))

            try:
                download_file(d_url, temp_pkg, progress_callback=on_progress, cancel_event=cancel_event)
                dialog.after(0, lambda: status_var.set('ดาวน์โหลดสำเร็จ! กำลังอัปเดตและรีสตาร์ทโปรแกรม...'))
                time.sleep(0.4)

                if getattr(sys, 'frozen', False):
                    app_dir = Path(sys.executable).parent
                    apply_update_and_restart(temp_pkg, app_dir, Path(sys.executable).name)
                else:
                    dialog.after(0, lambda: messagebox.showinfo('ดาวน์โหลดสำเร็จ', f'ดาวน์โหลดไฟล์อัปเดตเรียบร้อยที่:\n{temp_pkg}', parent=dialog))
                    dialog.destroy()
                    return

                if on_quit:
                    dialog.after(100, on_quit)
                else:
                    dialog.after(100, dialog.destroy)
            except InterruptedError:
                dialog.after(0, lambda: status_var.set('ยกเลิกการดาวน์โหลดแล้ว'))
                dialog.after(0, lambda: update_btn.configure(state='normal'))
                dialog.after(0, lambda: cancel_btn.configure(text='ไว้คราวหน้า', command=dialog.destroy))
            except Exception as err:
                dialog.after(0, lambda: messagebox.showerror('อัปเดตไม่สำเร็จ', f'เกิดข้อผิดพลาดระหว่างอัปเดต:\n{err}', parent=dialog))
                dialog.after(0, lambda: status_var.set(f'เกิดข้อผิดพลาด: {err}'))
                dialog.after(0, lambda: update_btn.configure(state='normal'))
                dialog.after(0, lambda: cancel_btn.configure(text='ไว้คราวหน้า', command=dialog.destroy))

        threading.Thread(target=worker, daemon=True).start()

    update_btn = ttk.Button(btn_row, text='⚡ อัปเดตเดี๋ยวนี้', style='Accent.TButton', command=start_auto_update)
    update_btn.pack(side='left')

    cancel_btn = ttk.Button(btn_row, text='ไว้คราวหน้า', command=dialog.destroy)
    cancel_btn.pack(side='right')
    return dialog

import ipaddress
import tkinter as tk
from tkinter import messagebox, ttk
from security import is_startup_enabled, password_record, sanitize_alias, set_startup_enabled
from tray_win import apply_window_icon


def show_settings(app):
    window = tk.Toplevel(app.root)
    apply_window_icon(window)
    window.title('LanDesk · ตั้งค่าและความปลอดภัย')
    window.transient(app.root)
    window.grab_set()
    frame = ttk.Frame(window, padding=20)
    frame.pack(fill='both', expand=True)
    cfg = app.settings.snapshot()
    notebook = ttk.Notebook(frame)
    notebook.pack(fill='both', expand=True)
    access = ttk.Frame(notebook, padding=16)
    advanced_frame = ttk.Frame(notebook, padding=16)
    notebook.add(access, text=' รหัสผ่านและการทำงานอัตโนมัติ ')
    notebook.add(advanced_frame, text=' เครือข่ายและตัวตนเครื่อง ')
    ttk.Label(access, text='ตั้งรหัสผ่านเพื่อเข้าควบคุมได้ทันทีโดยไม่ต้องกดรับ', font=('Leelawadee UI', 14, 'bold')).pack(anchor='w')
    ttk.Label(access, text='รหัสผ่านอย่างน้อย 4 ตัวอักษร (ตั้งได้อย่างอิสระ) · เว้นว่างเพื่อคงค่าเดิม').pack(anchor='w', pady=6)
    password, confirm = tk.StringVar(), tk.StringVar()
    ttk.Entry(access, textvariable=password, show='•', width=62).pack(fill='x')
    ttk.Label(access, text='ยืนยันรหัสใหม่').pack(anchor='w', pady=(8, 2))
    ttk.Entry(access, textvariable=confirm, show='•').pack(fill='x')
    remove = tk.BooleanVar(value=False)
    ttk.Checkbutton(access, text='ลบรหัสผ่าน — ให้กดยอมรับทุกครั้ง', variable=remove).pack(anchor='w', pady=8)
    ttk.Label(access, text='การทำงานของเซอร์วิสอัตโนมัติ', font=('Leelawadee UI', 11, 'bold')).pack(anchor='w', pady=(8, 4))
    values = {}
    for key, label in [('auto_listen', 'เปิดรับการเชื่อมต่ออัตโนมัติทันทีที่เปิดโปรแกรม (เหมือน AnyDesk/VNC)'),
                       ('close_to_tray', 'ทำงานเป็นเซอร์วิสเบื้องหลังใน System Tray เมื่อกดปิดหน้าต่าง'),
                       ('auto_check_update', 'ตรวจหาอัปเดตเวอร์ชันใหม่อัตโนมัติเมื่อเปิดโปรแกรม')]:
        values[key] = tk.BooleanVar(value=cfg.get(key, True))
        ttk.Checkbutton(access, text=label, variable=values[key]).pack(anchor='w', pady=3)
    startup_var = tk.BooleanVar(value=bool(cfg.get('startup', True) or is_startup_enabled()))
    ttk.Checkbutton(access, text='รันเซอร์วิสอัตโนมัติเมื่อเปิดเครื่อง Windows (Auto-Start Service)',
                    variable=startup_var).pack(anchor='w', pady=3)
    advanced = {}
    for key, label in [('alias', 'ชื่อเรียกเครื่องนี้ (เช่น แผนกบัญชี-01 · เว้นว่างใช้ชื่อเครื่อง Windows)'),
                       ('port', 'พอร์ต TCP (ปกติไม่ต้องเปลี่ยน)'),
                       ('allow_networks', 'จำกัด IP เช่น 192.168.1.0/24 (ว่าง = เครือข่ายภายใน)'),
                       ('max_minutes', 'ตัด session เมื่อครบกี่นาที (1–240)')]:
        ttk.Label(advanced_frame, text=label).pack(anchor='w', pady=(6, 2))
        advanced[key] = tk.StringVar(value=str(cfg.get(key, '')))
        ttk.Entry(advanced_frame, textvariable=advanced[key]).pack(fill='x')
    ttk.Label(advanced_frame, text='ลายนิ้วมือเครื่องนี้ — ตรวจสอบตอนจับคู่ครั้งแรก').pack(anchor='w', pady=(12, 2))
    fingerprint = tk.StringVar(value=app.fingerprint)
    window.fingerprint_variable = fingerprint
    ttk.Entry(advanced_frame, textvariable=fingerprint, state='readonly', font=('Consolas', 10), width=68).pack(fill='x')
    def clear_trust():
        address = app.ip.get().strip()
        key = address + ':' + str(cfg['port'])
        if key in app.settings.snapshot()['trusted'] and messagebox.askyesno('ลืมเครื่อง', 'ลืมตัวตนที่บันทึกของ ' + address + '? ครั้งต่อไปต้องตรวจลายนิ้วมือใหม่', parent=window):
            trusted = dict(app.settings.snapshot()['trusted'])
            trusted.pop(key, None)
            app.settings.save(trusted=trusted)
    ttk.Button(advanced_frame, text='ลืมเครื่องที่กรอกในช่อง IP หน้าหลัก', command=clear_trust).pack(anchor='w', pady=8)

    def save():
        try:
            port, minutes = int(advanced['port'].get()), int(advanced['max_minutes'].get())
            if not 1024 <= port <= 65535 or not 1 <= minutes <= 240:
                raise ValueError('ตรวจสอบพอร์ตและเวลาตัด session')
            networks = advanced['allow_networks'].get().strip()
            for value in networks.split(','):
                if value.strip() and ipaddress.ip_network(value.strip()).version != 4:
                    raise ValueError('รองรับ IPv4 เท่านั้น')
            alias = sanitize_alias(advanced['alias'].get())
            record = cfg['password']
            if remove.get():
                record = None
            elif password.get():
                if password.get() != confirm.get():
                    raise ValueError('รหัสผ่านสองช่องไม่ตรงกัน')
                record = password_record(password.get())
            try:
                set_startup_enabled(startup_var.get())
            except OSError:
                pass
            app.settings.save(password=record, port=port, max_minutes=minutes, allow_networks=networks,
                              alias=alias, startup=bool(startup_var.get()),
                              control=True, clipboard=True, files=True,
                              **{key: var.get() for key, var in values.items()})
            password.set('')
            confirm.set('')
            app.stop_host()
            app.mode.set(app.mode_text())
            if hasattr(app, 'refresh_identity_banner'):
                app.refresh_identity_banner()
            window.destroy()
            if values['auto_listen'].get():
                app.start_host()
            else:
                app.host_status.set('บันทึกการตั้งค่าแล้ว')
        except Exception as error:
            messagebox.showerror('บันทึกไม่ได้', str(error), parent=window)
    ttk.Button(frame, text='บันทึกการตั้งค่า', style='Accent.TButton', command=save).pack(fill='x', pady=12)

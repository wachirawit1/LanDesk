"""LanDesk 0.3: AnyDesk-style LAN remote desktop with address book, live permissions, chat, and multi-monitor."""
import ipaddress
import os
from pathlib import Path
import queue
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from clipboard_win import Clipboard
from engine import Host, enable_dpi_awareness
from protocol import lan_address, make_identity, probe_host
from security import VERSION, Settings, password_record, sanitize_alias, set_startup_enabled
from tray_win import SingleInstanceGuard, SystemTrayIcon, apply_window_icon, set_app_user_model_id


def ips():
    values = []
    try:
        for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            try:
                ip = lan_address(item[4][0])
                if ip not in values and not ip.startswith('127.'):
                    values.append(ip)
            except ValueError:
                pass
    except OSError:
        pass
    return ', '.join(values) or 'ตรวจสอบ IPv4 ด้วย ipconfig'


class App:
    def __init__(self, root, settings=None, start_in_background=False):
        self.root, self.settings = root, settings or Settings()
        self.host = self.viewer = self.tray = self.instance_guard = None
        self.closing = False
        self.calls = queue.Queue()
        self.online_cache = {}
        self.context, self.fingerprint = make_identity(settings=self.settings)
        set_app_user_model_id()
        apply_window_icon(root)
        root.title('LanDesk ' + VERSION + ' · รีโมทภายในองค์กร')
        root.geometry('960x790')
        root.minsize(880, 700)
        root.configure(bg='#edf2f6')
        style = ttk.Style(root)
        style.theme_use('clam')
        style.configure('.', font=('Leelawadee UI', 11), background='#edf2f6', foreground='#172d40')
        style.configure('TButton', padding=(10, 6))
        style.configure('Accent.TButton', background='#00695c', foreground='white')
        style.map('Accent.TButton', background=[('active', '#00574d')])
        style.configure('Treeview', rowheight=26, font=('Leelawadee UI', 10))
        style.configure('Treeview.Heading', font=('Leelawadee UI', 10, 'bold'))
        body = ttk.Frame(root, padding=20)
        body.pack(fill='both', expand=True)
        header = ttk.Frame(body)
        header.pack(fill='x')
        title_box = ttk.Frame(header)
        title_box.pack(side='left', anchor='w')
        ttk.Label(title_box, text='LanDesk', font=('Leelawadee UI', 22, 'bold'), foreground='#00695c').pack(anchor='w')
        self.identity_label_var = tk.StringVar()
        self.refresh_identity_banner()
        ttk.Label(title_box, textvariable=self.identity_label_var, font=('Leelawadee UI', 10)).pack(anchor='w')

        btn_box = ttk.Frame(header)
        btn_box.pack(side='right', anchor='center')
        self.btn_settings = ttk.Button(btn_box, text='ตั้งค่าและความปลอดภัย', command=self.configure)
        self.btn_settings.pack(side='right')
        self.btn_update = ttk.Button(btn_box, text='ตรวจหาอัปเดต', command=lambda: self.check_updates(manual=True))
        self.btn_update.pack(side='right', padx=6)
        self.btn_installer = ttk.Button(btn_box, text='ติดตั้ง / ถอนการติดตั้ง', command=self.open_installer)
        self.btn_installer.pack(side='right', padx=(0, 6))
        self.btn_received = ttk.Button(btn_box, text='โฟลเดอร์ไฟล์ที่รับ', command=self.open_received)
        self.btn_received.pack(side='right', padx=(0, 6))

        receive = ttk.LabelFrame(body, text=' เครื่องนี้ (This Desk) ', padding=14)
        receive.pack(fill='x', pady=(10, 0))
        ip_row = ttk.Frame(receive)
        ip_row.pack(fill='x')
        self.ip_text = ips()
        ttk.Label(ip_row, text=self.ip_text, font=('Consolas', 19, 'bold')).pack(side='left')
        ttk.Button(ip_row, text='คัดลอก IP', command=self.copy_my_ip).pack(side='left', padx=12)
        ttk.Button(ip_row, text='ซ่อนไว้เบื้องหลัง (Tray)', command=lambda: self.hide_to_tray(notify=True)).pack(side='right')
        self.disconnect_btn = ttk.Button(ip_row, text='ตัดการเชื่อมต่อผู้รีโมท', command=self.disconnect_active_session, state='disabled')
        self.disconnect_btn.pack(side='right', padx=6)

        self.mode = tk.StringVar(value=self.mode_text())
        ttk.Label(receive, textvariable=self.mode, wraplength=820).pack(anchor='w', pady=(6, 2))
        self.host_status = tk.StringVar(value='กำลังเตรียมเซอร์วิสรับการเชื่อมต่ออัตโนมัติ...')
        ttk.Label(receive, textvariable=self.host_status, wraplength=820, foreground='#00695c', font=('Leelawadee UI', 10, 'bold')).pack(anchor='w', pady=(2, 0))

        chat_box = ttk.Frame(receive)
        chat_box.pack(fill='x', pady=(8, 0))
        self.host_chat_log = tk.Text(chat_box, height=3, state='disabled', wrap='word',
                                     font=('Leelawadee UI', 10), bg='#ffffff', fg='#172d40')
        self.host_chat_log.pack(fill='x', pady=(0, 4))
        chat_input_row = ttk.Frame(chat_box)
        chat_input_row.pack(fill='x')
        self.host_chat_var = tk.StringVar()
        host_chat_entry = ttk.Entry(chat_input_row, textvariable=self.host_chat_var)
        host_chat_entry.pack(side='left', fill='x', expand=True)
        host_chat_entry.bind('<Return>', lambda e: self.send_host_chat())
        ttk.Button(chat_input_row, text='ส่งข้อความแชท', command=self.send_host_chat).pack(side='right', padx=(8, 0))

        connect = ttk.LabelFrame(body, text=' เชื่อมต่อไปยังเครื่องอื่น (Remote Desk) ', padding=14)
        connect.pack(fill='x', pady=12)
        row = ttk.Frame(connect)
        row.pack(fill='x')
        ttk.Label(row, text='IP ปลายทาง:').pack(side='left')
        self.ip = tk.StringVar()
        self.ip_combo = ttk.Combobox(row, textvariable=self.ip, values=self.history_ips(), font=('Consolas', 15), width=18)
        self.ip_combo.pack(side='left', padx=(6, 12))
        self.ip_combo.bind('<Return>', lambda e: self.connect())
        ttk.Label(row, text='ชื่อบันทึก:').pack(side='left')
        self.contact_name = tk.StringVar()
        ttk.Entry(row, textvariable=self.contact_name, width=20).pack(side='left', padx=(6, 10))
        ttk.Button(row, text='บันทึกชื่อ', command=self.save_contact).pack(side='left')
        ttk.Button(row, text='เชื่อมต่อ', style='Accent.TButton', command=self.connect).pack(side='right')

        book = ttk.LabelFrame(body, text=' สมุดรายชื่อและเครื่องล่าสุด (Address Book) ', padding=12)
        book.pack(fill='both', expand=True)
        columns = ('name', 'ip', 'status', 'last')
        self.contacts_tree = ttk.Treeview(book, columns=columns, show='headings', height=5, selectmode='browse')
        self.contacts_tree.heading('name', text='ชื่อเครื่อง / แผนก')
        self.contacts_tree.heading('ip', text='IP Address')
        self.contacts_tree.heading('status', text='สถานะใน LAN')
        self.contacts_tree.heading('last', text='เชื่อมต่อล่าสุด')
        self.contacts_tree.column('name', width=240, anchor='w')
        self.contacts_tree.column('ip', width=160, anchor='w')
        self.contacts_tree.column('status', width=150, anchor='center')
        self.contacts_tree.column('last', width=160, anchor='center')
        self.contacts_tree.pack(fill='both', expand=True)
        self.contacts_tree.bind('<<TreeviewSelect>>', self.on_contact_select)
        self.contacts_tree.bind('<Double-1>', lambda e: self.connect_selected())

        book_actions = ttk.Frame(book)
        book_actions.pack(fill='x', pady=(8, 0))
        ttk.Button(book_actions, text='เชื่อมต่อเครื่องที่เลือก', command=self.connect_selected).pack(side='left')
        ttk.Button(book_actions, text='เช็กสถานะออนไลน์', command=self.probe_contacts).pack(side='left', padx=6)
        ttk.Button(book_actions, text='ติดดาว ★', command=self.toggle_favorite).pack(side='left')
        ttk.Button(book_actions, text='ลบรายชื่อ', command=self.delete_contact).pack(side='right')

        self.refresh_contacts()
        root.update_idletasks()
        self.clipboard = Clipboard(root.winfo_id())
        self.is_test_profile = any(k in str(self.settings.directory) for k in ('smoke-profile', 'ui-test-profile', 'landesk-test-'))
        if not self.is_test_profile:
            self.tray = SystemTrayIcon(
                f'LanDesk {VERSION} · พร้อมรับรีโมท ({self.ip_text})',
                on_open=lambda: self.post(self.show_from_tray),
                on_quit=lambda: self.post(self.close),
            )
            if self.settings.snapshot().get('startup', True):
                try:
                    set_startup_enabled(True, background=True)
                except OSError:
                    pass
        root.protocol('WM_DELETE_WINDOW', self.on_window_close)
        root.after(30, self.pump)
        if self.settings.snapshot().get('auto_listen', True) or start_in_background:
            root.after(150, self.start_host)
        if start_in_background and not self.is_test_profile:
            root.after(250, lambda: self.hide_to_tray(notify=False))
        if self.settings.snapshot().get('auto_check_update', True) and not self.is_test_profile:
            root.after(3000, lambda: self.check_updates(manual=False))

    def check_updates(self, manual=False):
        cfg = self.settings.snapshot()
        repo = (cfg.get('github_repo') or '').strip() or 'wachirawit1/LanDesk'

        if manual:
            self.host_status.set('กำลังตรวจหาอัปเดตเวอร์ชันใหม่...')

        def worker():
            try:
                from updater import fetch_latest_release, is_newer_version, show_update_dialog
                rel = fetch_latest_release(repo)
                if is_newer_version(rel['version'], VERSION):
                    self.post( self.host_status.set, f"พบเวอร์ชันใหม่พร้อมอัปเดต: {rel['tag']}")
                    self.post(show_update_dialog, self.root, rel, VERSION, self.close)
                else:
                    if manual:
                        self.post(self.host_status.set, f'🟢 พร้อมรับการเชื่อมต่ออัตโนมัติ · LanDesk v{VERSION} เป็นเวอร์ชันล่าสุดแล้ว')
                        self.post(lambda: messagebox.showinfo(
                            'เป็นเวอร์ชันล่าสุดแล้ว',
                            f'คุณกำลังใช้งาน LanDesk เวอร์ชันล่าสุดแล้ว (v{VERSION})',
                            parent=self.root,
                        ))
            except Exception as err:
                if manual:
                    err_text = str(err)
                    self.post(self.host_status.set, '🟢 พร้อมรับการเชื่อมต่ออัตโนมัติ · ตรวจหาอัปเดตไม่สำเร็จ')
                    self.post(lambda: messagebox.showwarning(
                        'ตรวจอัปเดตไม่สำเร็จ',
                        f'ไม่สามารถเชื่อมต่อตรวจหาอัปเดตได้ในขณะนี้:\n{err_text}',
                        parent=self.root,
                    ))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_identity_banner(self):
        cfg = self.settings.snapshot()
        alias = sanitize_alias(cfg.get('alias') or '') or socket.gethostname()
        self.identity_label_var.set(f'รุ่น {VERSION} · ชื่อเครื่อง: {alias} · เซอร์วิสพร้อมรับอัตโนมัติ')

    def history_ips(self):
        cfg = self.settings.snapshot()
        ips_set = {v.rsplit(':', 1)[0] for v in cfg.get('trusted', {})}
        for item in cfg.get('contacts', []):
            if isinstance(item, dict) and item.get('ip'):
                ips_set.add(item['ip'])
        return sorted(ips_set)

    def copy_my_ip(self):
        first_ip = self.ip_text.split(',')[0].strip()
        try:
            self.clipboard.text(first_ip)
            self.host_status.set(f'คัดลอก IP ({first_ip}) ลง Clipboard แล้ว')
        except Exception:
            self.root.clipboard_clear()
            self.root.clipboard_append(first_ip)
            self.host_status.set(f'คัดลอก IP ({first_ip}) แล้ว')

    def open_received(self):
        directory = self.settings.directory / 'Received'
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def mode_text(self):
        return ('เปิดใช้รหัสผ่าน: เข้าด้วยรหัสผ่าน หรือส่งคำขอให้กดรับ' if self.settings.snapshot().get('password')
                else 'ไม่ได้ตั้งรหัสผ่าน: เจ้าของเครื่องต้องกดยอมรับทุกครั้ง')

    def post(self, function, *args):
        if not self.closing:
            self.calls.put((function, args))

    def pump(self):
        try:
            for _ in range(100):
                try:
                    function, args = self.calls.get_nowait()
                except queue.Empty:
                    break
                try:
                    function(*args)
                except Exception:
                    pass
        finally:
            if not self.closing:
                self.root.after(30, self.pump)

    def ask(self, kind, data, cancel=None, timeout=60):
        done, box = threading.Event(), [None]
        expires = time.monotonic() + timeout
        self.post(self.dialog, kind, data, done, box, cancel, expires)
        while not done.wait(.05):
            if self.closing or (cancel and cancel.is_set()) or time.monotonic() >= expires:
                return None
        return box[0]

    def dialog(self, kind, data, done, box, cancel, expires):
        if self.closing or (cancel and cancel.is_set()) or time.monotonic() >= expires:
            done.set()
            return
        window = tk.Toplevel(self.root)
        apply_window_icon(window)
        window.title('LanDesk · ' + {'trust': 'ยืนยันเครื่อง', 'auth': 'เลือกวิธีเชื่อมต่อ', 'consent': 'คำขอรีโมท'}[kind])
        window.transient(self.root)
        window.attributes('-topmost', True)
        window.resizable(False, False)
        frame = ttk.Frame(window, padding=24)
        frame.pack(fill='both', expand=True)
        def finish(value=None):
            if not done.is_set():
                box[0] = value if time.monotonic() < expires and not (cancel and cancel.is_set()) else None
                done.set()
            window.destroy()
        window.protocol('WM_DELETE_WINDOW', finish)
        if kind == 'trust':
            ttk.Label(frame, text='ยืนยันตัวตนเครื่อง ' + data['address'], font=('Leelawadee UI', 14, 'bold')).pack(anchor='w')
            ttk.Label(frame, text='เปรียบเทียบลายนิ้วมือนี้กับหน้า “ตั้งค่า” ของปลายทาง\nผ่านผู้ใช้หรือฝ่าย IT ก่อนกดเชื่อถือ ครั้งต่อไปจะตรวจให้อัตโนมัติ', wraplength=580).pack(anchor='w', pady=12)
            value = tk.StringVar(value=data['fingerprint'])
            window.fingerprint_variable = value
            ttk.Entry(frame, textvariable=value, state='readonly', width=66, font=('Consolas', 10)).pack(fill='x', pady=8)
            ttk.Button(frame, text='ตรวจสอบตรงกันแล้ว — เชื่อถือเครื่องนี้', command=lambda: finish(True)).pack(fill='x')
        elif kind == 'auth':
            title_lbl = data['address']
            if data.get('alias'):
                title_lbl = f"{data['alias']} ({data['address']})"
            ttk.Label(frame, text='เชื่อมต่อ ' + title_lbl, font=('Leelawadee UI', 14, 'bold')).pack(anchor='w', pady=8)
            ttk.Button(frame, text='ส่งคำขอให้ปลายทางกดรับ', command=lambda: finish({'method': 'attended'})).pack(fill='x', pady=8)
            if data['password_available']:
                ttk.Label(frame, text='หรือกรอกรหัสผ่านของเครื่องปลายทาง').pack(anchor='w', pady=8)
                password = tk.StringVar()
                entry = ttk.Entry(frame, textvariable=password, show='•', width=45)
                entry.pack(fill='x')
                def by_password():
                    value = password.get()
                    password.set('')
                    finish({'method': 'password', 'password': value})
                ttk.Button(frame, text='เข้าด้วยรหัสผ่าน', command=by_password).pack(fill='x', pady=8)
                entry.bind('<Return>', lambda e: by_password())
            else:
                ttk.Label(frame, text='เครื่องนี้ไม่ได้เปิดใช้การเข้าด้วยรหัสผ่าน').pack(anchor='w', pady=8)
        else:
            ttk.Label(frame, text='เครื่อง ' + data['peer'] + ' ขอเชื่อมต่อรีโมท', font=('Leelawadee UI', 14, 'bold')).pack(anchor='w')
            ttk.Label(frame, text='กดอนุญาตเพื่อให้ผู้เชื่อมต่อเข้ามาควบคุมหน้าจอและรับส่งไฟล์ได้ทันที · หมดเวลาใน 30 วินาที').pack(anchor='w', pady=12)
            ttk.Button(
                frame,
                text='อนุญาตให้เชื่อมต่อและควบคุม',
                style='Accent.TButton',
                command=lambda: finish({'control': True, 'clipboard': True, 'files': True}),
            ).pack(fill='x', pady=8)
        ttk.Button(frame, text='ยกเลิก / ปฏิเสธ', command=finish).pack(fill='x', pady=(8, 0))
        def check():
            if done.is_set():
                return
            if self.closing or time.monotonic() >= expires or (cancel and cancel.is_set()):
                finish()
            else:
                window.after(100, check)
        check()

    def consent(self, peer, cancel):
        answer = self.ask('consent', {'peer': peer}, cancel, 30)
        return answer is not None, answer or {}

    def on_host_session(self, active, peer, perms):
        if hasattr(self, 'disconnect_btn'):
            self.disconnect_btn.configure(state='normal' if active else 'disabled')
        if active:
            self.append_host_chat('ระบบ', f'เริ่มการเชื่อมต่อกับ {peer}')
        else:
            self.append_host_chat('ระบบ', f'สิ้นสุดการเชื่อมต่อกับ {peer}')

    def disconnect_active_session(self):
        self.stop_host()
        self.root.after(150, self.start_host)

    def append_host_chat(self, sender, text):
        self.host_chat_log.configure(state='normal')
        self.host_chat_log.insert('end', f'{sender}: {text}\n')
        self.host_chat_log.see('end')
        self.host_chat_log.configure(state='disabled')

    def send_host_chat(self):
        msg = self.host_chat_var.get().strip()
        if not msg:
            return
        if not self.host or not self.host.connected_peer:
            self.host_status.set('ยังไม่มีผู้เชื่อมต่อเข้ามา จึงส่งแชทไม่ได้')
            return
        try:
            self.host.send_chat(msg)
            self.host_chat_var.set('')
            self.append_host_chat('คุณ', msg)
        except Exception as error:
            self.host_status.set(str(error))

    def cancel_host_transfer(self):
        if self.host:
            self.host.cancel_transfer()

    def start_host(self):
        if self.host:
            return
        host = None
        try:
            host = Host(
                self.consent,
                lambda text: self.post(self.host_status.set, text),
                settings=self.settings,
                clipboard=self.clipboard,
                audit_path=self.settings.directory / 'audit.jsonl',
                receive_directory=self.settings.directory / 'Received',
                on_chat=lambda peer, text: self.post(self.append_host_chat, peer, text),
                on_session=lambda active, peer, perms: self.post(self.on_host_session, active, peer, perms),
                identity=(self.context, self.fingerprint),
            )
            host.start(self.settings.snapshot()['port'])
            self.host = host
            self.host_status.set('🟢 พร้อมรับการเชื่อมต่ออัตโนมัติ · ' + self.mode_text())
        except Exception as error:
            if host:
                host.stop()
            messagebox.showerror('เปิดรับไม่ได้', str(error), parent=self.root)

    def stop_host(self):
        if self.host:
            self.host.stop()
            self.host = None
        if hasattr(self, 'disconnect_btn'):
            self.disconnect_btn.configure(state='disabled')
        self.host_status.set('ตัดการเชื่อมต่อแล้ว')

    def save_contact(self):
        try:
            ip = lan_address(self.ip.get().strip())
            name = sanitize_alias(self.contact_name.get().strip())
            self.settings.upsert_contact(ip, name=name)
            self.refresh_contacts()
            self.host_status.set(f'บันทึก {name or ip} ลงในสมุดรายชื่อแล้ว')
        except Exception as error:
            messagebox.showerror('บันทึกรายชื่อไม่ได้', str(error), parent=self.root)

    def record_connected(self, ip, alias=''):
        try:
            existing = next((c for c in self.settings.snapshot().get('contacts', []) if c.get('ip') == ip), None)
            name = existing.get('name') if existing and existing.get('name') else alias
            self.settings.upsert_contact(ip, name=name, touched=True)
            self.refresh_contacts()
        except Exception:
            pass

    def refresh_contacts(self):
        self.ip_combo.configure(values=self.history_ips())
        for row_id in self.contacts_tree.get_children():
            self.contacts_tree.delete(row_id)
        cfg = self.settings.snapshot()
        contacts = [c for c in cfg.get('contacts', []) if isinstance(c, dict) and c.get('ip')]
        known_ips = {c['ip'] for c in contacts}
        for trusted_addr in sorted(cfg.get('trusted', {})):
            t_ip = trusted_addr.rsplit(':', 1)[0]
            if t_ip not in known_ips:
                contacts.append({'ip': t_ip, 'name': '', 'favorite': False, 'last_connected': '-'})
        for item in contacts:
            ip = item['ip']
            star = '★ ' if item.get('favorite') else ''
            name = star + (item.get('name') or 'เครื่องในองค์กร')
            status = self.online_cache.get(ip, 'ยังไม่ตรวจสอบ')
            last = item.get('last_connected') or '-'
            self.contacts_tree.insert('', 'end', iid=ip, values=(name, ip, status, last))

    def on_contact_select(self, event=None):
        sel = self.contacts_tree.selection()
        if not sel:
            return
        ip = sel[0]
        self.ip.set(ip)
        for item in self.settings.snapshot().get('contacts', []):
            if isinstance(item, dict) and item.get('ip') == ip:
                self.contact_name.set(item.get('name', ''))
                break

    def connect_selected(self):
        sel = self.contacts_tree.selection()
        if sel:
            self.ip.set(sel[0])
        if self.ip.get().strip():
            self.connect()

    def toggle_favorite(self):
        ip = self.ip.get().strip() or (self.contacts_tree.selection()[0] if self.contacts_tree.selection() else '')
        if not ip:
            return
        try:
            current = next((c for c in self.settings.snapshot().get('contacts', []) if c.get('ip') == ip), None)
            fav = not (current.get('favorite', False) if current else False)
            name = self.contact_name.get().strip() or (current.get('name', '') if current else '')
            self.settings.upsert_contact(ip, name=name, favorite=fav)
            self.refresh_contacts()
        except Exception as error:
            messagebox.showerror('แก้ไขรายการไม่ได้', str(error), parent=self.root)

    def delete_contact(self):
        sel = self.contacts_tree.selection()
        ip = sel[0] if sel else self.ip.get().strip()
        if not ip:
            return
        self.settings.remove_contact(ip)
        self.online_cache.pop(ip, None)
        self.refresh_contacts()

    def probe_contacts(self):
        items = list(self.contacts_tree.get_children())
        if self.ip.get().strip():
            try:
                typed = lan_address(self.ip.get().strip())
                if typed not in items:
                    items.append(typed)
            except ValueError:
                pass
        if not items:
            return
        port = self.settings.snapshot()['port']
        for ip in items:
            self.online_cache[ip] = 'กำลังตรวจ…'
        self.refresh_contacts()
        def worker():
            for ip in items:
                online = probe_host(ip, port, timeout=1.0)
                self.online_cache[ip] = 'ออนไลน์ (พร้อมรับ)' if online else 'ออฟไลน์ / ปิดรับ'
                self.post(self.refresh_contacts)
        threading.Thread(target=worker, daemon=True).start()

    def connect(self):
        if self.viewer:
            self.viewer.window.lift()
            return
        try:
            from viewer import Viewer
            target_ip = lan_address(self.ip.get().strip())
            if self.contact_name.get().strip():
                self.settings.upsert_contact(target_ip, name=self.contact_name.get().strip())
                self.refresh_contacts()
            self.viewer = Viewer(self, target_ip, self.settings.snapshot()['port'])
        except Exception as error:
            messagebox.showerror('เชื่อมต่อไม่ได้', str(error), parent=self.root)

    def configure(self):
        from settings_ui import show_settings
        show_settings(self)

    def open_installer(self):
        from installer import show_installer_dialog
        show_installer_dialog(parent=self.root, on_quit=self.close)

    def show_from_tray(self, ack_callback=None):
        if self.closing:
            return
        try:
            self.root.deiconify()
            self.root.state('normal')
            self.root.attributes('-topmost', True)
            self.root.lift()
            self.root.focus_force()
            self.root.after(250, lambda: self.root.attributes('-topmost', False))
        except Exception:
            pass
        if ack_callback:
            try:
                ack_callback()
            except Exception:
                pass

    def hide_to_tray(self, notify=True):
        if self.closing or not self.tray:
            return
        try:
            self.root.withdraw()
            if notify:
                self.tray.notify_balloon(
                    'LanDesk ทำงานอยู่เบื้องหลัง',
                    f'พร้อมรับการเชื่อมต่อจากเครื่องในวง LAN ตลอดเวลา ({self.ip_text}) · คลิกไอคอนเพื่อเปิดหน้าต่าง',
                )
        except Exception:
            pass

    def on_window_close(self):
        if self.is_test_profile or not self.tray or not self.settings.snapshot().get('close_to_tray', True):
            self.close()
        else:
            self.hide_to_tray(notify=True)

    def close(self):
        self.closing = True
        if self.tray:
            self.tray.close()
            self.tray = None
        if self.instance_guard:
            self.instance_guard.close()
            self.instance_guard = None
        self.stop_host()
        if self.viewer:
            self.viewer.close()
        self.root.destroy()


def main():
    if sys.platform != 'win32':
        raise SystemExit('รองรับ Windows เท่านั้น')
    enable_dpi_awareness()
    set_app_user_model_id()
    exe_stem = Path(sys.executable).stem.lower() if getattr(sys, 'frozen', False) else ''
    args = [a.lower() for a in sys.argv[1:]]
    if 'uninstall' in exe_stem or '--uninstall' in args or '/uninstall' in args:
        from installer import show_uninstaller_dialog, uninstall_landesk
        if '--silent' in args or '/s' in args:
            uninstall_landesk(remove_user_data='--purge' in args)
            return
        win = show_uninstaller_dialog(parent=None)
        win.mainloop()
        return
    if '--install' in args or '/install' in args:
        from installer import install_landesk, show_installer_dialog
        if '--silent' in args or '/s' in args:
            install_landesk()
            return
        win = show_installer_dialog(parent=None)
        win.mainloop()
        return

    is_test_env = any(k in os.environ.get('LOCALAPPDATA', '') for k in ('smoke-profile', 'ui-test-profile', 'landesk-test-'))
    app_ref = [None]
    guard = None
    if not is_test_env:
        guard = SingleInstanceGuard(on_wakeup=lambda ack=None: app_ref[0] and app_ref[0].post(app_ref[0].show_from_tray, ack))
        if guard.already_running:
            return

    root = tk.Tk()
    try:
        try:
            settings = Settings()
        except ValueError as err:
            if 'อ่านการตั้งค่าที่เข้ารหัสไม่ได้' in str(err):
                if messagebox.askyesno(
                    'รีเซ็ตการตั้งค่า LanDesk',
                    'ไม่สามารถอ่านไฟล์การตั้งค่าเดิมได้ (อาจเกิดจากการก๊อปปี้ไฟล์มาจากเครื่องอื่น หรือเปลี่ยนบัญชี Windows)\n\nคุณต้องการรีเซ็ตการตั้งค่าเป็นค่าเริ่มต้นใหม่สำหรับเครื่องนี้หรือไม่?',
                    parent=root
                ):
                    raw_path = Path(os.environ.get('LOCALAPPDATA', '')) / 'LanDesk' / 'settings-v2.dat'
                    if raw_path.exists():
                        try:
                            raw_path.replace(raw_path.with_suffix('.bak'))
                        except Exception:
                            raw_path.unlink(missing_ok=True)
                    settings = Settings()
                else:
                    raise
            else:
                raise
        start_bg = '--background' in args or '/background' in args
        app_instance = App(root, settings=settings, start_in_background=start_bg)
        app_instance.instance_guard = guard
        app_ref[0] = app_instance
    except Exception as error:
        if guard:
            guard.close()
        root.withdraw()
        messagebox.showerror('LanDesk เปิดไม่ได้', str(error), parent=root)
        root.destroy()
        return
    root.mainloop()


if __name__ == '__main__':
    main()

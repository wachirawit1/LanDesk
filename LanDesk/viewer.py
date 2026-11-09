import io
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageTk
from protocol import connect_pinned, receive_json, receive_packet, send_json
from transfers import TransferPeer
from tray_win import apply_window_icon


class Viewer:
    def __init__(self, app, host, port):
        self.app = app
        self.host_ip = host
        self.remote_alias = host
        self.closed = threading.Event()
        self.sock = None
        self.events, self.actions = queue.Queue(maxsize=256), queue.Queue(maxsize=32)
        self.frames = queue.Queue(maxsize=1)
        self.permissions = {'control': False, 'clipboard': False, 'files': False}
        self.rect = self.image = None
        self.last_move = 0
        self.quality = 'sharp'
        self.monitor = 0
        self.monitors_count = 1
        self.accept_files = False
        self.last_files = None
        self.last_text = None
        self.last_clip_seq = getattr(app, 'clipboard', None).sequence_number() if getattr(app, 'clipboard', None) else 0
        self.received = []
        self.received_batch = None
        self.unread_chat = 0
        self.chat_visible = False
        self.window = tk.Toplevel(app.root)
        apply_window_icon(self.window)
        self.window.title('LanDesk · ' + host)
        self.window.geometry('1240x800')
        self.window.minsize(940, 540)
        self.window.protocol('WM_DELETE_WINDOW', self.close)
        self.status = tk.StringVar(value='กำลังเชื่อมต่อ ' + host + '…')
        self.transfer_status = tk.StringVar(value='คลิกขวา Copy ไฟล์ที่เครื่องเรา แล้วไปคลิกขวา Paste (วาง) หรือกด Ctrl+V ที่ปลายทางได้เลย')
        self.progress_var = tk.DoubleVar(value=0)
        bar = ttk.Frame(self.window, padding=(10, 8, 10, 4))
        bar.pack(fill='x')
        ttk.Label(bar, textvariable=self.status, font=('Leelawadee UI', 11, 'bold')).pack(side='left')
        ttk.Button(bar, text='ตัดการเชื่อมต่อ', command=self.close).pack(side='right')
        self.chat_button = ttk.Button(bar, text='แชท (0)', command=self.toggle_chat)
        self.chat_button.pack(side='right', padx=6)
        ttk.Button(bar, text='เต็มจอ (F11)', command=self.toggle_fullscreen).pack(side='right')
        toolbar = ttk.Frame(self.window, padding=(10, 2, 10, 8))
        toolbar.pack(fill='x')
        self.quality_var = tk.StringVar(value='ภาพชัด')
        quality = ttk.Combobox(toolbar, textvariable=self.quality_var,
                               values=['ภาพชัด', 'ลื่นไหล', 'ไม่สูญเสียรายละเอียด'],
                               state='readonly', width=18)
        quality.pack(side='left')
        quality.bind('<<ComboboxSelected>>', lambda e: setattr(
            self, 'quality', {'ภาพชัด': 'sharp', 'ลื่นไหล': 'smooth', 'ไม่สูญเสียรายละเอียด': 'lossless'}[self.quality_var.get()]))
        self.monitor_var = tk.StringVar(value='จอ 1')
        self.monitor_box = ttk.Combobox(toolbar, textvariable=self.monitor_var,
                                        values=['จอ 1'], state='readonly', width=11)
        self.monitor_box.pack(side='left', padx=(6, 0))
        self.monitor_box.bind('<<ComboboxSelected>>', self.select_monitor)
        self.native = tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar, text='ขนาด 100%', variable=self.native, command=self.redraw).pack(side='left', padx=8)
        self.draw_cursor = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text='เมาส์ปลายทาง', variable=self.draw_cursor).pack(side='left', padx=(0, 8))
        ttk.Button(toolbar, text='ส่งไฟล์…', command=self.choose_files).pack(side='left')
        ttk.Button(toolbar, text='ส่งโฟลเดอร์…', command=self.choose_folder).pack(side='left', padx=4)
        ttk.Button(toolbar, text='โฟลเดอร์ไฟล์ที่รับ', command=self.open_received).pack(side='left', padx=4)
        ttk.Button(toolbar, text='ยกเลิกโอนไฟล์', command=lambda: self.action('cancel_transfer', None)).pack(side='left', padx=4)
        bottom = ttk.Frame(self.window, padding=(10, 4, 10, 6))
        bottom.pack(side='bottom', fill='x')
        self.progress_bar = ttk.Progressbar(bottom, variable=self.progress_var, maximum=100, length=180, mode='determinate')
        self.progress_bar.pack(side='right', padx=(8, 0))
        ttk.Label(bottom, textvariable=self.transfer_status).pack(side='left', fill='x', expand=True)
        workspace = ttk.Frame(self.window)
        workspace.pack(fill='both', expand=True)
        surface = ttk.Frame(workspace)
        surface.pack(side='left', fill='both', expand=True)
        self.chat_panel = ttk.Frame(workspace, width=300, padding=8)
        ttk.Label(self.chat_panel, text='แชทกับเครื่องปลายทาง', font=('Leelawadee UI', 11, 'bold')).pack(anchor='w', pady=(0, 4))
        self.chat_log = tk.Text(self.chat_panel, width=32, height=18, state='disabled', wrap='word',
                                font=('Leelawadee UI', 10), bg='#ffffff', fg='#172d40')
        self.chat_log.pack(fill='both', expand=True, pady=4)
        chat_row = ttk.Frame(self.chat_panel)
        chat_row.pack(fill='x', pady=(4, 0))
        self.chat_input = tk.StringVar()
        chat_entry = ttk.Entry(chat_row, textvariable=self.chat_input)
        chat_entry.pack(side='left', fill='x', expand=True)
        chat_entry.bind('<Return>', lambda e: self.send_chat())
        ttk.Button(chat_row, text='ส่ง', command=self.send_chat).pack(side='right', padx=(6, 0))
        self.canvas = tk.Canvas(surface, bg='#111c27', highlightthickness=0, takefocus=True)
        horizontal = ttk.Scrollbar(surface, orient='horizontal', command=self.canvas.xview)
        vertical = ttk.Scrollbar(surface, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=horizontal.set, yscrollcommand=vertical.set)
        surface.rowconfigure(0, weight=1)
        surface.columnconfigure(0, weight=1)
        self.canvas.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        self.canvas.bind('<Configure>', lambda e: self.redraw())
        self.canvas.bind('<Motion>', self.motion)
        for button in (1, 2, 3):
            self.canvas.bind(f'<ButtonPress-{button}>', lambda e, b=button: self.button(e, b, True))
            self.canvas.bind(f'<ButtonRelease-{button}>', lambda e, b=button: self.button(e, b, False))
        self.canvas.bind('<MouseWheel>', lambda e: self.enqueue({'type': 'wheel', 'delta': max(-1200, min(1200, e.delta))}))
        self.canvas.bind('<KeyPress>', lambda e: self.key(e, True))
        self.canvas.bind('<KeyRelease>', lambda e: self.key(e, False))
        self.canvas.bind('<FocusOut>', lambda e: self.enqueue({'type': 'release'}))
        self.canvas.bind('<Enter>', lambda e: self.sync_local_clipboard())
        self.window.bind('<FocusIn>', lambda e: self.sync_local_clipboard())
        self.canvas.bind('<Control-Alt-F12>', lambda e: self.close())
        self.window.bind('<Control-Alt-F12>', lambda e: self.close())
        self.canvas.bind('<F11>', lambda e: self.toggle_fullscreen())
        self.window.after(40, self.paint)
        threading.Thread(target=self.run, args=(host, port), daemon=True).start()

    def toggle_fullscreen(self):
        self.window.attributes('-fullscreen', not self.window.attributes('-fullscreen'))

    def toggle_chat(self):
        if self.chat_visible:
            self.chat_panel.pack_forget()
            self.chat_visible = False
        else:
            self.chat_panel.pack(side='right', fill='y')
            self.chat_visible = True
            self.unread_chat = 0
            self.chat_button.configure(text='แชท (0)')

    def append_chat(self, sender, text):
        if self.closed.is_set():
            return
        self.chat_log.configure(state='normal')
        self.chat_log.insert('end', f'{sender}: {text}\n')
        self.chat_log.see('end')
        self.chat_log.configure(state='disabled')
        if not self.chat_visible and sender != 'คุณ':
            self.unread_chat += 1
            self.chat_button.configure(text=f'แชท ({self.unread_chat})')

    def send_chat(self):
        msg = self.chat_input.get().strip()
        if not msg:
            return
        if len(msg) > 1000:
            self.transfer_status.set('ข้อความแชทยาวเกิน 1,000 ตัวอักษร')
            return
        self.chat_input.set('')
        self.append_chat('คุณ', msg)
        self.action('chat', msg)

    def select_monitor(self, event=None):
        val = self.monitor_var.get()
        if val == 'ทุกหน้าจอ':
            self.monitor = -1
        elif val.startswith('จอ '):
            try:
                self.monitor = max(0, int(val.split()[1]) - 1)
            except ValueError:
                self.monitor = 0
        else:
            self.monitor = 0

    def update_monitors(self, count):
        if self.closed.is_set() or type(count) is not int or count < 1 or count == self.monitors_count:
            return
        self.monitors_count = count
        options = [f'จอ {i + 1}' for i in range(count)]
        if count > 1:
            options.append('ทุกหน้าจอ')
        self.monitor_box.configure(values=options)
        if self.monitor_var.get() not in options:
            self.monitor_var.set('จอ 1')
            self.monitor = 0

    def enqueue(self, event):
        if self.permissions['control'] and not self.closed.is_set():
            try:
                self.events.put_nowait(event)
            except queue.Full:
                try:
                    self.events.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self.events.put_nowait(event)
                except queue.Full:
                    pass

    def action(self, kind, value):
        if self.closed.is_set():
            return
        try:
            self.actions.put_nowait((kind, value))
        except queue.Full:
            self.transfer_status.set('มีคำสั่งรออยู่ กรุณารอสักครู่')

    def position(self, event):
        if self.rect:
            x, y, w, h = self.rect
            return {'x': max(0, min(1, (self.canvas.canvasx(event.x) - x) / max(1, w - 1))),
                    'y': max(0, min(1, (self.canvas.canvasy(event.y) - y) / max(1, h - 1)))}

    def motion(self, event):
        if time.monotonic() - self.last_move < .03:
            return
        self.last_move = time.monotonic()
        pos = self.position(event)
        if pos:
            self.enqueue({'type': 'move', **pos})

    def button(self, event, button, down):
        self.canvas.focus_set()
        if down:
            self.sync_local_clipboard()
        pos = self.position(event)
        if pos:
            self.enqueue({'type': 'button', 'button': button, 'down': down, **pos})

    def key(self, event, down):
        if down and event.state & 4 and event.keycode in (67, 86) and (self.permissions['clipboard'] or self.permissions['files']):
            (self.copy_remote if event.keycode == 67 else self.paste_local)()
        elif 8 <= event.keycode <= 254:
            self.enqueue({'type': 'key', 'code': event.keycode, 'down': down})
        return 'break'

    def choose_files(self):
        if not self.permissions['files']:
            self.transfer_status.set('ปลายทางยังไม่ได้อนุญาตรับส่งไฟล์')
            return
        paths = filedialog.askopenfilenames(parent=self.window)
        if paths:
            self.action('files', (paths, False))

    def choose_folder(self):
        if not self.permissions['files']:
            self.transfer_status.set('ปลายทางยังไม่ได้อนุญาตรับส่งไฟล์')
            return
        folder = filedialog.askdirectory(parent=self.window, mustexist=True)
        if folder:
            self.action('files', ([folder], False))

    def open_received(self):
        directory = self.app.settings.directory / 'Received'
        directory.mkdir(parents=True, exist_ok=True)
        os.startfile(str(directory))

    def copy_remote(self):
        if self.permissions['clipboard'] or self.permissions['files']:
            self.accept_files = self.permissions['files']
            self.action('copy', None)
        else:
            self.transfer_status.set('ปลายทางยังไม่ได้อนุญาต Clipboard หรือไฟล์')

    def sync_local_clipboard(self, force=False):
        if self.closed.is_set() or not getattr(self.app, 'clipboard', None):
            return
        if not (self.permissions.get('files') or self.permissions.get('clipboard')):
            return
        try:
            seq = self.app.clipboard.sequence_number()
            if not force and seq and seq == self.last_clip_seq:
                return
            self.last_clip_seq = seq
            data = self.app.clipboard.read()
            if data.get('files') and self.permissions.get('files'):
                valid_paths = [p for p in data['files'] if os.path.exists(p)]
                if not valid_paths:
                    return
                signature = tuple((p, os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in valid_paths)
                if signature != self.last_files:
                    self.last_files = signature
                    self.transfer_status.set('กำลังซิงค์ไฟล์จาก Clipboard ไปยังเครื่องปลายทาง…')
                    self.action('files', (valid_paths, False))
            elif 'text' in data and self.permissions.get('clipboard'):
                text_val = data['text']
                if text_val and text_val != self.last_text:
                    self.last_text = text_val
                    self.action('set_text', text_val)
        except Exception:
            pass

    def paste_local(self):
        try:
            data = self.app.clipboard.read()
            if data.get('files') and self.permissions['files']:
                signature = tuple((p, os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in data['files'])
                if signature == self.last_files and not (getattr(self, 'transfer', None) and (self.transfer.outgoing or self.transfer.pending)):
                    self.action('paste_files', None)
                elif signature == self.last_files and getattr(self, 'transfer', None) and (self.transfer.outgoing or self.transfer.pending):
                    self.transfer.batch_auto_paste = True
                    self.transfer_status.set('กำลังส่งไฟล์… จะวางให้อัตโนมัติทันทีที่ส่งเสร็จ')
                else:
                    self.last_files = signature
                    self.last_clip_seq = self.app.clipboard.sequence_number()
                    self.action('files', (data['files'], True))
            elif 'text' in data and self.permissions['clipboard']:
                self.last_text = data['text']
                self.last_clip_seq = self.app.clipboard.sequence_number()
                self.action('text', data['text'])
            else:
                self.transfer_status.set('ชนิด Clipboard นี้ไม่รองรับ หรือปลายทางไม่ได้อนุญาต')
        except Exception as error:
            self.transfer_status.set(str(error))

    def on_batch_sent(self, auto_paste):
        if auto_paste and self.permissions.get('control') and self.permissions.get('files'):
            self.action('paste_files', None)
            self.app.post(self.set_transfer_status, 'ส่งไฟล์เสร็จและสั่งวางที่ปลายทางให้อัตโนมัติแล้ว')
        elif self.permissions.get('files'):
            self.app.post(
                self.set_transfer_status,
                'ซิงค์ไฟล์ไปที่ Clipboard ปลายทางแล้ว · คลิกขวาเลือก Paste (วาง) หรือกด Ctrl+V ในโฟลเดอร์ปลายทางได้เลย!'
            )

    def run(self, host, port):
        transfer = TransferPeer(
            self.app.settings.directory / 'Received',
            self.file_received,
            lambda text: self.app.post(self.set_transfer_status, text),
            on_batch_sent=self.on_batch_sent,
        )
        self.transfer = transfer
        try:
            address = host + ':' + str(port)
            def trust(fingerprint):
                if self.app.ask('trust', {'address': address, 'fingerprint': fingerprint}, self.closed):
                    self.app.settings.trust(address, fingerprint)
                    return True
                return False
            pin = self.app.settings.snapshot()['trusted'].get(address, '')
            self.sock = connect_pinned(host, port, pin, trust)
            if self.closed.is_set():
                return
            self.sock.settimeout(65)
            send_json(self.sock, {'version': 3})
            methods = receive_json(self.sock)
            if not methods.get('ok'):
                raise ValueError(methods.get('error', 'เชื่อมต่อไม่ได้'))
            if methods.get('alias'):
                self.remote_alias = str(methods['alias'])[:48]
            auth = (self.app.ask('auth', {'address': address, **methods}, self.closed)
                    if methods.get('password_available') else {'method': 'attended'})
            if auth is None or self.closed.is_set():
                self.app.post(self.close)
                return
            send_json(self.sock, auth)
            auth.clear()
            result = receive_json(self.sock)
            if not result.get('ok'):
                raise ValueError(result.get('error', 'ยืนยันตัวตนไม่ผ่าน'))
            if result.get('waiting'):
                self.app.post(self.set_status, 'รอปลายทางกดยอมรับ…')
            result = receive_json(self.sock)
            if not result.get('ok'):
                raise ValueError(result.get('error', 'ปลายทางไม่อนุญาต'))
            if result.get('alias'):
                self.remote_alias = str(result['alias'])[:48]
            if hasattr(self.app, 'record_connected'):
                self.app.post(self.app.record_connected, host, self.remote_alias)
            if result.get('monitors'):
                self.app.post(self.update_monitors, result.get('monitors'))
            self.sock.settimeout(15)
            while not self.closed.is_set():
                meta = receive_json(self.sock, 1024 * 1024)
                if meta.get('error'):
                    raise ValueError(str(meta['error']))
                self.permissions = {k: meta.get(k) is True for k in self.permissions}
                if meta.get('monitors'):
                    self.app.post(self.update_monitors, meta.get('monitors'))
                if meta.get('frame'):
                    data = receive_packet(self.sock)
                    try:
                        self.frames.get_nowait()
                    except queue.Empty:
                        pass
                    self.frames.put_nowait(data)
                if meta.get('transfer'):
                    msg = meta['transfer']
                    if isinstance(msg, dict) and msg.get('op') == 'cancel':
                        transfer.receive(msg)
                        self.app.post(self.progress_var.set, 0)
                    elif not self.permissions['files'] or (msg.get('op') == 'begin' and not self.accept_files):
                        raise ValueError('ปฏิเสธการรับไฟล์ที่ไม่ได้ร้องขอ')
                    else:
                        transfer.receive(msg)
                        self.app.post(self.progress_var.set, transfer.progress)
                if meta.get('chat') and isinstance(meta['chat'], list):
                    for chat_text in meta['chat'][:10]:
                        if isinstance(chat_text, str) and chat_text.strip():
                            self.app.post(self.append_chat, self.remote_alias, chat_text.strip()[:1000])
                if 'clipboard_text' in meta:
                    if not self.permissions['clipboard']:
                        raise ValueError('ไม่ได้อนุญาตข้อความ Clipboard')
                    self.last_text = meta['clipboard_text']
                    self.app.clipboard.text(meta['clipboard_text'])
                    self.last_clip_seq = self.app.clipboard.sequence_number()
                    self.app.post(self.set_transfer_status, 'รับข้อความแล้ว · วางในเครื่องนี้ด้วย Ctrl+V หรือคลิกขวา Paste')
                request = {'events': [], 'quality': self.quality, 'monitor': self.monitor,
                           'draw_cursor': bool(self.draw_cursor.get())}
                while True:
                    try:
                        request['events'].append(self.events.get_nowait())
                    except queue.Empty:
                        break
                try:
                    kind, value = self.actions.get_nowait()
                    if kind == 'files' and self.permissions['files']:
                        if isinstance(value, tuple):
                            paths, auto_paste = value
                        else:
                            paths, auto_paste = value, False
                        try:
                            transfer.queue_files(paths, auto_paste=auto_paste)
                        except Exception as err:
                            self.app.post(self.set_transfer_status, str(err))
                    elif kind == 'copy':
                        self.received.clear()
                        request['clipboard'] = {'op': 'copy'}
                    elif kind == 'text':
                        request['clipboard'] = {'op': 'paste_text', 'text': value}
                    elif kind == 'set_text':
                        request['clipboard'] = {'op': 'set_text', 'text': value}
                    elif kind == 'paste_files':
                        if transfer.outgoing or transfer.pending:
                            transfer.batch_auto_paste = True
                            self.app.post(self.set_transfer_status, 'กำลังส่งไฟล์… จะวางให้อัตโนมัติเมื่อส่งเสร็จ')
                        else:
                            request['clipboard'] = {'op': 'paste_files'}
                    elif kind == 'chat':
                        request['chat'] = value
                    elif kind in ('cancel', 'cancel_transfer'):
                        transfer.abort(notify_peer=True)
                        request['cancel_transfer'] = True
                        self.app.post(self.progress_var.set, 0)
                        self.app.post(self.set_transfer_status, 'ยกเลิกการโอนไฟล์แล้ว (Session ยังเชื่อมต่ออยู่)')
                except queue.Empty:
                    pass
                request['transfer'] = transfer.next_message() if (self.permissions['files'] or transfer.replies) else None
                if transfer.outgoing:
                    self.app.post(self.progress_var.set, transfer.progress)
                send_json(self.sock, request)
                label = f'{self.remote_alias} ({host})' if self.remote_alias != host else host
                perm_items = []
                if self.permissions['control']:
                    perm_items.append('ควบคุมได้')
                else:
                    perm_items.append('ดูอย่างเดียว')
                if self.permissions['clipboard']:
                    perm_items.append('Clipboard')
                if self.permissions['files']:
                    perm_items.append('ไฟล์')
                self.app.post(self.set_status, f'เชื่อมต่อกับ {label} · ' + ' / '.join(perm_items))
        except Exception as error:
            if not self.closed.is_set():
                self.app.post(self.failed, str(error))
        finally:
            transfer.cancel()
            if self.sock:
                self.sock.close()

    def file_received(self, path):
        if self.received_batch != self.transfer.received_batch:
            self.received.clear()
            self.received_batch = self.transfer.received_batch
        self.received.append(path)
        self.app.clipboard.files(self.received)
        try:
            self.last_files = tuple((str(p), os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in self.received if os.path.exists(p))
            self.last_clip_seq = self.app.clipboard.sequence_number()
        except Exception:
            pass

    def set_status(self, text):
        if not self.closed.is_set():
            self.status.set(text)

    def set_transfer_status(self, text):
        if not self.closed.is_set():
            self.transfer_status.set(text)
            if hasattr(self, 'transfer') and self.transfer:
                self.progress_var.set(self.transfer.progress)

    def failed(self, text):
        if not self.closed.is_set():
            messagebox.showerror('การเชื่อมต่อสิ้นสุด', text, parent=self.window)
            self.close()

    def redraw(self):
        if self.image is None or self.closed.is_set():
            return
        image = self.image.copy()
        cw, ch = max(1, self.canvas.winfo_width()), max(1, self.canvas.winfo_height())
        if not self.native.get():
            image.thumbnail((cw, ch), Image.Resampling.LANCZOS)
        w, h = image.size
        x, y = max(0, (cw - w) // 2), max(0, (ch - h) // 2)
        self.rect = x, y, w, h
        self.photo = ImageTk.PhotoImage(image)
        self.canvas.delete('all')
        self.canvas.create_image(x, y, image=self.photo, anchor='nw')
        self.canvas.configure(scrollregion=(0, 0, max(w, cw), max(h, ch)))

    def paint(self):
        if self.closed.is_set():
            return
        try:
            data = self.frames.get_nowait()
            image = Image.open(io.BytesIO(data))
            if image.width > 4096 or image.height > 2160:
                raise ValueError('ภาพมีขนาดเกินกำหนด')
            image.load()
            self.image = image
            self.redraw()
        except queue.Empty:
            pass
        except Exception as error:
            self.failed(str(error))
            return
        self.window.after(40, self.paint)

    def close(self):
        if self.closed.is_set():
            return
        self.closed.set()
        if self.sock:
            try:
                import socket
                self.sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            self.sock.close()
        self.window.destroy()
        self.app.viewer = None

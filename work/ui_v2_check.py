import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch
import tkinter as tk
from PIL import Image
sys.path.insert(0, str(Path('LanDesk').resolve()))
from app import App
from security import Settings
from updater import show_update_dialog
from viewer import Viewer

root = tk.Tk()
settings = Settings(Path('work/ui-test-profile').resolve())
app = App(root, settings)
root.update()
print('Main UI:', root.winfo_width(), root.winfo_height(), 'Received btn width:', app.btn_received.winfo_width())
assert app.btn_received.winfo_width() >= 100, f'btn_received too narrow: {app.btn_received.winfo_width()}'
app.configure()
root.update()
settings_window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
assert settings_window.fingerprint_variable.get() == app.fingerprint
print('Settings UI:', settings_window.winfo_width(), settings_window.winfo_height())
settings_window.destroy()
for kind, data in [('trust', {'address': '127.0.0.1:45888', 'fingerprint': 'A' * 64}),
                   ('auth', {'address': '127.0.0.1', 'password_available': True}),
                   ('consent', {'peer': '127.0.0.1'})]:
    app.dialog(kind, data, threading.Event(), [None], threading.Event(), time.monotonic() + 60)
    root.update()
    window = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
    if kind == 'trust':
        assert window.fingerprint_variable.get() == 'A' * 64
    print(kind, 'dialog:', window.winfo_width(), window.winfo_height())
    window.destroy()
with patch('threading.Thread.start'):
    viewer = Viewer(app, '127.0.0.1', 45888)
    app.viewer = viewer
    root.update()
    viewer.image = Image.new('RGB', (1920, 1080), '#186658')
    viewer.redraw()
    root.update()
    assert viewer.rect[2] < 1920
    viewer.native.set(True)
    viewer.redraw()
    assert viewer.rect[2:] == (1920, 1080)
    viewer.update_monitors(2)
    assert 'ทุกหน้าจอ' in viewer.monitor_box.cget('values')
    viewer.append_chat('RemotePC', 'ทดสอบแชท')
    assert viewer.unread_chat == 1
    viewer.toggle_chat()
    assert viewer.unread_chat == 0 and viewer.chat_visible
    print('Viewer fit / 100% / Multi-monitor / Chat: OK')
    viewer.close()
app.ip.set('192.168.1.99')
app.contact_name.set('เครื่องทดสอบ')
app.save_contact()
assert '192.168.1.99' in app.contacts_tree.get_children()
app.delete_contact()
assert '192.168.1.99' not in app.contacts_tree.get_children()
mock_rel = {
    'tag': 'v0.3.1',
    'version': '0.3.1',
    'title': 'LanDesk v0.3.1 Test',
    'body': 'ฟีเจอร์ใหม่ทดสอบระบบอัปเดตสไตล์ FanControl',
    'html_url': 'https://github.com/example/landesk',
    'download_url': None,
    'asset_name': 'test.zip',
    'asset_size': 1024,
}
show_update_dialog(root, mock_rel, '0.3.0')
root.update()
update_dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
print('Update dialog UI:', update_dlg.winfo_width(), update_dlg.winfo_height())
update_dlg.destroy()

app.open_installer()
root.update()
inst_dlg = [w for w in root.winfo_children() if isinstance(w, tk.Toplevel)][0]
print('Installer dialog UI:', inst_dlg.winfo_width(), inst_dlg.winfo_height())
inst_dlg.destroy()

print('Address Book UI: OK')
app.close()

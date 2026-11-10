"""Chunked file transfer. Peer supplied paths are never opened or executed."""
import base64
import collections
import hashlib
import os
import re
import secrets
import shutil
import zipfile
from pathlib import Path

CHUNK = 192 * 1024
MAX_FILE = 1024 * 1024 * 1024
MAX_SESSION = 5 * 1024 * 1024 * 1024
MAX_FOLDER_FILES = 5000


def safe_name(name):
    if (not isinstance(name, str) or not name or len(name) > 160 or name in ('.', '..')
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((' ', '.'))
            or name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *('COM'+str(i) for i in range(10)), *('LPT'+str(i) for i in range(10))}):
        raise ValueError('ชื่อไฟล์ไม่ปลอดภัย')
    return name


def cleanup_partials(directory):
    directory = Path(directory)
    if not directory.exists():
        return
    for partial in list(directory.rglob('*.partial')):
        try:
            partial.unlink(missing_ok=True)
            if partial.parent != directory and not any(partial.parent.iterdir()):
                partial.parent.rmdir()
        except OSError:
            pass
    staging = directory / '.staging'
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)


def pack_folder_to_zip(folder_path, staging_dir):
    folder = Path(folder_path).resolve()
    if not folder.is_dir() or folder.is_symlink():
        raise ValueError('โฟลเดอร์ไม่ถูกต้อง')
    zip_name = safe_name(folder.name + '.zip')
    staging_dir = Path(staging_dir) / secrets.token_hex(8)
    staging_dir.mkdir(parents=True, exist_ok=True)
    zip_path = staging_dir / zip_name
    total_bytes = 0
    file_count = 0
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(folder.rglob('*')):
                if item.is_symlink():
                    continue
                if item.is_file():
                    file_count += 1
                    if file_count > MAX_FOLDER_FILES:
                        raise ValueError('โฟลเดอร์มีไฟล์เกิน 2,000 ไฟล์')
                    stat_size = item.stat().st_size
                    total_bytes += stat_size
                    if total_bytes > MAX_FILE:
                        raise ValueError('ขนาดโฟลเดอร์รวมเกิน 100 MB')
                    rel = item.relative_to(folder.parent)
                    for part in rel.parts:
                        safe_name(part)
                    archive.write(item, str(rel))
        if zip_path.stat().st_size > MAX_FILE:
            raise ValueError('ไฟล์ ZIP ของโฟลเดอร์ใหญ่เกิน 100 MB')
        return zip_path
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


class TransferPeer:
    def __init__(self, directory, complete=lambda path: None, notify=lambda text: None,
                 on_batch_sent=lambda auto_paste: None):
        self.directory = Path(directory)
        self.complete, self.notify = complete, notify
        self.on_batch_sent = on_batch_sent
        self.pending = collections.deque()
        self.replies = collections.deque()
        self.outgoing = self.incoming = None
        self.total = 0
        self.count = 0
        self.progress = 0
        self.received_batch = None
        self.cancelled_ids = collections.deque(maxlen=32)
        cleanup_partials(self.directory)

    def queue_files(self, paths, auto_paste=False):
        resolved = [Path(p).resolve() for p in paths]
        if len(resolved) + len(self.pending) > 500:
            raise ValueError('ส่งได้ครั้งละไม่เกิน 500 รายการ')
        staged_items = []
        try:
            for path in resolved:
                if path.is_symlink():
                    raise ValueError('ไม่รองรับไฟล์ลิงก์ (Symlink)')
                if path.is_dir():
                    self.notify(f'กำลังบีบอัดโฟลเดอร์ {path.name} เป็น ZIP…')
                    zip_path = pack_folder_to_zip(path, self.directory / '.staging')
                    staged_items.append((zip_path, True))
                elif path.is_file():
                    safe_name(path.name)
                    if path.stat().st_size > MAX_FILE:
                        raise ValueError('รองรับไฟล์ปกติไม่เกิน 100 MB ต่อไฟล์')
                    staged_items.append((path, False))
                else:
                    raise ValueError('ไม่พบไฟล์หรือโฟลเดอร์ที่เลือก')
        except Exception:
            for item_path, is_temp in staged_items:
                if is_temp:
                    shutil.rmtree(item_path.parent, ignore_errors=True)
            raise
        batch = secrets.token_hex(12)
        for item_path, is_temp in staged_items:
            self.pending.append((item_path, batch, is_temp, bool(auto_paste)))

    def next_message(self):
        if self.replies:
            return self.replies.popleft()
        if not self.outgoing and self.pending:
            entry = self.pending.popleft()
            if len(entry) == 2:
                path, batch = entry
                is_temp = auto_paste = False
            else:
                path, batch, is_temp, auto_paste = entry
            handle = path.open('rb')
            size = os.fstat(handle.fileno()).st_size
            if size > MAX_FILE:
                handle.close()
                if is_temp:
                    shutil.rmtree(path.parent, ignore_errors=True)
                raise ValueError('ไฟล์ใหญ่เกิน 100 MB')
            self.progress = 0 if size > 0 else 100
            self.outgoing = {'id': secrets.token_hex(12), 'file': handle, 'size': size,
                             'sent': 0, 'hash': hashlib.sha256(), 'ready': False, 'phase': 'begin',
                             'name': path.name, 'path': path, 'batch': batch,
                             'is_temp': is_temp, 'auto_paste': auto_paste}
            return {'op': 'begin', 'id': self.outgoing['id'], 'name': path.name, 'size': size, 'batch': batch}
        out = self.outgoing
        if not out or not out['ready']:
            return None
        out['ready'] = False
        data = out['file'].read(min(CHUNK, out['size'] - out['sent']))
        if data:
            out['hash'].update(data)
            offset = out['sent']
            out['sent'] += len(data)
            out['phase'] = 'data'
            pct = out['sent'] * 100 // max(1, out['size'])
            self.progress = pct
            self.notify(f"ส่ง {out['name']} · {pct}%")
            return {'op': 'data', 'id': out['id'], 'offset': offset, 'data': base64.b64encode(data).decode()}
        if out['sent'] != out['size']:
            raise ValueError('ไฟล์ถูกเปลี่ยนระหว่างส่ง')
        out['phase'] = 'end'
        self.progress = 100
        return {'op': 'end', 'id': out['id'], 'sha256': out['hash'].hexdigest()}

    def receive(self, msg):
        if not isinstance(msg, dict) or not re.fullmatch('[a-f0-9]{24}', str(msg.get('id', ''))):
            raise ValueError('ข้อมูลโอนไฟล์ไม่ถูกต้อง')
        op, identity = msg.get('op'), msg['id']
        if op == 'cancel':
            self.abort(notify_peer=False)
            self.notify('ยกเลิกการโอนไฟล์แล้ว')
            return
        if identity in self.cancelled_ids:
            return
        if op == 'ack':
            out = self.outgoing
            if not out or out['id'] != identity or msg.get('offset') != out['sent']:
                raise ValueError('ลำดับยืนยันไฟล์ผิด')
            if out['phase'] == 'end':
                out['file'].close()
                if out.get('is_temp'):
                    shutil.rmtree(out['path'].parent, ignore_errors=True)
                batch = out.get('batch')
                auto_paste = out.get('auto_paste', False)
                name = out['name']
                self.outgoing = None
                self.progress = 100
                more_in_batch = any(len(item) >= 2 and item[1] == batch for item in self.pending)
                self.notify('ส่งเสร็จ: ' + name)
                if not more_in_batch:
                    self.on_batch_sent(auto_paste)
            else:
                out['ready'] = True
            return
        if op == 'begin':
            name, size = safe_name(msg.get('name')), msg.get('size')
            batch = msg.get('batch', identity)
            if not isinstance(batch, str) or not re.fullmatch('[a-f0-9]{24}', batch):
                raise ValueError('รหัสชุดไฟล์ไม่ถูกต้อง')
            if self.incoming or type(size) is not int or not 0 <= size <= MAX_FILE:
                raise ValueError('ขนาดไฟล์หรือสถานะไม่ถูกต้อง')
            if self.total + size > MAX_SESSION or self.count >= 100:
                raise ValueError('เกินโควตารับไฟล์ต่อ session (1 GB / 100 ไฟล์)')
            self.directory.mkdir(parents=True, exist_ok=True)
            if shutil.disk_usage(self.directory).free < size + 16 * 1024 * 1024:
                raise ValueError('พื้นที่รับไฟล์ไม่เพียงพอ')
            # Locally chosen subdirectory prevents overwrite and path injection.
            folder = self.directory / secrets.token_hex(12)
            folder.mkdir()
            path = folder / name
            partial = folder / (name + '.partial')
            self.progress = 0 if size > 0 else 100
            self.incoming = {'id': identity, 'path': path, 'partial': partial,
                             'file': partial.open('xb'), 'size': size, 'received': 0, 'hash': hashlib.sha256(), 'batch': batch}
            self.total += size
            self.count += 1
        else:
            inc = self.incoming
            if not inc or inc['id'] != identity:
                raise ValueError('ไม่มีรายการรับไฟล์นี้')
            if op == 'data':
                encoded = msg.get('data')
                if not isinstance(encoded, str) or len(encoded) > (CHUNK * 4 // 3 + 4):
                    raise ValueError('ก้อนข้อมูลใหญ่เกินกำหนด')
                data = base64.b64decode(encoded, validate=True)
                if (not data or len(data) > CHUNK or msg.get('offset') != inc['received']
                        or inc['received'] + len(data) > inc['size']):
                    raise ValueError('ลำดับหรือขนาดข้อมูลไฟล์ผิด')
                inc['file'].write(data)
                inc['hash'].update(data)
                inc['received'] += len(data)
                pct = inc['received'] * 100 // max(1, inc['size'])
                self.progress = pct
                self.notify(f"รับ {inc['path'].name} · {pct}%")
            elif op == 'end':
                if inc['received'] != inc['size'] or msg.get('sha256') != inc['hash'].hexdigest():
                    raise ValueError('SHA-256 ของไฟล์ไม่ตรง')
                inc['file'].close()
                inc['partial'].rename(inc['path'])
                # Mark network-origin files; do not bypass Windows attachment warnings.
                if os.name == 'nt':
                    Path(str(inc['path']) + ':Zone.Identifier').write_text('[ZoneTransfer]\nZoneId=3\n')
                self.received_batch = inc['batch']
                self.progress = 100
                self.complete(inc['path'])
                self.notify('รับเสร็จ: ' + inc['path'].name)
            else:
                raise ValueError('คำสั่งโอนไฟล์ไม่รองรับ')
        inc = self.incoming
        self.replies.append({'op': 'ack', 'id': identity, 'offset': inc['received']})
        if op == 'end':
            self.incoming = None

    def abort(self, notify_peer=True):
        active_id = None
        while self.pending:
            entry = self.pending.popleft()
            if len(entry) >= 3 and entry[2]:
                shutil.rmtree(entry[0].parent, ignore_errors=True)
        self.replies.clear()
        if self.outgoing:
            active_id = self.outgoing['id']
            self.cancelled_ids.append(active_id)
            self.outgoing['file'].close()
            if self.outgoing.get('is_temp'):
                shutil.rmtree(self.outgoing['path'].parent, ignore_errors=True)
            self.outgoing = None
        if self.incoming:
            active_id = active_id or self.incoming['id']
            self.cancelled_ids.append(self.incoming['id'])
            self.incoming['file'].close()
            self.incoming['partial'].unlink(missing_ok=True)
            try:
                if not any(self.incoming['partial'].parent.iterdir()):
                    self.incoming['partial'].parent.rmdir()
            except OSError:
                pass
            self.incoming = None
        self.progress = 0
        if notify_peer:
            self.replies.append({'op': 'cancel', 'id': active_id or secrets.token_hex(12)})

    def cancel(self):
        self.abort(notify_peer=False)

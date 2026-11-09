"""Bounded, JSON-only LAN protocol. No pickle or remote code execution."""
import hashlib
import hmac
import ipaddress
import json
import socket
import ssl
import struct
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_PACKET = 48 * 1024 * 1024
NETWORKS = tuple(ipaddress.ip_network(n) for n in
                 ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8'))


def lan_address(value):
    address = ipaddress.ip_address(value)
    if not any(address in network for network in NETWORKS):
        raise ValueError('ใช้ IPv4 ภายในองค์กร เช่น 192.168.1.20 เท่านั้น')
    return str(address)


def read_exact(sock, size):
    data = bytearray()
    while len(data) < size:
        part = sock.recv(size - len(data))
        if not part:
            raise ConnectionError('การเชื่อมต่อสิ้นสุด')
        data.extend(part)
    return bytes(data)


def send_packet(sock, data):
    if not 0 < len(data) <= MAX_PACKET:
        raise ValueError('ขนาดข้อมูลไม่ถูกต้อง')
    sock.sendall(struct.pack('!I', len(data)) + data)


def receive_packet(sock):
    size, = struct.unpack('!I', read_exact(sock, 4))
    if not 0 < size <= MAX_PACKET:
        raise ValueError('ขนาดข้อมูลไม่ถูกต้อง')
    return read_exact(sock, size)


def send_json(sock, data):
    send_packet(sock, json.dumps(data, ensure_ascii=False).encode('utf-8'))


def receive_json(sock, limit=65536):
    size, = struct.unpack('!I', read_exact(sock, 4))
    if not 0 < size <= limit:
        raise ValueError('ขนาดคำสั่งไม่ถูกต้อง')
    obj = json.loads(read_exact(sock, size))
    if not isinstance(obj, dict):
        raise ValueError('รูปแบบคำสั่งไม่ถูกต้อง')
    return obj


def make_identity(directory=None, settings=None):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    saved = settings.snapshot().get('identity') if settings else None
    if saved:
        cert = x509.load_pem_x509_certificate(saved['cert'].encode())
        key = serialization.load_pem_private_key(saved['key'].encode(), password=None)
    else:
        key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'LanDesk session')])
    now = datetime.now(timezone.utc)
    if not saved:
        cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(now + timedelta(days=3650)).sign(key, hashes.SHA256()))
        if settings:
            settings.save(identity={'cert': cert.public_bytes(serialization.Encoding.PEM).decode(),
                                    'key': key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                                             serialization.NoEncryption()).decode()})
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    identity_path = None
    target_dir = directory or getattr(settings, 'directory', None)
    if target_dir:
        Path(target_dir).mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.NamedTemporaryFile(prefix='landesk-', suffix='.pem', dir=target_dir, delete=False) as identity:
            identity_path = Path(identity.name)
            identity.write(cert.public_bytes(serialization.Encoding.PEM))
            # Even the short-lived on-disk key is encrypted with a fresh in-memory secret.
            import secrets
            key_password = secrets.token_bytes(32)
            identity.write(key.private_bytes(serialization.Encoding.PEM,
                           serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(key_password)))
        context.load_cert_chain(identity_path, password=key_password)
    finally:
        if identity_path:
            import time
            for _ in range(5):
                try:
                    identity_path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    time.sleep(0.02)
    fingerprint = cert.fingerprint(hashes.SHA256()).hex().upper()
    # Loaded into SSLContext; do not retain the private key on disk.
    return context, fingerprint


def probe_host(host, port, timeout=1.2):
    try:
        host = lan_address(host)
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except Exception:
        return False


def connect_pinned(host, port, fingerprint='', trust=None):
    host = lan_address(host)
    pin = fingerprint.replace(':', '').replace(' ', '').strip().upper()
    if pin and (len(pin) != 64 or any(c not in '0123456789ABCDEF' for c in pin)):
        raise ValueError('คัดลอกลายนิ้วมือ SHA-256 ทั้ง 64 ตัวจากเครื่องปลายทาง')
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.check_hostname = False
    # Trust is an explicit SHA-256 pin, checked BEFORE sending the password.
    context.verify_mode = ssl.CERT_NONE
    raw = socket.create_connection((host, port), timeout=10)
    sock = None
    try:
        sock = context.wrap_socket(raw, server_hostname=host)
        actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest().upper()
        if pin and not hmac.compare_digest(actual, pin):
            sock.close()
            raise ValueError('ลายนิ้วมือไม่ตรงกับเครื่องปลายทาง — ยกเลิกการเชื่อมต่อ')
        if not pin and (trust is None or not trust(actual)):
            sock.close()
            raise ValueError('ยังไม่ได้ยืนยันตัวตนเครื่องปลายทาง')
        return sock
    except Exception:
        if sock:
            sock.close()
        raw.close()
        raise

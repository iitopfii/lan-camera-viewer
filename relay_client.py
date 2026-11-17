"""เข้าจากนอกบ้านผ่าน cam.hypebizsoft.com (ทางผ่าน)

โปรแกรมนี้ต่อออกไปหาเซิร์ฟเวอร์กลางแล้วค้างไว้ (ไม่ต้องเปิดพอร์ตที่เราเตอร์)
ผู้ชมเปิด https://ชื่อบัญชี.cam.hypebizsoft.com → เซิร์ฟเวอร์กลางส่ง "ไบต์ที่ยังเข้ารหัสอยู่" มาทางท่อนี้
→ เราส่งต่อเข้าเว็บเซิร์ฟเวอร์ในเครื่อง ซึ่งถอดรหัสด้วยกุญแจที่สร้างและเก็บในเครื่องนี้เท่านั้น
เซิร์ฟเวอร์กลางจึงเห็นแค่ข้อมูลที่เข้ารหัส – ดูภาพ/รหัสผ่านไม่ได้
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import json
import ssl
import struct
import threading
import urllib.request
from pathlib import Path

from app_info import APP_NAME, APP_VERSION, DATA_DIR

DEFAULT_SERVER = "https://cam.hypebizsoft.com"
RELAY_DOMAIN = DEFAULT_SERVER.split("://", 1)[1]  # ซับโดเมนของแต่ละบัญชีอยู่ใต้ชื่อนี้
OPEN, DATA, CLOSE, PAUSE, RESUME, CTRL = 1, 2, 3, 4, 5, 10
RENEW_DAYS = 30

# พอร์ตต้นทาง (ในเครื่อง) ของการเชื่อมต่อที่มาจากท่อ → IP จริงของผู้ชม (ให้ระบบกันเดารหัสนับแยกคน)
relay_peer_ips: dict[int, str] = {}


def _frame(t: int, sid: int, payload: bytes = b"") -> bytes:
    return struct.pack(">BI", t, sid) + payload


def cert_paths() -> tuple[Path, Path]:
    return DATA_DIR / "relay_cert.pem", DATA_DIR / "relay_key.pem"


def cert_valid_for(host: str) -> bool:
    """มีใบรับรองของชื่อนี้ และยังไม่ใกล้หมดอายุ"""
    cert, key = cert_paths()
    if not (cert.exists() and key.exists()):
        return False
    try:
        from cryptography import x509  # noqa: PLC0415
        c = x509.load_pem_x509_certificate(cert.read_bytes())
        names = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        left = c.not_valid_after_utc - _dt.datetime.now(_dt.timezone.utc)
        return host in names and left.days > RENEW_DAYS
    except Exception:  # noqa: BLE001
        return False


def make_csr(host: str) -> str:
    """สร้างกุญแจใหม่ (เก็บในเครื่องนี้) + คำขอใบรับรองที่มีแค่กุญแจสาธารณะ"""
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415
    key = ec.generate_private_key(ec.SECP256R1())
    _cert, key_path = cert_paths()
    pending = key_path.with_suffix(".pending.pem")
    pending.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    csr = (x509.CertificateSigningRequestBuilder()
           .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
           .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
           .sign(key, hashes.SHA256()))
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def install_cert(chain_pem: str):
    cert, key = cert_paths()
    pending = key.with_suffix(".pending.pem")
    if pending.exists():
        pending.replace(key)
    cert.write_text(chain_pem)


def pair(server: str, code: str, name: str) -> dict:
    """ใส่รหัสจับคู่จากหน้าเว็บ → ได้ token ประจำเครื่อง"""
    req = urllib.request.Request(
        server.rstrip("/") + "/api/pair",
        data=json.dumps({"code": code, "name": name}).encode(),
        headers={"Content-Type": "application/json", "User-Agent": f"{APP_NAME.replace(' ', '')}/{APP_VERSION}"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (URL ของบริการเราเอง)
            return json.load(r)
    except urllib.error.HTTPError as e:
        try:
            msg = json.load(e).get("statusMessage") or str(e)
        except Exception:  # noqa: BLE001
            msg = str(e)
        raise RuntimeError(msg) from None


class RelayClient:
    """ท่อไปหาเซิร์ฟเวอร์กลาง – ทำงานในเธรดของตัวเอง (asyncio) ต่อใหม่เองเมื่อหลุด"""

    def __init__(self, server: str, token: str, local_port: int, on_status=None, on_cert=None, cafile: str | None = None):
        self.server = server.rstrip("/")
        self.token = token
        self.local_port = local_port
        self.on_status = on_status or (lambda s, ok: None)
        self.on_cert = on_cert or (lambda host: None)
        self.cafile = cafile  # ใช้ตอนทดสอบเท่านั้น
        self.host = ""
        self.connected = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="relay", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        if self._loop:
            self._loop.call_soon_threadsafe(lambda: [t.cancel() for t in asyncio.all_tasks(self._loop)])

    def _status(self, text, ok=False):
        self.connected = ok
        try:
            self.on_status(text, ok)
        except Exception:  # noqa: BLE001
            pass

    # ----
    def _run(self):
        self._loop = asyncio.new_event_loop()
        try:
            self._loop.run_until_complete(self._main())
        except BaseException:  # noqa: BLE001
            pass
        finally:
            self._status("ปิดการเข้าจากนอกบ้านแล้ว")

    async def _main(self):
        import websockets  # noqa: PLC0415
        delay = 2
        url = ("wss://" if self.server.startswith("https://") else "ws://") + self.server.split("://", 1)[1] + "/_tunnel"
        ssl_ctx = None
        if url.startswith("wss://"):
            ssl_ctx = ssl.create_default_context(cafile=self.cafile) if self.cafile else ssl.create_default_context()
        while not self._stop:
            self._status("กำลังเชื่อมต่อ…")
            try:
                async with websockets.connect(url, ssl=ssl_ctx, max_size=None, ping_interval=20, ping_timeout=20,
                                              additional_headers={"Authorization": f"Bearer {self.token}"},
                                              user_agent_header=f"{APP_NAME.replace(' ', '')}/{APP_VERSION}") as ws:
                    delay = 2
                    await self._serve(ws)
            except asyncio.CancelledError:
                return
            except Exception as e:  # noqa: BLE001
                text = str(e)
                if "401" in text:
                    self._status("เซิร์ฟเวอร์ไม่รับเครื่องนี้ (ยกเลิกการจับคู่แล้ว?) – จับคู่ใหม่ในหน้าต่างดูผ่านเว็บ")
                    return  # token ใช้ไม่ได้แล้ว ไม่ต้องลองซ้ำ
                self._status(f"หลุดการเชื่อมต่อ ({text[:60]}) – จะลองใหม่ใน {delay} วินาที")
            if self._stop:
                return
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _serve(self, ws):
        streams: dict[int, tuple] = {}  # id → (writer, resume_event, task)

        async def pump(sid, reader, resume: asyncio.Event):
            try:
                while True:
                    await resume.wait()
                    chunk = await reader.read(65536)
                    if not chunk:
                        break
                    await ws.send(_frame(DATA, sid, chunk))
            except (asyncio.CancelledError, ConnectionError, OSError):
                pass
            finally:
                if sid in streams:
                    streams.pop(sid, None)
                    try:
                        await ws.send(_frame(CLOSE, sid))
                    except Exception:  # noqa: BLE001
                        pass

        async def open_stream(sid, ip):
            try:
                reader, writer = await asyncio.open_connection("127.0.0.1", self.local_port)
            except OSError:
                await ws.send(_frame(CLOSE, sid))
                return
            relay_peer_ips[writer.get_extra_info("sockname")[1]] = ip
            resume = asyncio.Event()
            resume.set()
            task = asyncio.create_task(pump(sid, reader, resume))
            streams[sid] = (writer, resume, task)

        pending_open: dict[int, asyncio.Task] = {}
        try:
            async for msg in ws:
                if not isinstance(msg, (bytes, bytearray)) or len(msg) < 5:
                    continue
                t, sid = struct.unpack(">BI", msg[:5])
                body = bytes(msg[5:])
                if t == CTRL:
                    await self._ctrl(ws, json.loads(body or b"{}"))
                elif t == OPEN:
                    ip = (json.loads(body or b"{}") or {}).get("ip", "")
                    pending_open[sid] = asyncio.create_task(open_stream(sid, ip))
                elif t == DATA:
                    if sid in pending_open:
                        await pending_open.pop(sid)
                    s = streams.get(sid)
                    if s:
                        s[0].write(body)
                        await s[0].drain()
                elif t == CLOSE:
                    pending_open.pop(sid, None)
                    s = streams.pop(sid, None)
                    if s:
                        s[2].cancel()
                        s[0].close()
                elif t == PAUSE:
                    s = streams.get(sid)
                    if s:
                        s[1].clear()
                elif t == RESUME:
                    s = streams.get(sid)
                    if s:
                        s[1].set()
        finally:
            for w, _r, task in streams.values():
                task.cancel()
                w.close()
            self._status("หลุดการเชื่อมต่อ – กำลังต่อใหม่…")

    async def _ctrl(self, ws, msg):
        t = msg.get("t")
        if t == "hello":
            self.host = msg.get("host", "")
            if cert_valid_for(self.host):
                self.on_cert(self.host)
                self._status(f"เชื่อมต่อแล้ว – https://{self.host}", ok=True)
            else:
                self._status("กำลังขอใบรับรอง HTTPS (กุญแจสร้างในเครื่องนี้)…", ok=True)
                csr = await asyncio.to_thread(make_csr, self.host)
                await ws.send(_frame(CTRL, 0, json.dumps({"t": "cert", "csr": csr}).encode()))
        elif t == "cert":
            install_cert(msg.get("chain", ""))
            self.on_cert(self.host)
            self._status(f"เชื่อมต่อแล้ว – https://{self.host}", ok=True)
        elif t == "cert_error":
            self._status(f"ขอใบรับรองไม่สำเร็จ: {msg.get('error', '')}", ok=False)

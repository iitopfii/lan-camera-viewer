"""ดูกล้องผ่านเบราว์เซอร์ (มือถือ / นอกบ้าน)

- ใช้สตรีมที่โปรแกรมเปิดอยู่แล้ว (ไม่ต่อกล้องซ้ำ) → ส่งเป็นภาพ JPEG ทีละภาพ ไม่ติดข้อจำกัด 6 การเชื่อมต่อของเบราว์เซอร์
- HTTPS เสมอ (สร้างใบรับรองให้เองครั้งแรก) + ต้องล็อกอิน + จำกัดการเดารหัส
- เปิดพอร์ตออกอินเทอร์เน็ตแค่พอร์ตนี้พอร์ตเดียว – กล้องยังอยู่ใน LAN
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import ipaddress
import json
import queue
import secrets
import socket
import ssl
import threading
import time
from http import cookies
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt

from app_info import APP_NAME, APP_VERSION, DATA_DIR

DEFAULT_PORT = 8443
SESSION_HOURS = 12
MAX_FAILS = 5            # ใส่รหัสผิดเกินนี้ (ต่อ IP) → ล็อก
LOCK_MINUTES = 15
WEB_WIDTH = 1280         # ย่อภาพไม่ให้กว้างเกินนี้ ประหยัดเน็ต
JPEG_QUALITY = 70
COOKIE = "lcv_session"


# ---------------------------------------------------------------- รหัสผ่าน
PBKDF2_ITER = 600_000    # รหัสที่ตั้งใหม่ใช้ค่านี้ · รหัสเดิม (ไม่มี "iter") = 200,000 ยังใช้ได้ตามเดิม
MAX_PW = 200


def hash_password(pw: str, salt: str | None = None, iterations: int = PBKDF2_ITER) -> dict:
    salt = salt or secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt), iterations).hex()
    return {"salt": salt, "hash": h, "iter": iterations}


def check_password(pw: str, rec: dict) -> bool:
    if not rec or not rec.get("salt") or not rec.get("hash") or len(pw) > MAX_PW:
        return False
    try:
        iterations = min(max(int(rec.get("iter") or 200_000), 1), 5_000_000)
        return hmac.compare_digest(hash_password(pw, rec["salt"], iterations)["hash"], str(rec["hash"]))
    except (ValueError, TypeError):
        return False


# ---------------------------------------------------------------- ใบรับรอง HTTPS
def lan_ips() -> list[str]:
    ips, primary = set(), ""
    try:  # IP ที่ใช้ออกเน็ตจริง (ไม่ได้ส่งข้อมูลออกไป แค่ถามระบบว่าจะใช้การ์ดไหน)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            primary = s.getsockname()[0]
            ips.add(primary)
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    ok = [ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254.")]
    # IP หลัก (ที่ใช้ออกเน็ต) ขึ้นก่อน ตามด้วย 192.168.x / 10.x / อื่นๆ (เช่น การ์ดเสมือนของ WSL)
    return sorted(ok, key=lambda ip: (ip != primary, not ip.startswith("192.168."), ip))


def ensure_cert() -> tuple[Path, Path]:
    """ใบรับรองแบบเซ็นเอง (อายุ 10 ปี) เก็บใน AppData – เบราว์เซอร์จะเตือนครั้งแรก ให้กดยอมรับ"""
    cert, key = DATA_DIR / "web_cert.pem", DATA_DIR / "web_key.pem"
    if cert.exists() and key.exists():
        return cert, key
    from cryptography import x509  # noqa: PLC0415
    from cryptography.hazmat.primitives import hashes, serialization  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric import ec  # noqa: PLC0415
    from cryptography.x509.oid import NameOID  # noqa: PLC0415

    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, APP_NAME)])
    alt = [x509.DNSName("localhost"), x509.DNSName(socket.gethostname())]
    for ip in ["127.0.0.1", *lan_ips()]:
        alt.append(x509.IPAddress(ipaddress.ip_address(ip)))
    now = _dt.datetime.now(_dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number())
         .not_valid_before(now - _dt.timedelta(days=1)).not_valid_after(now + _dt.timedelta(days=3650))
         .add_extension(x509.SubjectAlternativeName(alt), critical=False)
         .sign(k, hashes.SHA256()))
    key.write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption()))
    cert.write_bytes(c.public_bytes(serialization.Encoding.PEM))
    return cert, key


# ---------------------------------------------------------------- แหล่งภาพ
class FrameSource:
    """ดึงภาพจากสตรีมของหน้าต่างหลัก แปลงเป็น JPEG (แคชไว้ ผู้ดูหลายคนใช้ภาพเดียวกัน)"""

    def __init__(self, get_window):
        self.get_window = get_window
        self._cache: dict[int, tuple] = {}
        self._lock = threading.Lock()
        self._ptz: dict[str, object] = {}
        self._ptz_lock = threading.Lock()

    def cams(self) -> list[dict]:
        win = self.get_window()
        if win is None:
            return []
        out = []
        for i, (tile, cam) in enumerate(win.tile_cams):
            w = tile.worker
            out.append({"id": i, "name": cam["name"], "device": cam.get("device", cam["name"]),
                        "ptz": bool(cam.get("ptz")), "crop": tile.crop or "",
                        "codec": (w.codec if w is not None else "") or "",
                        "live": bool(w is not None and w.latest_image is not None),
                        "status": getattr(w, "last_status", "") if w else "ยังไม่เชื่อมต่อ"})
        return out

    def _entry(self, cid: int):
        win = self.get_window()
        if win is None or not (0 <= cid < len(win.tile_cams)):
            return None, None
        tile, cam = win.tile_cams[cid]
        return tile, cam

    def frame(self, cid: int, since: int = -1, wait: float = 1.5) -> tuple[int, bytes] | None:
        """คืน (เลขภาพ, JPEG) – ถ้าภาพยังเป็นภาพเดิม รอภาพใหม่สูงสุด wait วินาที"""
        tile, _cam = self._entry(cid)
        if tile is None:
            return None
        end = time.monotonic() + wait
        while True:
            w = tile.worker
            if w is not None:
                w.web_until = time.monotonic() + 4  # บอกหน้าต่างหลักว่ามีคนดูผ่านเว็บ → ถอดรหัสทุกภาพ
                img, fid = w.latest_image, w.frame_id
                if img is not None and (fid != since or time.monotonic() >= end):
                    return fid, self._jpeg(cid, w, fid, img, tile.crop)
            if time.monotonic() >= end:
                return None
            time.sleep(0.04)

    def _jpeg(self, cid, worker, fid, img, crop) -> bytes:
        key = (id(worker), fid)
        with self._lock:
            hit = self._cache.get(cid)
            if hit and hit[0] == key:
                return hit[1]
        if crop == "top":
            img = img.copy(0, 0, img.width(), img.height() // 2)
        elif crop == "bottom":
            img = img.copy(0, img.height() // 2, img.width(), img.height() - img.height() // 2)
        if img.width() > WEB_WIDTH:
            img = img.scaledToWidth(WEB_WIDTH, Qt.TransformationMode.SmoothTransformation)
        ba = QByteArray()
        buf = QBuffer(ba)
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "JPG", JPEG_QUALITY)
        buf.close()
        data = bytes(ba)
        with self._lock:
            self._cache[cid] = (key, data)
        return data

    def video(self, cid: int, quality: str, write, alive) -> str:
        """ส่งวิดีโอจริง (MP4 แบบแบ่งท่อน) ให้เบราว์เซอร์ – ห่อแพ็กเก็ตจากกล้องใหม่ ไม่เข้ารหัสใหม่
        write(bytes) ส่งข้อมูลออก · alive() = ผู้ดูยังอยู่ไหม · คืนเหตุผลที่จบ"""
        import av  # noqa: PLC0415
        tile, _cam = self._entry(cid)
        win = self.get_window()
        if tile is None or win is None:
            return "no camera"
        hd = quality == "hd"
        if not hd:
            tile.web_hd_until = 0  # ขอ SD → เลิกบังคับ HD ทันที
        q = vst = worker = None
        end = time.monotonic() + 12
        while time.monotonic() < end:  # รอให้ช่องนี้สลับไปสตรีมที่ขอ (HD/SD) และต่อกล้องได้
            now = time.monotonic()
            if hd:
                tile.web_hd_until = now + 6
            w = tile.worker
            if w is not None:
                w.web_until = now + 6
                want_url = win.url_for(tile, "hd") if hd else None
                if (want_url is None or w.url == want_url) and w.vstream is not None:
                    q, vst = w.subscribe()
                    if q is not None:
                        worker = w
                        break
            time.sleep(0.1)
        if q is None:
            return "timeout"
        out = None
        try:
            sink = _Sink(write)
            out = av.open(sink, "w", format="mp4", buffer_size=8192,
                          options={"movflags": "empty_moov+default_base_moof+frag_every_frame"})
            ost = out.add_stream_from_template(vst)
            if worker.codec == "hevc":
                try:
                    ost.codec_tag = "hvc1"  # Safari / Chrome ต้องการ hvc1
                except Exception:  # noqa: BLE001
                    pass
            t0 = None
            last = -1
            started = False
            while alive():
                now = time.monotonic()
                worker.web_until = now + 6
                if hd:
                    tile.web_hd_until = now + 6
                if tile.worker is not worker:
                    return "stream switched"
                if getattr(q, "overflow", False):
                    return "client too slow"
                try:
                    item = q.get(timeout=5)
                except queue.Empty:
                    continue
                if item is None:
                    return "camera disconnected"
                data, key, arrived = item
                if not started:
                    if not key:
                        continue  # วิดีโอต้องเริ่มที่ภาพหลัก
                    started = True
                    t0 = arrived
                ts = int((arrived - t0) / ost.time_base)
                if ts <= last:
                    ts = last + 1
                last = ts
                pkt = av.Packet(data)
                pkt.pts = pkt.dts = ts
                pkt.time_base = ost.time_base
                pkt.is_keyframe = key
                pkt.stream = ost
                out.mux(pkt)
            return "viewer left"
        except (BrokenPipeError, ConnectionError, ssl.SSLError, OSError):
            return "viewer left"
        finally:
            worker.unsubscribe(q)
            if out is not None:
                try:
                    out.close()
                except Exception:  # noqa: BLE001
                    pass

    def ptz(self, cid: int, pan: float, tilt: float, stop: bool):
        _tile, cam = self._entry(cid)
        if cam is None or not cam.get("ptz"):
            raise ValueError("กล้องนี้หมุนไม่ได้")
        from onvif_ptz import OnvifPTZ  # noqa: PLC0415
        with self._ptz_lock:
            p = self._ptz.get(cam["ip"])
            if p is None:
                p = OnvifPTZ(cam["ip"], cam.get("username", ""), cam.get("password", ""),
                             int(cam.get("onvif_port") or 2020)).connect()
                self._ptz[cam["ip"]] = p
            if stop:
                p.stop()
            else:
                p.move(max(-1.0, min(1.0, pan)), max(-1.0, min(1.0, tilt)))


class _Sink:
    """ที่รับข้อมูลจากตัวทำ MP4 → ส่งออก HTTP ทันที (ไม่มี seek = ตัวทำ MP4 จะเขียนแบบสตรีม)"""

    def __init__(self, write):
        self._write = write

    def write(self, b):
        if b:
            self._write(bytes(b))
        return len(b)

    def flush(self):
        pass


# ---------------------------------------------------------------- เซิร์ฟเวอร์
GLOBAL_FAILS = 40        # ผิดรวมทุก IP เกินนี้ใน 10 นาที = กำลังถูกเดารหัสจากหลาย IP
GLOBAL_LOCK_MINUTES = 10  # → พักการล็อกอินจาก IP ที่ไม่เคยล็อกอินสำเร็จ (เจ้าของที่เคยเข้าได้ยังเข้าได้)


class _Guard:
    """จำกัดการเดารหัส: ต่อ IP และรวมทั้งระบบ"""

    def __init__(self):
        self.fails: dict[str, list[float]] = {}
        self.locked: dict[str, float] = {}
        self.all_fails: list[float] = []
        self.trusted: dict[str, float] = {}  # IP ที่เคยล็อกอินสำเร็จ → เวลาที่สำเร็จล่าสุด
        self.global_until = 0.0
        self.lock = threading.Lock()

    def blocked(self, ip) -> float:
        now = time.time()
        with self.lock:
            left = max(0.0, self.locked.get(ip, 0) - now)
            if not left and self.global_until > now and now - self.trusted.get(ip, 0) > 30 * 86400:
                left = self.global_until - now
            return left

    def fail(self, ip):
        now = time.time()
        with self.lock:
            lst = [t for t in self.fails.get(ip, []) if now - t < LOCK_MINUTES * 60] + [now]
            self.fails[ip] = lst
            self.all_fails = [t for t in self.all_fails if now - t < 600] + [now]
            if len(lst) >= MAX_FAILS:
                self.locked[ip] = now + LOCK_MINUTES * 60
                self.fails[ip] = []
            if len(self.all_fails) >= GLOBAL_FAILS:
                self.global_until = now + GLOBAL_LOCK_MINUTES * 60
            if len(self.fails) > 5000:  # ไม่ให้ตารางโตไม่สิ้นสุดเมื่อถูกยิงจาก IP จำนวนมาก
                self.fails = {k: v for k, v in self.fails.items() if v and now - v[-1] < LOCK_MINUTES * 60}
                self.locked = {k: v for k, v in self.locked.items() if v > now}
            n = len(self.all_fails)
        time.sleep(1.0 + min(n, 30) * 0.2)  # ยิ่งผิดเยอะ ยิ่งตอบช้า

    def ok(self, ip):
        with self.lock:
            self.fails.pop(ip, None)
            self.trusted[ip] = time.time()


class WebServer:
    def __init__(self, web_cfg: dict, get_window):
        self.cfg = web_cfg
        self.port = int(web_cfg.get("port") or DEFAULT_PORT)
        self.source = FrameSource(get_window)
        self.sessions: dict[str, float] = {}
        self.guard = _Guard()
        self.httpd: ThreadingHTTPServer | None = None
        self.error = ""
        self.relay_host = ""  # ชื่อ ชื่อบัญชี.cam.hypebizsoft.com (ถ้าเปิดเข้าจากนอกบ้าน)
        self.relay_ctx: ssl.SSLContext | None = None

    def set_relay_cert(self, host: str):
        """ใช้ใบรับรองจริงของ ชื่อบัญชี.cam.hypebizsoft.com เมื่อผู้ชมเข้ามาด้วยชื่อนั้น (กุญแจอยู่ในเครื่องนี้)"""
        from relay_client import cert_paths  # noqa: PLC0415
        cert, key = cert_paths()
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert, key)
            self.relay_ctx, self.relay_host = ctx, host.lower()
        except (OSError, ssl.SSLError):
            self.relay_ctx, self.relay_host = None, ""

    def _sni(self, sslsock, server_name, _ctx):
        name = (server_name or "").lower()
        if self.relay_ctx is not None and name == self.relay_host:
            sslsock.context = self.relay_ctx
            return None
        from relay_client import RELAY_DOMAIN  # noqa: PLC0415
        if name.endswith("." + RELAY_DOMAIN):
            # มาทางชื่อ ...cam.hypebizsoft.com แต่ยังไม่มีใบรับรองจริงของชื่อนั้น → ปฏิเสธ
            # (ไม่ส่งใบรับรองที่สร้างเองออกไป ผู้ชมจะได้ไม่ต้องกด "ไปต่อ" กับปลายทางที่ยืนยันไม่ได้)
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        return None

    # ---- lifecycle
    def start(self) -> bool:
        try:
            cert, key = ensure_cert()
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            ctx.load_cert_chain(cert, key)
            ctx.sni_callback = self._sni
            server = self

            class Handler(_Handler):
                srv = server
                tls = ctx

            # ไม่ห่อ TLS ที่ซ็อกเก็ตหลัก: ดูไบต์แรกของแต่ละการเชื่อมต่อก่อน
            # → HTTPS ใช้ TLS ตามปกติ · พิมพ์ http:// มาผิด ก็พาไป https:// ให้ (แทนที่จะเงียบหาย)
            httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
            httpd.daemon_threads = True
            self.httpd = httpd
            threading.Thread(target=httpd.serve_forever, name="web", daemon=True).start()
            return True
        except Exception as e:  # noqa: BLE001
            self.error = str(e)
            self.httpd = None
            return False

    def stop(self):
        if self.httpd:
            threading.Thread(target=self.httpd.shutdown, daemon=True).start()
            self.httpd.server_close()
            self.httpd = None

    def urls(self) -> list[str]:
        return [f"https://{ip}:{self.port}" for ip in lan_ips()]

    # ---- sessions
    def login(self, ip, user, pw) -> str | None:
        # เทียบเป็นไบต์: compare_digest กับข้อความที่มีอักษรไทยจะ error
        ok_user = hmac.compare_digest((user or "").strip().lower().encode(),
                                      (self.cfg.get("username") or "").strip().lower().encode())
        ok_pw = check_password(pw or "", self.cfg.get("password") or {})
        if not (ok_user and ok_pw):
            self.guard.fail(ip)
            return None
        self.guard.ok(ip)
        tok = secrets.token_urlsafe(32)
        now = time.time()
        self.sessions = {k: v for k, v in self.sessions.items() if v > now}
        self.sessions[tok] = now + SESSION_HOURS * 3600
        return tok

    def valid(self, tok) -> bool:
        exp = self.sessions.get(tok or "")
        return bool(exp and exp > time.time())


class _Handler(BaseHTTPRequestHandler):
    srv: WebServer
    server_version = "LANCameraViewer"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def log_message(self, *_a):  # ไม่เขียน log ลงคอนโซล
        pass

    tls: ssl.SSLContext | None = None
    plain = False  # มาแบบ http:// ธรรมดา → ตอบแค่ให้ย้ายไป https://

    def setup(self):
        try:
            self.request.settimeout(20)
            first = self.request.recv(1, socket.MSG_PEEK)
            if first == b"\x16":  # TLS ClientHello
                self.request = self.tls.wrap_socket(self.request, server_side=True)
            else:
                self.plain = True
        except (ssl.SSLError, OSError):
            self.plain = True
        super().setup()

    def handle(self):
        if self.plain:
            try:
                self.raw_requestline = self.rfile.readline(4097)
                if not self.raw_requestline or not self.parse_request():
                    return
                host = (self.headers.get("Host") or "").split("/")[0]
                if not host:
                    return
                self.send_response(301)
                self.send_header("Location", f"https://{host}{self.path if self.path.startswith('/') else '/'}")
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
            except (OSError, ssl.SSLError):
                pass
            return
        super().handle()

    # ---- helpers
    def _client(self):
        ip, port = self.client_address[0], self.client_address[1]
        if ip == "127.0.0.1":  # มาทางท่อ cam.hypebizsoft.com → ใช้ IP จริงของผู้ชม
            from relay_client import relay_peer_ips  # noqa: PLC0415
            return relay_peer_ips.get(port, ip)
        return ip

    def _session(self):
        c = cookies.SimpleCookie(self.headers.get("Cookie", ""))
        return c[COOKIE].value if COOKIE in c else None

    def _authed(self):
        return self.srv.valid(self._session())

    def _page(self, html: str):
        """ส่งหน้า HTML: สคริปต์ในหน้าได้รหัสสุ่ม (nonce) ใหม่ทุกครั้ง – สคริปต์อื่นที่ถูกแทรกเข้ามาจะไม่ทำงาน"""
        nonce = secrets.token_urlsafe(16)
        self._send(200, html.replace("<script>", f'<script nonce="{nonce}">').encode(), nonce=nonce)

    def _send(self, code, body: bytes, ctype="text/html; charset=utf-8", extra=None, nonce=""):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "same-origin")  # no-referrer ทำให้ Origin เป็น null
        self.send_header("Strict-Transport-Security", "max-age=31536000")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' blob:; media-src 'self' blob:; style-src 'self' 'unsafe-inline'; "
                         f"script-src {repr('nonce-' + nonce) if nonce else repr('none')}; object-src 'none'; "
                         "base-uri 'none'; connect-src 'self'; frame-ancestors 'none'; form-action 'self'")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _redirect(self, to, extra=None):
        self._send(303, b"", extra={"Location": to, **(extra or {})})

    def _same_origin(self):
        origin = self.headers.get("Origin")
        if not origin:
            return True
        return urlsplit(origin).netloc == self.headers.get("Host", "")

    # ---- routes
    def do_GET(self):
        u = urlsplit(self.path)
        q = parse_qs(u.query)
        if u.path == "/login":
            return self._page(login_page(q.get("e", [""])[0]))
        if u.path == "/logout":
            self.srv.sessions.pop(self._session() or "", None)
            return self._redirect("/login", {"Set-Cookie": f"{COOKIE}=; Max-Age=0; Path=/; Secure; HttpOnly; SameSite=Strict"})
        if not self._authed():
            if u.path.startswith(("/api/", "/frame/")):
                return self._json(401, {"error": "login"})
            return self._redirect("/login")
        if u.path == "/":
            return self._page(APP_PAGE)
        if u.path == "/api/cams":
            return self._json(200, {"app": APP_NAME, "version": APP_VERSION, "cams": self.srv.source.cams()})
        if u.path.startswith("/video/"):
            try:
                cid = int(u.path.rsplit("/", 1)[1])
            except ValueError:
                return self._json(400, {"error": "bad id"})
            return self._stream_video(cid, q.get("q", ["hd"])[0])
        if u.path.startswith("/frame/"):
            try:
                cid = int(u.path.rsplit("/", 1)[1])
                since = int(q.get("since", ["-1"])[0])
            except ValueError:
                return self._json(400, {"error": "bad id"})
            got = self.srv.source.frame(cid, since)
            if got is None:
                return self._send(204, b"", "image/jpeg")
            fid, data = got
            extra = {"X-Frame": str(fid)}
            if q.get("dl"):
                extra["Content-Disposition"] = f'attachment; filename="camera{cid}_{time.strftime("%Y%m%d_%H%M%S")}.jpg"'
            return self._send(200, data, "image/jpeg", extra)
        return self._send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    def _stream_video(self, cid, quality):
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.close_connection = True
        dead = []

        def write(b):
            try:
                self.wfile.write(b"%x\r\n" % len(b) + b + b"\r\n")
                self.wfile.flush()
            except (OSError, ssl.SSLError):
                dead.append(1)
                raise

        self.srv.source.video(cid, quality, write, lambda: not dead and self.srv.valid(self._session()))
        try:
            self.wfile.write(b"0\r\n\r\n")
        except (OSError, ssl.SSLError):
            pass

    def do_POST(self):
        u = urlsplit(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        if n > 4096:
            return self._send(413, b"too large", "text/plain")
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        if not self._same_origin():
            return self._send(403, b"bad origin", "text/plain")
        if u.path == "/login":
            ip = self._client()
            left = self.srv.guard.blocked(ip)
            if left:
                return self._redirect(f"/login?e=locked{int(left // 60) + 1}")
            f = parse_qs(body)
            tok = self.srv.login(ip, f.get("u", [""])[0], f.get("p", [""])[0])
            if not tok:
                return self._redirect("/login?e=bad")
            return self._redirect("/", {"Set-Cookie": f"{COOKIE}={tok}; Path=/; Secure; HttpOnly; SameSite=Strict; "
                                                      f"Max-Age={SESSION_HOURS * 3600}"})
        if not self._authed():
            return self._json(401, {"error": "login"})
        if u.path == "/api/ptz":
            try:
                d = json.loads(body or "{}")
                self.srv.source.ptz(int(d["id"]), float(d.get("pan", 0)), float(d.get("tilt", 0)), bool(d.get("stop")))
                return self._json(200, {"ok": True})
            except Exception as e:  # noqa: BLE001
                return self._json(400, {"error": str(e)[:120]})
        return self._send(404, b"not found", "text/plain")


# ---------------------------------------------------------------- หน้าเว็บ
_CSS = """
:root{--bg:#090c12;--card:#121723;--card2:#181e2c;--line:#232b3b;--text:#e8ebf1;--muted:#8a93a6;
--accent:#3b82f6;--accent2:#60a5fa;--bad:#ef4444;--ok:#22c55e;--r:14px}
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
html,body{margin:0;min-height:100%;background:var(--bg);color:var(--text);
font-family:"Leelawadee UI","Segoe UI",system-ui,-apple-system,"Noto Sans Thai",sans-serif;font-size:15px;line-height:1.5}
button,input{font:inherit;color:inherit}
svg{width:18px;height:18px;flex:none;stroke:currentColor;fill:none;stroke-width:2;stroke-linecap:round;stroke-linejoin:round}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:40px;background:var(--card2);
border:1px solid var(--line);border-radius:11px;padding:8px 14px;cursor:pointer;text-decoration:none;color:var(--text);
transition:background .15s,border-color .15s,transform .05s}
.btn:hover{background:#212a3d;border-color:#33405a}.btn:active{transform:scale(.97)}
.btn:focus-visible,input:focus-visible{outline:2px solid var(--accent2);outline-offset:2px}
.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;font-weight:600}.btn.primary:hover{background:#2f6fdc}
.btn.icon{padding:8px;min-width:40px}
.logo{display:inline-flex;align-items:center;justify-content:center;width:34px;height:34px;border-radius:10px;
background:linear-gradient(135deg,#3b82f6,#6366f1);color:#fff;flex:none}
"""

_ICON = {
    "cam": '<svg viewBox="0 0 24 24"><path d="m16 13 5.2 3.5a.5.5 0 0 0 .8-.4V7.9a.5.5 0 0 0-.8-.4L16 10.5"/><rect x="2" y="6" width="14" height="12" rx="2"/></svg>',
    "back": '<svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg>',
    "out": '<svg viewBox="0 0 24 24"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5"/><path d="M21 12H9"/></svg>',
    "grid": '<svg viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></svg>',
    "full": '<svg viewBox="0 0 24 24"><path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/><path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/></svg>',
    "snap": '<svg viewBox="0 0 24 24"><path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3z"/><circle cx="12" cy="13" r="3"/></svg>',
    "eye": '<svg viewBox="0 0 24 24"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
    "lock": '<svg viewBox="0 0 24 24"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>',
}

_LOGIN = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="theme-color" content="#090c12">
<title>เข้าสู่ระบบ · __APP__</title>
<style>__CSS__
body{display:flex;align-items:center;justify-content:center;min-height:100vh;padding:20px;
background:radial-gradient(900px 500px at 50% -10%,rgba(59,130,246,.18),transparent 70%),var(--bg)}
.box{width:100%;max-width:380px;padding:30px 26px 24px;background:var(--card);border:1px solid var(--line);border-radius:20px;
box-shadow:0 20px 60px rgba(0,0,0,.45)}
.head{display:flex;align-items:center;gap:12px;margin-bottom:22px}.head .logo{width:44px;height:44px;border-radius:13px}
.head .logo svg{width:24px;height:24px}
h1{font-size:19px;margin:0;line-height:1.3}.sub{color:var(--muted);font-size:13px;margin:0}
label{display:block;font-size:13px;color:var(--muted);margin:16px 0 6px}
.field{position:relative}
input{width:100%;height:46px;padding:0 13px;background:#0d121c;border:1px solid var(--line);border-radius:11px;font-size:16px}
input:focus{border-color:var(--accent)}
.field button{position:absolute;right:4px;top:4px;height:38px;width:38px;background:none;border:0;color:var(--muted);cursor:pointer;
border-radius:9px;display:flex;align-items:center;justify-content:center}
.err{display:flex;gap:8px;align-items:center;background:#3a1218;border:1px solid #6b1d27;color:#fecaca;padding:10px 12px;
border-radius:11px;font-size:13px}
.btn{width:100%;height:46px;margin-top:22px;font-size:16px}
.foot{display:flex;align-items:center;justify-content:center;gap:6px;color:var(--muted);font-size:12px;margin-top:18px}
.foot svg{width:13px;height:13px}
</style></head><body><form class="box" method="post" action="/login">
<div class="head"><span class="logo">__CAM__</span><div><h1>__APP__</h1><p class="sub">เข้าสู่ระบบเพื่อดูกล้อง</p></div></div>__ERR__
<label for="u">ชื่อผู้ใช้</label><input id="u" name="u" autocomplete="username" autocapitalize="none" required autofocus>
<label for="p">รหัสผ่าน</label><div class="field"><input id="p" name="p" type="password" autocomplete="current-password" required>
<button type="button" id="eye" aria-label="แสดงรหัสผ่าน">__EYE__</button></div>
<button class="btn primary" type="submit">เข้าสู่ระบบ</button>
<div class="foot">__LOCK__ เชื่อมต่อแบบเข้ารหัสถึงเครื่องที่บ้านโดยตรง</div></form>
<script>document.getElementById('eye').onclick=function(){var p=document.getElementById('p');p.type=p.type==='password'?'text':'password'}</script>
</body></html>"""


def login_page(err: str) -> str:
    msg = ""
    if err == "bad":
        msg = "ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง"
    elif err.startswith("locked"):
        # ค่านี้มาจาก URL (ใครก็พิมพ์ได้) → รับเฉพาะตัวเลข ไม่นำข้อความอื่นไปแสดงในหน้า
        mins = err[6:] if err[6:].isascii() and err[6:].isdigit() and len(err) <= 9 else str(LOCK_MINUTES)
        msg = f"ใส่รหัสผิดหลายครั้ง – ลองใหม่ในอีก {mins} นาที"
    html = (_LOGIN.replace("__CSS__", _CSS).replace("__APP__", APP_NAME).replace("__CAM__", _ICON["cam"])
            .replace("__EYE__", _ICON["eye"]).replace("__LOCK__", _ICON["lock"]))
    return html.replace("__ERR__", f'<div class="err">{msg}</div>' if msg else "")


APP_PAGE = """<!doctype html><html lang="th"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#090c12"><title>__APP__</title>
<style>__CSS__
header{position:sticky;top:0;z-index:5;display:flex;align-items:center;gap:10px;padding:10px 14px;
padding-top:max(10px,env(safe-area-inset-top));background:rgba(9,12,18,.88);backdrop-filter:blur(10px);border-bottom:1px solid var(--line)}
header h1{font-size:16px;margin:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
header .sp{flex:1;min-width:0;display:flex;align-items:center;gap:10px}
.pill{display:inline-flex;align-items:center;gap:6px;color:var(--muted);font-size:12px;border:1px solid var(--line);border-radius:99px;padding:3px 10px;white-space:nowrap}
.dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none}.dot.live{background:var(--ok);box-shadow:0 0 0 3px rgba(34,197,94,.18)}.dot.err{background:var(--bad)}
main{padding:12px;padding-bottom:max(16px,env(safe-area-inset-bottom));max-width:1500px;margin:0 auto}
.grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fill,minmax(min(100%,330px),1fr))}
.grid.one{grid-template-columns:1fr;max-width:900px;margin:0 auto}
.tile{position:relative;background:#05070b;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;cursor:pointer;
transition:border-color .15s,transform .15s}
.tile:hover{border-color:var(--accent)}.tile:active{transform:scale(.99)}
.stage{position:relative;aspect-ratio:16/9;width:100%;overflow:hidden;background:#05070b}
.stage img,.stage video{position:absolute;left:0;top:0;width:100%;height:100%;object-fit:contain;display:block}
.stage .msg{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--muted);font-size:12px;padding:12px;text-align:center}
.cap{position:absolute;left:0;right:0;bottom:0;display:flex;align-items:center;gap:8px;padding:22px 12px 9px;font-size:13px;font-weight:600;
background:linear-gradient(transparent,rgba(0,0,0,.72));pointer-events:none}
.cap span.n{white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#focus{display:none}#focus.on{display:block}
.player{background:#05070b;border:1px solid var(--line);border-radius:var(--r);overflow:hidden;display:flex;justify-content:center}
.player .stage{width:min(100%,128vh)}
.player:fullscreen{border:0;border-radius:0;align-items:center;width:100vw;height:100vh}
.player:fullscreen .stage{width:min(100vw,177.78vh)}
#fvid.crop{height:200%;object-fit:fill}#fvid.crop.bottom{top:-100%}
.ftitle{display:flex;align-items:center;gap:8px;margin:0 2px 10px;font-weight:600;font-size:16px}
.ftitle .n{flex:1;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.tag{font-size:11px;font-weight:500;color:var(--muted);border:1px solid var(--line);border-radius:7px;padding:2px 8px;white-space:nowrap}
.bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:12px 0}
.seg{display:inline-flex;border:1px solid var(--line);border-radius:11px;overflow:hidden;background:var(--card2)}
.seg button{background:none;border:0;min-height:40px;padding:0 16px;cursor:pointer;color:var(--muted)}
.seg button.on{background:var(--accent);color:#fff;font-weight:600}
.cols{display:grid;gap:14px;grid-template-columns:1fr}
@media(min-width:900px){.cols.ptz{grid-template-columns:1fr 250px;align-items:start}}
.panel{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:14px}
.panel h2{font-size:13px;color:var(--muted);font-weight:600;margin:0 0 10px}
.pad{display:grid;grid-template-columns:repeat(3,58px);gap:8px;justify-content:center;user-select:none;touch-action:none}
.pad button{height:58px;border-radius:50%;background:var(--card2);border:1px solid var(--line);cursor:pointer;display:flex;align-items:center;justify-content:center}
.pad button svg{width:22px;height:22px}.pad button:active,.pad button.down{background:var(--accent);border-color:var(--accent)}
.hint{color:var(--muted);font-size:12px;text-align:center;margin-top:10px}
.others{display:flex;gap:8px;overflow-x:auto;padding:2px 0 6px;scrollbar-width:thin}
.chip{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;background:var(--card2);border:1px solid var(--line);border-radius:99px;
padding:7px 13px;cursor:pointer;font-size:13px}
.chip.on{border-color:var(--accent);background:rgba(59,130,246,.15)}
#toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);background:#3a1218;border:1px solid #6b1d27;color:#fecaca;
padding:9px 14px;border-radius:11px;font-size:13px;display:none;z-index:9}
@media(max-width:520px){header h1{display:none}.btn .t{display:none}}
</style></head><body>
<header><span class="logo">__CAM__</span><div class="sp"><h1>__APP__</h1><span class="pill"><span class="dot" id="hdot"></span><span id="count">…</span></span></div>
<button class="btn" id="back" style="display:none">__BACK__<span>ทุกกล้อง</span></button>
<button class="btn icon" id="layout" title="สลับการจัดเรียง" aria-label="สลับการจัดเรียง">__GRID__</button>
<a class="btn" href="/logout" title="ออกจากระบบ">__OUT__<span class="t">ออก</span></a></header>
<main><div class="grid" id="grid"></div>
<section id="focus">
<div class="ftitle"><span class="dot" id="fdot"></span><span class="n" id="fname"></span><span class="tag" id="fmode"></span></div>
<div class="cols" id="fcols"><div>
<div class="player" id="player"><div class="stage"><img id="fimg" alt=""><video id="fvid" muted playsinline autoplay></video><div class="msg" id="fmsg"></div></div></div>
<div class="bar"><span class="seg" role="group" aria-label="คุณภาพ"><button data-q="sd">SD</button><button data-q="hd" class="on">HD</button></span>
<a class="btn" id="snap">__SNAP__<span>ถ่ายภาพ</span></a><button class="btn" id="fs">__FULL__<span>เต็มจอ</span></button></div>
</div>
<div class="panel" id="ptz" style="display:none"><h2>หมุนกล้อง</h2><div class="pad">
<button data-d="-1,1" aria-label="ซ้ายบน"><svg viewBox="0 0 24 24"><path d="M17 17 7 7"/><path d="M7 17V7h10"/></svg></button>
<button data-d="0,1" aria-label="ขึ้น"><svg viewBox="0 0 24 24"><path d="m18 15-6-6-6 6"/></svg></button>
<button data-d="1,1" aria-label="ขวาบน"><svg viewBox="0 0 24 24"><path d="M7 7h10v10"/><path d="M7 17 17 7"/></svg></button>
<button data-d="-1,0" aria-label="ซ้าย"><svg viewBox="0 0 24 24"><path d="m15 18-6-6 6-6"/></svg></button>
<button data-s="1" aria-label="หยุด"><svg viewBox="0 0 24 24"><rect x="8" y="8" width="8" height="8" rx="1.5" fill="currentColor"/></svg></button>
<button data-d="1,0" aria-label="ขวา"><svg viewBox="0 0 24 24"><path d="m9 18 6-6-6-6"/></svg></button>
<button data-d="-1,-1" aria-label="ซ้ายล่าง"><svg viewBox="0 0 24 24"><path d="M17 7 7 17"/><path d="M17 17H7V7"/></svg></button>
<button data-d="0,-1" aria-label="ลง"><svg viewBox="0 0 24 24"><path d="m6 9 6 6 6-6"/></svg></button>
<button data-d="1,-1" aria-label="ขวาล่าง"><svg viewBox="0 0 24 24"><path d="m7 7 10 10"/><path d="M17 7v10H7"/></svg></button></div>
<div class="hint">กดค้างเพื่อหมุน ปล่อยเพื่อหยุด</div></div></div>
<div class="panel" style="margin-top:14px"><h2>กล้องอื่น</h2><div class="others" id="others"></div></div>
</section></main><div id="toast">ขาดการเชื่อมต่อ – กำลังลองใหม่…</div>
<script>
let cams=[],focus=null,loops={},quality='hd',player=null,fails=0,noCodec=false;
const $=s=>document.querySelector(s);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const MS=window.ManagedMediaSource||window.MediaSource;
const MIME={h264:'video/mp4; codecs="avc1.640033"',hevc:'video/mp4; codecs="hvc1.1.6.L153.B0"'};
const canVideo=c=>!!(MS&&MIME[c]&&MS.isTypeSupported(MIME[c]));
const net=ok=>{fails=ok?0:fails+1;$('#toast').style.display=fails>=2?'block':'none'};
async function api(u,o){let r;try{r=await fetch(u,Object.assign({credentials:'same-origin'},o||{}))}catch(e){if(!(o&&o.signal&&o.signal.aborted))net(false);throw e}
 net(true);if(r.status===401){location.href='/login';throw 0}return r}
function loop(id,img,msg,dot,key){ // ภาพทีละภาพ: ภาพก่อนโหลดเสร็จค่อยขอภาพถัดไป (เน็ตช้าก็ไม่ค้าง)
 if(loops[key])return;const me=loops[key]={};let since=-1,url=null; // me = ตัวแทนของรอบนี้: หยุดแล้วเริ่มใหม่ทันที รอบเก่าต้องเลิกเอง
 (async()=>{while(loops[key]===me){if(document.hidden){await sleep(500);continue}
  try{const r=await api('/frame/'+id+'?since='+since);
   if(r.status===200){since=+r.headers.get('X-Frame');const b=await r.blob();const n=URL.createObjectURL(b);
    img.src=n;if(url)URL.revokeObjectURL(url);url=n;msg.textContent='';dot.className='dot live'}
   else{const c=cams[id];msg.textContent=(c&&c.status)||'รอภาพ…';dot.className='dot err'}
  }catch(e){await sleep(2000)}
  await sleep(key==='f'?40:150)}})()}
function stopLoops(key){for(const k in loops)if(!key||k===key||(key==='g'&&k.startsWith('g')))delete loops[k]}

// ---------- วิดีโอจริง (MSE): ห่อวิดีโอจากกล้องเป็น MP4 ไม่แปลงใหม่ ----------
function stopVideo(){if(player){player.stop=true;try{player.ctl.abort()}catch(e){}player=null}
 const v=$('#fvid');v.removeAttribute('src');v.load();v.style.display='none'}
async function playVideo(id,codec){
 stopVideo();const me={stop:false,ctl:null,fails:0};player=me;const v=$('#fvid'),c=cams[id];
 v.className=c.crop?('crop '+c.crop):'';v.style.display='';$('#fimg').style.display='none';
 $('#fmode').textContent='วิดีโอ '+quality.toUpperCase();
 while(!me.stop){
  try{
   const ms=new MS();v.disableRemotePlayback=true;v.src=URL.createObjectURL(ms);
   await new Promise(r=>ms.addEventListener('sourceopen',r,{once:true}));
   const sb=ms.addSourceBuffer(MIME[codec]);const q=[];let busy=false;
   const pump=()=>{if(busy||sb.updating||!q.length||ms.readyState!=='open')return;busy=true;
    try{sb.appendBuffer(q.shift())}catch(e){busy=false;if(e.name==='QuotaExceededError'){trim(true)}else throw e}};
   const trim=force=>{try{const b=sb.buffered;if(!b.length||sb.updating)return;
     const end=b.end(b.length-1);if(end-v.currentTime>1.5)v.currentTime=end-0.2; // ตามให้ทันสด
     if(force||v.currentTime-b.start(0)>20)sb.remove(b.start(0),Math.max(b.start(0),v.currentTime-5))}catch(e){}};
   sb.addEventListener('updateend',()=>{busy=false;pump()});
   me.ctl=new AbortController();
   const r=await api('/video/'+id+'?q='+quality,{signal:me.ctl.signal});
   const rd=r.body.getReader();const tick=setInterval(()=>{trim(false);if(v.paused)v.play().catch(()=>{})},1000);
   let got=0;
   try{for(;;){const {done,value}=await rd.read();if(done||me.stop)break;q.push(value);pump();
    if(!got++){$('#fmsg').textContent='';$('#fdot').className='dot live';me.fails=0}}}
   finally{clearInterval(tick)}
  }catch(e){if(me.stop)return}
  if(me.stop)return;
  if(++me.fails>=3){ // เล่นวิดีโอไม่ได้ → ใช้ภาพทีละภาพแทน
   $('#fmsg').textContent='';stopVideo();useImages(id);return}
  $('#fmsg').textContent='กำลังต่อใหม่…';$('#fdot').className='dot';await sleep(1000)}}
function useImages(id){$('#fimg').style.display='';$('#fvid').style.display='none';
 $('#fmode').textContent='ภาพต่อเนื่อง';loop(id,$('#fimg'),$('#fmsg'),$('#fdot'),'f')}

function build(){const g=$('#grid');g.innerHTML='';
 if(!cams.length){g.innerHTML='<div class="panel" style="text-align:center;color:var(--muted)">ยังไม่มีกล้อง – เพิ่มกล้องในโปรแกรมที่บ้าน</div>'}
 cams.forEach(c=>{const t=document.createElement('div');t.className='tile';t.tabIndex=0;t.setAttribute('role','button');
  t.innerHTML='<div class="stage"><img alt=""><div class="msg">กำลังโหลด…</div></div><div class="cap"><span class="dot"></span><span class="n"></span></div>';
  t.querySelector('.n').textContent=c.name;t.onclick=()=>openCam(c.id);t.onkeydown=e=>{if(e.key==='Enter')openCam(c.id)};g.appendChild(t);
  loop(c.id,t.querySelector('img'),t.querySelector('.msg'),t.querySelector('.dot'),'g'+c.id)});
 status()}
function status(){const live=cams.filter(c=>c.live).length;$('#count').textContent=live+'/'+cams.length+' ออนไลน์';
 $('#hdot').className='dot '+(cams.length&&live===cams.length?'live':live?'':'err')}
function others(){const o=$('#others');o.innerHTML='';cams.forEach(c=>{const b=document.createElement('button');
 b.className='chip'+(c.id===focus?' on':'');b.innerHTML='<span class="dot '+(c.live?'live':'err')+'"></span>';
 b.appendChild(document.createTextNode(c.name));b.onclick=()=>{if(c.id!==focus)showCam(c.id)};o.appendChild(b);
 if(c.id===focus)setTimeout(()=>b.scrollIntoView({block:'nearest',inline:'center'}),0)})}
function startFocus(){const c=cams[focus];stopLoops('f');stopVideo();$('#fimg').removeAttribute('src');
 $('#fmsg').textContent='กำลังโหลด…';$('#fdot').className='dot';noCodec=!c.codec; // กล้องยังต่อไม่ติด = ยังไม่รู้ชนิดวิดีโอ → refresh() จะลองใหม่
 if(document.hidden)return; // หน้าถูกซ่อนอยู่: ยังไม่ดึงภาพ – visibilitychange จะเรียก startFocus() อีกครั้งตอนกลับมา
 if(canVideo(c.codec))playVideo(focus,c.codec);else useImages(focus)}
function showCam(id){focus=id;const c=cams[id];$('#fname').textContent=c.name;$('#ptz').style.display=c.ptz?'':'none';
 $('#fcols').className='cols'+(c.ptz?' ptz':'');$('#snap').href='/frame/'+id+'?dl=1';others();startFocus()}
function openCam(id){stopLoops('g');$('#grid').style.display='none';$('#focus').classList.add('on');$('#back').style.display='';
 $('#layout').style.display='none';showCam(id);history.pushState({f:id},'');window.scrollTo(0,0)}
function closeCam(){focus=null;stopLoops('f');stopVideo();if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});$('#focus').classList.remove('on');$('#grid').style.display='';
 $('#back').style.display='none';$('#layout').style.display='';build()}
$('#back').onclick=()=>history.back();window.onpopstate=()=>{if(focus!==null)closeCam()};
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{quality=b.dataset.q;
 document.querySelectorAll('.seg button').forEach(x=>x.classList.toggle('on',x===b));if(focus!==null)startFocus()});
const setLayout=one=>{$('#grid').classList.toggle('one',one);try{localStorage.setItem('one',one?'1':'')}catch(e){}};
$('#layout').onclick=()=>setLayout(!$('#grid').classList.contains('one'));
try{if(localStorage.getItem('one'))setLayout(true)}catch(e){}
$('#fs').onclick=()=>{const p=$('#player'),v=$('#fvid');
 if(document.fullscreenElement)document.exitFullscreen();
 else if(p.requestFullscreen)p.requestFullscreen().catch(()=>{});
 else if(v.webkitEnterFullscreen&&v.style.display!=='none')v.webkitEnterFullscreen()};
$('#player').ondblclick=()=>$('#fs').click();
async function ptz(body){try{await api('/api/ptz',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})}catch(e){}}
document.querySelectorAll('.pad button').forEach(b=>{
 const go=e=>{e.preventDefault();b.classList.add('down');if(b.dataset.s)return ptz({id:focus,stop:true});const[x,y]=b.dataset.d.split(',').map(Number);ptz({id:focus,pan:x*.5,tilt:y*.5})};
 const st=e=>{e.preventDefault();if(!b.classList.contains('down'))return;b.classList.remove('down');if(!b.dataset.s)ptz({id:focus,stop:true})};
 b.addEventListener('pointerdown',go);b.addEventListener('pointerup',st);b.addEventListener('pointerleave',st);b.addEventListener('pointercancel',st)});
document.addEventListener('keydown',e=>{if(focus===null||e.target.tagName==='INPUT')return;
 if(e.key==='Escape'&&!document.fullscreenElement)history.back();
 else if(e.key==='ArrowRight')showCam((focus+1)%cams.length);
 else if(e.key==='ArrowLeft')showCam((focus+cams.length-1)%cams.length)});
document.addEventListener('visibilitychange',()=>{if(focus===null)return;if(document.hidden)stopVideo();else startFocus()});
// รายการกล้องเปลี่ยน (เพิ่ม/ลบ/ข้ามช่องในโปรแกรมที่บ้าน) → เลขลำดับของแต่ละช่องเลื่อน ต้องสร้างหน้าใหม่ ไม่งั้นชื่อกับภาพจะไม่ตรงกัน
const sigOf=l=>JSON.stringify(l.map(c=>[c.name,c.device,c.crop,c.ptz]));let lastSig='';
function refresh(n){const s=sigOf(n);
 if(s===lastSig){cams=n;status();if(focus!==null){others();if(noCodec&&!document.hidden&&canVideo(n[focus].codec))startFocus()}return}
 lastSig=s;const old=focus!==null?cams[focus]:null;cams=n;
 if(!old){stopLoops('g');build();return}
 const i=n.findIndex(c=>c.name===old.name&&c.device===old.device); // กล้องที่กำลังดูอยู่ย้ายไปลำดับไหน
 if(i<0)history.back();
 else if(i===focus&&n[i].crop===old.crop&&n[i].ptz===old.ptz){status();others()} // กล้องที่ดูอยู่ไม่ขยับ ไม่เปลี่ยน → ไม่ต้องต่อภาพใหม่
 else showCam(i)}
(async()=>{const r=await api('/api/cams');cams=(await r.json()).cams;lastSig=sigOf(cams);build();
 setInterval(async()=>{try{refresh((await (await api('/api/cams')).json()).cams)}catch(e){}},5000)})();
</script></body></html>"""
for _k, _v in (("__CSS__", _CSS), ("__APP__", APP_NAME), ("__CAM__", _ICON["cam"]), ("__BACK__", _ICON["back"]),
               ("__GRID__", _ICON["grid"]), ("__OUT__", _ICON["out"]), ("__SNAP__", _ICON["snap"]), ("__FULL__", _ICON["full"])):
    APP_PAGE = APP_PAGE.replace(_k, _v)

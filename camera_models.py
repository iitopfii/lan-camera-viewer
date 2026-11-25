"""รายการรุ่นกล้องที่รองรับ + แปลง config เป็นรายการช่องภาพ + เครื่องมือค้นหา/ทดสอบกล้อง

config.json (เวอร์ชัน 2) เก็บ "กล้องหนึ่งตัว = หนึ่งรายการ" แล้วโปรแกรมจะแตกเป็นช่องภาพตามจำนวนเลนส์เอง:
    {"name": "Tapo1", "model": "tapo_dual", "ip": "192.168.1.157",
     "username": "...", "password": "...", "enabled": true, "ptz": true}

รุ่นที่ไม่มีในรายการ ใช้ "model": "custom" แล้วใส่ hd_path / sd_path / port / transport / layout เอง
(หรือกด "ตรวจหาอัตโนมัติ" ในหน้าตั้งค่า ให้โปรแกรมถามกล้องผ่าน ONVIF)
"""
from __future__ import annotations

import ipaddress
import re
import socket
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote, urlsplit

# ตัวเลือก ffmpeg: TCP ภาพไม่แตก (Tapo, Hikvision ฯลฯ) · UDP สำหรับกล้องที่ไม่รองรับ TCP (Yoosee) ต้องเพิ่มบัฟเฟอร์
FFMPEG_OPTS = {
    "tcp": {"rtsp_transport": "tcp", "timeout": "5000000"},
    "udp": {"rtsp_transport": "udp", "timeout": "8000000", "buffer_size": "4096000",
            "max_delay": "1000000", "reorder_queue_size": "2000"},
}


def _hide_pw(e) -> str:
    """ข้อความ error ของ ffmpeg มี URL ที่มีรหัสผ่าน – ตัดออกก่อนแสดงบนจอ"""
    msg = re.sub(r"rtsp://[^@\s']*@", "rtsp://***@", str(e))
    msg = re.sub(r"^\[Errno -?\d+\]\s*", "", msg)
    return msg.split(":")[0][:60]


# lens: hd / sd = path ของสตรีม, crop = "top"/"bottom" เมื่อกล้องส่งสองเลนส์มาซ้อนบน-ล่างในภาพเดียว
MODELS: dict[str, dict] = {
    "tapo_single": {
        "label": "TP-Link Tapo – เลนส์เดียว (C100, C200, C210, C220, C500, C520WS ฯลฯ)",
        "user": "", "port": 554, "transport": "tcp", "ptz": True, "onvif_port": 2020,
        "lenses": [{"label": "", "hd": "stream1", "sd": "stream2"}],
        "help": "Username/Password = \"บัญชีกล้อง (Camera Account)\" ที่ตั้งในแอป Tapo → เลือกกล้อง → "
                "ตั้งค่า → ตั้งค่าขั้นสูง → บัญชีกล้อง (ไม่ใช่อีเมล TP-Link ID)",
    },
    "tapo_dual": {
        "label": "TP-Link Tapo – 2 เลนส์ (C545D ฯลฯ)",
        "user": "", "port": 554, "transport": "tcp", "ptz": True, "onvif_port": 2020,
        "lenses": [{"label": "เลนส์ 1", "hd": "stream1", "sd": "stream2"},
                   {"label": "เลนส์ 2", "hd": "stream6", "sd": "stream7"}],
        "help": "Username/Password = \"บัญชีกล้อง (Camera Account)\" ที่ตั้งในแอป Tapo → เลือกกล้อง → "
                "ตั้งค่า → ตั้งค่าขั้นสูง → บัญชีกล้อง (ไม่ใช่อีเมล TP-Link ID)",
    },
    "yoosee_dual": {
        "label": "Yoosee – 2 เลนส์ (ภาพซ้อนบน-ล่าง)",
        "user": "admin", "port": 554, "transport": "udp", "ptz": False, "onvif_port": 5000,
        "lenses": [{"label": "เลนส์บน", "hd": "onvif1", "sd": "onvif2", "crop": "top"},
                   {"label": "เลนส์ล่าง", "hd": "onvif1", "sd": "onvif2", "crop": "bottom"}],
        "help": "Username = admin เสมอ · Password = \"รหัส NVR\" ที่ตั้งในแอป Yoosee → ตั้งค่ากล้อง → "
                "ความปลอดภัย → รหัส NVR (กล้องดูผ่าน RTSP ได้ทีละไม่กี่เครื่อง ปิดแอปมือถือก่อนถ้าต่อไม่ได้)",
    },
    "yoosee_single": {
        "label": "Yoosee – เลนส์เดียว",
        "user": "admin", "port": 554, "transport": "udp", "ptz": False, "onvif_port": 5000,
        "lenses": [{"label": "", "hd": "onvif1", "sd": "onvif2"}],
        "help": "Username = admin เสมอ · Password = \"รหัส NVR\" ที่ตั้งในแอป Yoosee → ตั้งค่ากล้อง → "
                "ความปลอดภัย → รหัส NVR",
    },
    "hikvision": {
        "label": "Hikvision / HiLook / HiWatch",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 80,
        "lenses": [{"label": "", "hd": "Streaming/Channels/101", "sd": "Streaming/Channels/102"}],
        "nvr": {"hd": "Streaming/Channels/{n}01", "sd": "Streaming/Channels/{n}02"},
        "help": "ใช้ user/รหัสเดียวกับที่เข้าหน้าเว็บของกล้อง (ปกติ admin) "
                "ถ้าจะใช้หมุนกล้อง ต้องเปิด ONVIF และสร้าง user ONVIF ในหน้าเว็บกล้องก่อน · "
                "ถ้าเป็นเครื่องบันทึก (NVR/DVR) ใส่ IP ของเครื่องบันทึก แล้วตั้ง \"จำนวนช่อง\" ด้านล่าง",
    },
    "ezviz": {
        "label": "EZVIZ",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 80,
        "lenses": [{"label": "", "hd": "h264/ch1/main/av_stream", "sd": "h264/ch1/sub/av_stream"}],
        "help": "Username = admin · Password = \"Verification code\" 6 ตัวอักษรบนสติกเกอร์ใต้กล้อง "
                "(ต้องเปิด RTSP ในแอป EZVIZ → ตั้งค่า → LAN Live View ก่อน)",
    },
    "dahua": {
        "label": "Dahua / Imou",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 80,
        "lenses": [{"label": "", "hd": "cam/realmonitor?channel=1&subtype=0",
                    "sd": "cam/realmonitor?channel=1&subtype=1"}],
        "nvr": {"hd": "cam/realmonitor?channel={n}&subtype=0", "sd": "cam/realmonitor?channel={n}&subtype=1"},
        "help": "Dahua: user/รหัสหน้าเว็บกล้อง · Imou: Username = admin, Password = \"Safety code\" "
                "บนสติกเกอร์ใต้กล้อง (หรือรหัสที่ตั้งใหม่ในแอป Imou) · "
                "ถ้าเป็นเครื่องบันทึก (NVR/DVR) ใส่ IP ของเครื่องบันทึก แล้วตั้ง \"จำนวนช่อง\" ด้านล่าง",
    },
    "reolink": {
        "label": "Reolink",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 8000,
        "lenses": [{"label": "", "hd": "h264Preview_01_main", "sd": "h264Preview_01_sub"}],
        "nvr": {"hd": "h264Preview_{n:02d}_main", "sd": "h264Preview_{n:02d}_sub"},
        "help": "ใช้ user/รหัสเดียวกับแอป Reolink (ปกติ admin) · ต้องเปิด RTSP/ONVIF ใน "
                "ตั้งค่า → เครือข่าย → ตั้งค่าพอร์ตขั้นสูง · "
                "ถ้าเป็นเครื่องบันทึก (NVR) ใส่ IP ของเครื่องบันทึก แล้วตั้ง \"จำนวนช่อง\" ด้านล่าง",
    },
    "vstarcam": {
        "label": "VStarcam / Eye4",
        "user": "admin", "port": 10554, "transport": "tcp", "ptz": True, "onvif_port": 10080,
        "lenses": [{"label": "", "hd": "tcp/av0_0", "sd": "tcp/av0_1"}],
        "help": "Username = admin · Password = รหัสที่ตั้งในแอป Eye4 (ค่าเริ่มต้น 888888)",
    },
    "tvt": {
        "label": "TVT / Hiview – กล้อง IP",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 80,
        "lenses": [{"label": "", "hd": "profile1", "sd": "profile2"}],
        "help": "ใช้ user/รหัสเดียวกับหน้าเว็บกล้องหรือแอป SuperLive Plus / Hiview (ปกติ admin) · "
                "ถ้าต่อผ่านเครื่องบันทึก (NVR/DVR) ให้เลือกรุ่น \"TVT / Hiview – เครื่องบันทึก\" แทน",
    },
    "tvt_nvr": {
        "label": "TVT / Hiview – เครื่องบันทึก (NVR/DVR)",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 80,
        "lenses": [{"label": "", "hd": "chID=1&streamType=main", "sd": "chID=1&streamType=sub"}],
        "nvr": {"hd": "chID={n}&streamType=main", "sd": "chID={n}&streamType=sub"},
        "help": "ใส่ IP ของเครื่องบันทึก ใช้ user/รหัสเดียวกับหน้าเว็บเครื่องบันทึกหรือแอป SuperLive Plus / Hiview "
                "(ปกติ admin) แล้วตั้ง \"จำนวนช่อง\" ด้านล่างให้เท่ากับจำนวนกล้องที่ต่ออยู่",
    },
    "v380": {
        "label": "V380 / V380 Pro (เฉพาะรุ่นที่เปิด RTSP ได้)",
        "user": "admin", "port": 554, "transport": "tcp", "ptz": False, "onvif_port": 8899,
        "lenses": [{"label": "", "hd": "live/ch00_0", "sd": "live/ch00_1"}],
        "help": "ต้องเปิด RTSP/ONVIF ในแอป V380 ก่อน (ตั้งค่ากล้อง → ตั้งค่าขั้นสูง) แล้วใช้ Username = admin "
                "กับรหัสที่ตั้งตอนเปิด · รุ่นที่ไม่มีเมนูนี้ดูได้ผ่านแอปเท่านั้น ใช้กับโปรแกรมนี้ไม่ได้",
    },
    "custom": {
        "label": "รุ่นอื่นๆ / กำหนดเอง (กด \"ตรวจหาอัตโนมัติ\" ได้)",
        "user": "admin", "port": 554, "transport": "auto", "ptz": False, "onvif_port": 80,
        "lenses": [],
        "help": "กล้องส่วนใหญ่ที่รองรับ ONVIF/RTSP ใช้ได้ – ใส่ IP, user, รหัส แล้วกด \"ตรวจหาอัตโนมัติ\" "
                "โปรแกรมจะถามที่อยู่สตรีมจากกล้องเอง ถ้าไม่ได้ ให้ดู path RTSP จากคู่มือกล้องแล้วใส่เอง",
    },
}

LAYOUTS = {"single": "ภาพเดียว", "split": "2 เลนส์ ซ้อนบน-ล่างในภาพเดียว"}
MAX_CHANNELS = 64


def channels_of(cam: dict) -> int:
    """จำนวนช่องของเครื่องบันทึก (NVR/DVR) – 1 = กล้องตัวเดียว · รุ่นที่ไม่ใช่เครื่องบันทึกได้ 1 เสมอ"""
    if "nvr" not in MODELS.get(cam.get("model", "custom"), {}):
        return 1
    try:
        return min(MAX_CHANNELS, max(1, int(cam.get("channels") or 1)))
    except (TypeError, ValueError):
        return 1


def lenses_of(cam: dict) -> list[dict]:
    if cam.get("model", "custom") != "custom" and cam.get("model") in MODELS:
        m, n = MODELS[cam["model"]], channels_of(cam)
        if n > 1:  # เครื่องบันทึก: หนึ่งช่องภาพต่อหนึ่งช่องของเครื่อง
            return [{"label": f"ช่อง {i}", "hd": m["nvr"]["hd"].format(n=i), "sd": m["nvr"]["sd"].format(n=i)}
                    for i in range(1, n + 1)]
        return m["lenses"]
    hd, sd = cam.get("hd_path", ""), cam.get("sd_path") or cam.get("hd_path", "")
    if cam.get("layout") == "split":
        return [{"label": "เลนส์บน", "hd": hd, "sd": sd, "crop": "top"},
                {"label": "เลนส์ล่าง", "hd": hd, "sd": sd, "crop": "bottom"}]
    return [{"label": "", "hd": hd, "sd": sd}]


def setting(cam: dict, key: str):
    """ค่าของกล้อง: ถ้าในรายการไม่ได้ใส่ไว้ ใช้ค่าตั้งต้นของรุ่น"""
    if key in cam and cam[key] not in (None, ""):
        return cam[key]
    return MODELS.get(cam.get("model", "custom"), MODELS["custom"]).get(key)


def expand_cameras(cfg: dict) -> list[dict]:
    """แตกกล้องที่เปิดใช้งานเป็นช่องภาพ (หนึ่งช่องต่อหนึ่งเลนส์)"""
    tiles = []
    for cam in cfg.get("cameras", []):
        if not cam.get("enabled", True) or not cam.get("ip"):
            continue
        for lens in lenses_of(cam):
            if not lens.get("hd"):
                continue
            tiles.append({
                "name": f"{cam['name']} {lens['label']}".strip(),
                "device": cam["name"],
                "model": cam.get("model", "custom"),
                "ip": cam["ip"],
                "port": int(setting(cam, "port") or 554),
                "username": cam.get("username", cfg.get("username", "")),
                "password": cam.get("password", cfg.get("password", "")),
                "hd_path": lens["hd"], "sd_path": lens.get("sd") or lens["hd"],
                "crop": lens.get("crop"),
                "transport": setting(cam, "transport") or "auto",
                "ptz": bool(cam.get("ptz", MODELS.get(cam.get("model"), {}).get("ptz", False))),
                "onvif_port": int(setting(cam, "onvif_port") or 80),
            })
    return tiles


def frame_to_qimage(f, w=None, h=None):
    """av.VideoFrame → QImage (ย่อได้) – สร้างจากข้อมูลดิบพร้อมระยะต่อแถวจริง (บางความกว้างมีช่องว่างท้ายแถว)"""
    from PySide6.QtGui import QImage  # noqa: PLC0415
    rf = f.reformat(width=w or f.width, height=h or f.height, format="rgb24", interpolation="BILINEAR")
    p = rf.planes[0]
    return QImage(bytes(p), rf.width, rf.height, p.line_size, QImage.Format.Format_RGB888).copy()


def rtsp_url(ip, port, user, password, path) -> str:
    auth = f"{quote(user or '', safe='')}:{quote(password or '', safe='')}@" if user or password else ""
    return f"rtsp://{auth}{ip}:{port}/{path.lstrip('/')}"


# ---------------------------------------------------------------- แปลง config เก่า
def migrate(cfg: dict) -> tuple[dict, bool]:
    """config แบบเก่า (หนึ่งรายการต่อหนึ่งเลนส์ มี hd_path) → แบบใหม่ (หนึ่งรายการต่อกล้องหนึ่งตัว)"""
    cams = cfg.get("cameras", [])
    if not any("hd_path" in c and "model" not in c for c in cams):
        return cfg, False
    top_user, top_pw = cfg.get("username", ""), cfg.get("password", "")
    out, groups, order = [], {}, []
    for c in cams:
        if "model" in c:
            out.append(("keep", c))
            continue
        key = (c.get("ip"), c.get("device") or c.get("name"))
        if key not in groups:
            groups[key] = []
            order.append(key)
            out.append(("group", key))
        groups[key].append(c)
    new = []
    for kind, val in out:
        if kind == "keep":
            new.append(val)
            continue
        g = groups[val]
        first = g[0]
        base = {"name": first.get("device") or first["name"], "ip": first.get("ip", ""),
                "username": first.get("username", top_user), "password": first.get("password", top_pw),
                "enabled": all(c.get("enabled", True) for c in g)}
        paths = [(c.get("hd_path"), c.get("sd_path")) for c in g]
        if len(g) == 1 and first.get("split") and first.get("hd_path") == "onvif1":
            new.append({**base, "name": first["name"], "model": "yoosee_dual", "ptz": False})
        elif len(g) == 1 and first.get("hd_path") == "onvif1":
            new.append({**base, "name": first["name"], "model": "yoosee_single", "ptz": False})
        elif paths == [("stream1", "stream2"), ("stream6", "stream7")]:
            new.append({**base, "model": "tapo_dual", "ptz": first.get("ptz", True)})
        elif paths == [("stream1", "stream2")]:
            new.append({**base, "model": "tapo_single", "ptz": first.get("ptz", True)})
        else:
            for c in g:  # ไม่รู้จัก – เก็บไว้ตามเดิมแบบกำหนดเอง
                new.append({**base, "name": c["name"], "model": "custom",
                            "hd_path": c.get("hd_path", ""), "sd_path": c.get("sd_path", ""),
                            "port": c.get("port", 554), "transport": c.get("transport", "tcp"),
                            "layout": "split" if c.get("split") else "single",
                            "ptz": c.get("ptz", False), "onvif_port": c.get("onvif_port", 80)})
    out_cfg = {k: v for k, v in cfg.items() if k not in ("cameras", "username", "password")}
    out_cfg["version"] = 2
    out_cfg["cameras"] = new
    return out_cfg, True


# ---------------------------------------------------------------- ค้นหากล้องในวง LAN
SCAN_PORTS = [554, 10554, 2020, 5000, 8000, 9000, 37777, 80]


def local_networks() -> list[ipaddress.IPv4Network]:
    ips = set()
    try:  # หา IP ของเครื่องที่ใช้ออกเน็ต (ไม่ได้ส่งข้อมูลจริง)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))
        ips.add(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    nets = []
    for ip in ips:
        if ip.startswith("127.") or ip.startswith("169.254."):
            continue
        net = ipaddress.ip_network(f"{ip}/24", strict=False)
        if net not in nets:
            nets.append(net)
    return nets


def _open(ip, port, timeout):
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def guess_model(ports: set[int]) -> str:
    if 2020 in ports:
        return "tapo_single"
    if 10554 in ports:
        return "vstarcam"
    if 37777 in ports:
        return "dahua"
    if 9000 in ports:
        return "reolink"
    if 5000 in ports:
        return "yoosee_single"
    if 8000 in ports:
        return "hikvision"
    return "custom"


def scan_lan(progress=None, timeout=0.5) -> list[dict]:
    """สแกน /24 ของเครื่องนี้ หาเครื่องที่เปิด RTSP แล้วเดายี่ห้อจากพอร์ตที่เปิด"""
    hosts = [str(h) for net in local_networks() for h in net.hosts()]
    found = []
    done = 0

    def check(ip):
        if not (_open(ip, 554, timeout) or _open(ip, 10554, timeout)):
            return None
        ports = {p for p in SCAN_PORTS if _open(ip, p, timeout)}
        return {"ip": ip, "ports": sorted(ports), "model": guess_model(ports)}

    with ThreadPoolExecutor(max_workers=64) as ex:
        for r in ex.map(check, hosts):
            done += 1
            if progress and done % 16 == 0:
                progress(done, len(hosts))
            if r:
                found.append(r)
    return found


# ---------------------------------------------------------------- ทดสอบสตรีม
AUTH_ERR = "Username หรือ Password ไม่ถูกต้อง"


def is_auth_error(e) -> bool:
    low = str(e).lower()
    return "401" in low or "unauthorized" in low


def is_tapo(cam: dict) -> bool:
    return str(cam.get("model", "")).startswith("tapo") or int(cam.get("onvif_port") or 0) == 2020


def auth_hint(cam: dict, onvif_ok: bool = False) -> str:
    """คำแนะนำเมื่อกล้องตอบ 401 – ใช้ในหน้าตั้งค่า"""
    tips = []
    if is_tapo(cam):
        if onvif_ok:
            tips.append("ตรวจหาอัตโนมัติ (ONVIF) ใช้รหัสนี้ได้ แสดงว่ารหัสถูก แต่กล้องไม่ให้ดูภาพแบบ RTSP")
        tips.append("แอป Tapo → ฉัน (Me) → Tapo Lab → เปิด \"Third-Party Compatibility\" "
                    "(ความเข้ากันได้กับอุปกรณ์อื่น) แล้วรอกล้องรีสตาร์ท ~1 นาที")
        tips.append("ใช้ \"บัญชีกล้อง (Camera Account)\" จากแอป Tapo → กล้อง → ⚙ → ตั้งค่าขั้นสูง → บัญชีกล้อง "
                    "ไม่ใช่อีเมล/รหัสบัญชี TP-Link")
        tips.append("ถ้าลองผิดหลายครั้ง กล้องอาจล็อกชั่วคราว ~30 นาที – เปลี่ยนรหัสแล้วรอสักพักค่อยทดสอบ")
    else:
        tips.append("ตรวจตัวพิมพ์เล็ก/ใหญ่ และดูว่ามีช่องว่างหน้า-หลังรหัสไหม")
    if (cam.get("password") or "") != (cam.get("password") or "").strip():
        tips.insert(0, "รหัสผ่านมีช่องว่างอยู่หน้าหรือท้าย – ลองลบออก")
    return "\n".join("• " + t for t in tips)


def probe_stream(url: str, transports=("tcp", "udp"), timeout=12.0):
    """เปิดสตรีมแล้วถอดรหัสหนึ่งภาพ → (transport ที่ใช้ได้, ภาพ BGR) หรือโยน RuntimeError พร้อมเหตุผล"""
    import av  # noqa: PLC0415
    last = "ไม่ทราบสาเหตุ"
    for tr in transports:
        t0 = time.time()
        try:
            c = av.open(url, options=FFMPEG_OPTS[tr], timeout=(timeout, 8))
            try:
                for f in c.decode(video=0):
                    return tr, frame_to_qimage(f)
            finally:
                c.close()
        except Exception as e:  # noqa: BLE001
            msg = _hide_pw(e)
            low = str(e).lower()
            if is_auth_error(e):
                raise RuntimeError(AUTH_ERR) from None
            if "404" in low or "not found" in low:
                last = "ไม่พบ path ของสตรีม (รุ่นไม่ตรง?)"
            elif time.time() - t0 >= timeout - 1:
                last = "กล้องไม่ตอบ (หมดเวลา)"
            else:
                last = msg
    raise RuntimeError(last)


def split_rtsp_uri(uri: str) -> tuple[int, str]:
    """rtsp://ip:port/path?q → (port, 'path?q')"""
    u = urlsplit(uri)
    path = u.path.lstrip("/") + (f"?{u.query}" if u.query else "")
    return (u.port or 554), path

"""ONVIF PTZ แบบเบาๆ (ไม่ต้องลงไลบรารีเพิ่ม) สำหรับหมุนกล้อง Tapo

Tapo เปิด ONVIF ที่พอร์ต 2020 และใช้ user/password เดียวกับ Camera Account
"""
import base64
import hashlib
import os
import re
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

NS = {
    "s": "http://www.w3.org/2003/05/soap-envelope",
    "tds": "http://www.onvif.org/ver10/device/wsdl",
    "trt": "http://www.onvif.org/ver10/media/wsdl",
    "tptz": "http://www.onvif.org/ver20/ptz/wsdl",
    "tt": "http://www.onvif.org/ver10/schema",
}


class OnvifError(Exception):
    pass


class OnvifPTZ:
    def __init__(self, ip: str, username: str, password: str, port: int = 2020, timeout: float = 4.0):
        self.username = username
        self.password = password
        self.timeout = timeout
        self.device_url = f"http://{ip}:{port}/onvif/device_service"
        self.ptz_url = self.device_url
        self.media_url = self.device_url
        self.profile = None
        self.time_offset = timedelta(0)
        self.presets: list[tuple[str, str]] = []  # (token, name)

    # ---------- SOAP ----------
    def _security(self) -> str:
        nonce = os.urandom(16)
        created = (datetime.now(timezone.utc) + self.time_offset).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        digest = base64.b64encode(hashlib.sha1(nonce + created.encode() + self.password.encode()).digest()).decode()
        return (
            '<Security s:mustUnderstand="1" xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd">'
            "<UsernameToken>"
            f"<Username>{_esc(self.username)}</Username>"
            '<Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</Password>"
            '<Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{base64.b64encode(nonce).decode()}</Nonce>"
            f'<Created xmlns="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">{created}</Created>'
            "</UsernameToken></Security>"
        )

    def _call(self, url: str, body: str, auth: bool = True) -> ET.Element:
        header = f"<s:Header>{self._security()}</s:Header>" if auth else ""
        env = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            f'<s:Envelope xmlns:s="{NS["s"]}" xmlns:tds="{NS["tds"]}" xmlns:trt="{NS["trt"]}" '
            f'xmlns:tptz="{NS["tptz"]}" xmlns:tt="{NS["tt"]}">'
            f"{header}<s:Body>{body}</s:Body></s:Envelope>"
        )
        req = urllib.request.Request(url, data=env.encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/soap+xml; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = r.read()
        except urllib.error.HTTPError as e:
            data = e.read()
            root = _parse(data)
            reason = root.find(".//s:Reason/s:Text", NS) if root is not None else None
            raise OnvifError(reason.text if reason is not None else f"HTTP {e.code}") from None
        except OSError as e:
            raise OnvifError(f"ติดต่อกล้องไม่ได้ ({e})") from None
        root = _parse(data)
        if root is None:
            raise OnvifError("กล้องตอบกลับไม่ใช่ ONVIF")
        fault = root.find(".//s:Fault", NS)
        if fault is not None:
            txt = fault.find(".//s:Text", NS)
            raise OnvifError(txt.text if txt is not None else "ONVIF fault")
        return root

    # ---------- setup ----------
    def connect(self):
        # 1) ปรับเวลาให้ตรงกับกล้อง (รหัสแบบ digest จะถูกปฏิเสธถ้าเวลาต่างกันมาก)
        try:
            root = self._call(self.device_url, "<tds:GetSystemDateAndTime/>", auth=False)
            utc = root.find(".//tt:UTCDateTime", NS)
            if utc is not None:
                d, t = utc.find("tt:Date", NS), utc.find("tt:Time", NS)
                cam = datetime(int(_t(d, "Year")), int(_t(d, "Month")), int(_t(d, "Day")),
                               int(_t(t, "Hour")), int(_t(t, "Minute")), int(_t(t, "Second")), tzinfo=timezone.utc)
                self.time_offset = cam - datetime.now(timezone.utc)
        except (OnvifError, TypeError, ValueError):
            pass

        # 2) หา URL ของ Media และ PTZ service
        try:
            root = self._call(self.device_url, '<tds:GetCapabilities><tds:Category>All</tds:Category></tds:GetCapabilities>')
            media = root.find(".//tt:Media/tt:XAddr", NS)
            ptz = root.find(".//tt:PTZ/tt:XAddr", NS)
            if media is not None and media.text:
                self.media_url = media.text.strip()
            if ptz is not None and ptz.text:
                self.ptz_url = ptz.text.strip()
        except OnvifError as e:
            if "auth" in str(e).lower() or "not authorized" in str(e).lower():
                raise OnvifError("user/password ไม่ถูกต้อง (ใช้ Camera Account)") from None
            raise

        # 3) เลือก profile ที่ควบคุม PTZ ได้
        root = self._call(self.media_url, "<trt:GetProfiles/>")
        profiles = root.findall(".//trt:Profiles", NS)
        if not profiles:
            raise OnvifError("ไม่พบ media profile")
        chosen = next((p for p in profiles if p.find("tt:PTZConfiguration", NS) is not None), profiles[0])
        self.profile = chosen.get("token")
        self.refresh_presets()
        return self

    def refresh_presets(self):
        try:
            root = self._call(self.ptz_url, f"<tptz:GetPresets><tptz:ProfileToken>{_esc(self.profile)}</tptz:ProfileToken></tptz:GetPresets>")
            self.presets = [(p.get("token"), (p.findtext("tt:Name", default="", namespaces=NS) or p.get("token")))
                            for p in root.findall(".//tptz:Preset", NS)]
        except OnvifError:
            self.presets = []
        return self.presets

    # ---------- commands ----------
    def move(self, pan: float, tilt: float):
        """หมุนต่อเนื่อง: pan/tilt อยู่ในช่วง -1..1 (ขวา/ขึ้น = บวก)"""
        self._call(self.ptz_url,
                   f"<tptz:ContinuousMove><tptz:ProfileToken>{_esc(self.profile)}</tptz:ProfileToken>"
                   f'<tptz:Velocity><tt:PanTilt x="{pan:.2f}" y="{tilt:.2f}"/></tptz:Velocity>'
                   "</tptz:ContinuousMove>")

    def stop(self):
        self._call(self.ptz_url,
                   f"<tptz:Stop><tptz:ProfileToken>{_esc(self.profile)}</tptz:ProfileToken>"
                   "<tptz:PanTilt>true</tptz:PanTilt><tptz:Zoom>true</tptz:Zoom></tptz:Stop>")

    def goto_preset(self, token: str):
        self._call(self.ptz_url,
                   f"<tptz:GotoPreset><tptz:ProfileToken>{_esc(self.profile)}</tptz:ProfileToken>"
                   f"<tptz:PresetToken>{_esc(token)}</tptz:PresetToken></tptz:GotoPreset>")


ONVIF_PORTS = [2020, 80, 8000, 5000, 8080, 8899, 10080, 888]


def discover_streams(ip: str, username: str, password: str, ports=None, timeout: float = 4.0) -> dict:
    """ถามกล้องผ่าน ONVIF ว่ามีสตรีมอะไรบ้าง
    คืนค่า {"onvif_port", "ptz", "streams": [(ชื่อ, กว้าง, สูง, rtsp_uri), ...]} เรียงจากใหญ่ไปเล็ก"""
    import socket  # noqa: PLC0415
    errors = []
    for port in ports or ONVIF_PORTS:
        try:
            socket.create_connection((ip, port), timeout=1.0).close()
        except OSError:
            continue
        cam = OnvifPTZ(ip, username, password, port, timeout)
        try:
            cam.connect()
        except OnvifError as e:
            errors.append(f"พอร์ต {port}: {e}")
            continue
        root = cam._call(cam.media_url, "<trt:GetProfiles/>")
        streams = []
        for p in root.findall(".//trt:Profiles", NS):
            token = p.get("token")
            res = p.find(".//tt:VideoEncoderConfiguration/tt:Resolution", NS)
            w = int(res.findtext("tt:Width", "0", NS)) if res is not None else 0
            h = int(res.findtext("tt:Height", "0", NS)) if res is not None else 0
            try:
                r = cam._call(cam.media_url,
                              "<trt:GetStreamUri><trt:StreamSetup><tt:Stream>RTP-Unicast</tt:Stream>"
                              "<tt:Transport><tt:Protocol>RTSP</tt:Protocol></tt:Transport></trt:StreamSetup>"
                              f"<trt:ProfileToken>{_esc(token)}</trt:ProfileToken></trt:GetStreamUri>")
            except OnvifError:
                continue
            uri = r.findtext(".//tt:Uri", "", NS).strip()
            if uri.startswith("rtsp://"):
                streams.append((p.findtext("tt:Name", token, NS), w, h, uri))
        if streams:
            streams.sort(key=lambda s: s[1] * s[2], reverse=True)
            has_ptz = any(p.find("tt:PTZConfiguration", NS) is not None for p in root.findall(".//trt:Profiles", NS))
            return {"onvif_port": port, "ptz": has_ptz, "streams": streams}
        errors.append(f"พอร์ต {port}: ไม่มีสตรีม RTSP")
    if errors:
        raise OnvifError(" · ".join(errors[:2]))
    raise OnvifError("ไม่พบ ONVIF ที่กล้องนี้ (ลองเปิด ONVIF ในแอป/หน้าเว็บของกล้อง)")


def _parse(data: bytes):
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        return None


def _t(el, tag):
    return el.findtext(f"tt:{tag}", namespaces=NS)


def _esc(s) -> str:
    return re.sub(r"[&<>\"']", lambda m: {"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&apos;"}[m.group()], str(s))

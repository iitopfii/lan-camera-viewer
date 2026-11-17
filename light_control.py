"""ไฟและโหมดกลางคืนของกล้อง Tapo (ผ่านช่องควบคุมของ Tapo ในวง LAN พอร์ต 443 ด้วยไลบรารี pytapo)

ต้องใช้รหัสผ่านบัญชี TP-Link (บัญชีที่ล็อกอินแอป Tapo) – Camera Account สั่งไฟไม่ได้
Yoosee สั่งไฟได้เฉพาะผ่านแอป/คลาวด์ของ Yoosee จึงไม่มีในแผงนี้
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QButtonGroup, QComboBox, QFrame, QHBoxLayout, QPushButton, QVBoxLayout

import theme

# โหมดกลางคืน: (คีย์ภายใน, ชื่อบนปุ่ม, คำอธิบาย)
NIGHT_MODES = [
    ("auto", "อัตโนมัติ", "กล้องเลือกเองตามแสง"),
    ("ir", "อินฟราเรด", "กลางคืนภาพขาวดำ ไม่เปิดไฟ"),
    ("color", "ภาพสี", "กลางคืนเปิดสปอตไลท์ให้ภาพเป็นสี"),
]
# ค่าที่กล้องใช้ (รุ่นใหม่ใช้ night_vision_mode, รุ่นเก่าใช้ inf_type)
_NV = {"auto": "md_night_vision", "ir": "inf_night_vision", "color": "wtl_night_vision"}
_NV_BACK = {v: k for k, v in _NV.items()}
_INF = {"auto": "auto", "ir": "on", "color": "off"}
_INF_BACK = {v: k for k, v in _INF.items()}


class LightError(Exception):
    pass


class TapoLight:
    """ตัวควบคุมไฟของกล้อง Tapo หนึ่งตัว (เชื่อมต่อครั้งแรกตอนใช้งาน)"""

    def __init__(self, ip: str, cloud_password: str):
        self.ip = ip
        self.cloud_password = cloud_password
        self.t = None
        self.nv_style = None  # "nv" หรือ "inf"

    def _tapo(self):
        if self.t is None:
            if not self.cloud_password:
                raise LightError("ยังไม่ได้ใส่รหัสบัญชี TP-Link (ตั้งค่า → ทั่วไป)")
            from pytapo import Tapo  # noqa: PLC0415
            try:
                self.t = Tapo(self.ip, "admin", self.cloud_password, printWarnInformation=False)
            except Exception as e:  # noqa: BLE001
                msg = str(e)
                if "auth" in msg.lower() or "password" in msg.lower():
                    raise LightError("รหัสบัญชี TP-Link ไม่ถูกต้อง (ใช้รหัสที่ล็อกอินแอป Tapo)") from None
                if "suspended" in msg.lower() or "temporary" in msg.lower():
                    raise LightError("กล้องล็อกชั่วคราวเพราะรหัสผิดหลายครั้ง – รอ 30 นาทีแล้วลองใหม่") from None
                raise LightError(f"ติดต่อกล้องไม่ได้ ({msg[:80]})") from None
        return self.t

    def status(self) -> dict:
        """อ่านสถานะ → {"light": bool|None, "mode": "auto"/"ir"/"color"/None}"""
        t = self._tapo()
        out = {"light": None, "mode": None}
        try:
            out["light"] = bool(t.getForceWhitelampState())
        except Exception:  # noqa: BLE001
            pass
        try:
            nv = t.getNightVisionModeConfig()["image"]["switch"]["night_vision_mode"]
            out["mode"] = _NV_BACK.get(nv)
            self.nv_style = "nv"
        except Exception:  # noqa: BLE001
            try:
                out["mode"] = _INF_BACK.get(t.getDayNightMode())
                self.nv_style = "inf"
            except Exception:  # noqa: BLE001
                pass
        return out

    def set_light(self, on: bool):
        try:
            self._tapo().setForceWhitelampState(on)
        except LightError:
            raise
        except Exception as e:  # noqa: BLE001
            raise LightError(f"สั่งไฟไม่ได้ ({str(e)[:80]})") from None

    def set_mode(self, mode: str):
        t = self._tapo()
        try:
            if self.nv_style == "inf":
                t.setDayNightMode(_INF[mode])
            else:
                t.setNightVisionModeConfig(_NV[mode])
        except Exception as e:  # noqa: BLE001
            raise LightError(f"เปลี่ยนโหมดไม่ได้ ({str(e)[:80]})") from None


class LightPanel(QFrame):
    """แผงไฟ: เปิด/ปิดสปอตไลท์ และเลือกโหมดกลางคืน (เฉพาะกล้อง Tapo)"""

    class _Bridge(QObject):
        status = Signal(str, str)      # (ip, ข้อความ)
        state = Signal(str, object)    # (ip, dict สถานะ)

    def __init__(self, devices: list[tuple[str, str]], cloud_password: str, open_settings=None):
        super().__init__()
        self.setProperty("card", True)
        self.setFixedWidth(232)
        self.devices = devices  # [(ชื่อ, ip)]
        self.ctrl = {ip: TapoLight(ip, cloud_password) for _, ip in devices}
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.bridge = self._Bridge()
        self.bridge.status.connect(self._on_status)
        self.bridge.state.connect(self._on_state)
        self._updating = False

        self.cam_box = QComboBox()
        self.cam_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for name, _ip in devices:
            self.cam_box.addItem(name)
        self.cam_box.currentIndexChanged.connect(lambda _i: self.refresh())

        self.btn_light = QPushButton("เปิดสปอตไลท์")
        self.btn_light.setObjectName("lightBtn")
        self.btn_light.setIcon(theme.icon("bulb"))
        self.btn_light.setIconSize(theme.ICON_SIZE)
        self.btn_light.setCheckable(True)
        self.btn_light.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_light.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_light.toggled.connect(self._light_clicked)

        self.mode_btns = {}
        group = QButtonGroup(self)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for i, (key, text, tip) in enumerate(NIGHT_MODES):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setToolTip(tip)
            b.setProperty("seg", "left" if i == 0 else "right" if i == len(NIGHT_MODES) - 1 else "mid")
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.clicked.connect(lambda _c, k=key: self._mode_clicked(k))
            group.addButton(b)
            seg.addWidget(b)
            self.mode_btns[key] = b

        self.status = theme.label("", "muted")
        self.status.setWordWrap(True)
        self.btn_settings = theme.btn("ใส่รหัส TP-Link", "settings", kind="ghost")
        self.btn_settings.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if open_settings:
            self.btn_settings.clicked.connect(open_settings)
        self.btn_settings.setVisible(not cloud_password)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 14, 16, 14)
        v.setSpacing(10)
        v.addWidget(theme.label("ไฟ / โหมดกลางคืน", "title"))
        v.addWidget(self.cam_box)
        v.addWidget(self.btn_light)
        v.addWidget(theme.label("โหมดกลางคืน", "section"))
        v.addLayout(seg)
        v.addWidget(self.status)
        v.addWidget(self.btn_settings)
        self._enable(False)
        self.refresh()

    # ---- ช่วยเหลือ
    @property
    def ip(self):
        i = self.cam_box.currentIndex()
        return self.devices[i][1] if 0 <= i < len(self.devices) else None

    def _enable(self, on):
        self.btn_light.setEnabled(on)
        for b in self.mode_btns.values():
            b.setEnabled(on)

    def _submit(self, ip, fn, ok_msg=None):
        def job():
            try:
                fn()
                if ok_msg:
                    self.bridge.status.emit(ip, ok_msg)
                self.bridge.state.emit(ip, self.ctrl[ip].status())
            except LightError as e:
                self.bridge.status.emit(ip, f"⚠ {e}")
            except Exception as e:  # noqa: BLE001
                self.bridge.status.emit(ip, f"⚠ ผิดพลาด: {str(e)[:80]}")
        self.pool.submit(job)

    # ---- คำสั่ง
    def select_by_ip(self, ip):
        for i, (_n, dip) in enumerate(self.devices):
            if dip == ip and i != self.cam_box.currentIndex():
                self.cam_box.setCurrentIndex(i)

    def refresh(self):
        ip = self.ip
        if not ip:
            return
        self._enable(False)
        self.status.setText("กำลังอ่านสถานะไฟ…")
        self._submit(ip, lambda: None)

    def _light_clicked(self, on):
        if self._updating or not self.ip:
            return
        self.btn_light.setText("ปิดสปอตไลท์" if on else "เปิดสปอตไลท์")
        ip = self.ip
        self._submit(ip, lambda: self.ctrl[ip].set_light(on), "เปิดไฟแล้ว" if on else "ปิดไฟแล้ว")

    def _mode_clicked(self, key):
        if self._updating or not self.ip:
            return
        ip = self.ip
        name = next(t for k, t, _ in NIGHT_MODES if k == key)
        self._submit(ip, lambda: self.ctrl[ip].set_mode(key), f"โหมดกลางคืน: {name}")

    # ---- ผลจากเธรด
    def _on_status(self, ip, text):
        if ip == self.ip:
            self.status.setText(text)
            if text.startswith("⚠"):
                self._enable(False)
                self.btn_settings.setVisible("TP-Link" in text)

    def _on_state(self, ip, st):
        if ip != self.ip:
            return
        self._updating = True
        self._enable(True)
        if st.get("light") is None:
            self.btn_light.setEnabled(False)
            self.btn_light.setToolTip("กล้องรุ่นนี้ไม่รองรับการสั่งสปอตไลท์")
        else:
            self.btn_light.setChecked(st["light"])
            self.btn_light.setText("ปิดสปอตไลท์" if st["light"] else "เปิดสปอตไลท์")
        if st.get("mode") in self.mode_btns:
            self.mode_btns[st["mode"]].setChecked(True)
        elif st.get("mode") is None:
            for b in self.mode_btns.values():
                b.setEnabled(False)
        self._updating = False
        if self.status.text().startswith("กำลังอ่าน"):
            self.status.setText("พร้อมใช้งาน")
        self.btn_settings.hide()

    def shutdown(self):
        self.pool.shutdown(wait=False, cancel_futures=True)

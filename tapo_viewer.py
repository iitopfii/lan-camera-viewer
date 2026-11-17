"""LAN Camera Viewer - ดูกล้องวงจรปิด (Tapo, Yoosee, Hikvision, Dahua ฯลฯ) ในวง LAN บน Windows

ใช้งาน:  ดับเบิลคลิก run.bat
ตั้งค่ากล้องจากปุ่ม "ตั้งค่า" ในโปรแกรม (เก็บไว้ใน config.json)
"""
import json
import math
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import re  # noqa: E402

import av  # noqa: E402  (อ่านสตรีม RTSP – ตั้งค่าแยกต่อกล้องได้)
from concurrent.futures import ThreadPoolExecutor  # noqa: E402

av.logging.set_level(None)  # ปิด log ทั้งหมด – ห้ามตั้งเป็นระดับอื่น: callback ของ PyAV ต้องแย่ง GIL จากเธรดถอดรหัส ทำให้โปรแกรมค้าง

from PySide6.QtCore import QObject, QSize, Qt, QThread, QTimer, Signal, Slot  # noqa: E402
from PySide6.QtGui import QImage, QPixmap, QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication, QButtonGroup, QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QMainWindow,
    QMessageBox, QPushButton, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

import theme  # noqa: E402

from camera_models import FFMPEG_OPTS, _hide_pw, expand_cameras, is_auth_error, frame_to_qimage, migrate, rtsp_url  # noqa: E402
from light_control import LightPanel  # noqa: E402
from onvif_ptz import OnvifError, OnvifPTZ  # noqa: E402

from app_info import APP_NAME, ASSETS, DATA_DIR, media_dir  # noqa: E402

CONFIG_PATH = DATA_DIR / "config.json"


def load_config() -> dict | None:
    """อ่าน config.json (ไม่มีไฟล์ = None) และแปลงรูปแบบเก่าเป็นแบบใหม่ให้อัตโนมัติ (สำรองไฟล์เดิมไว้)"""
    if not CONFIG_PATH.exists():
        return None
    with open(CONFIG_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    cfg, changed = migrate(cfg)
    if changed:
        backup = DATA_DIR / "config.old.json"
        if not backup.exists():
            backup.write_bytes(CONFIG_PATH.read_bytes())
        save_config(cfg)
    return cfg


def save_config(cfg: dict):
    if CONFIG_PATH.exists():
        (DATA_DIR / "config.backup.json").write_bytes(CONFIG_PATH.read_bytes())
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(CONFIG_PATH)


def build_url(cfg: dict, cam: dict, quality: str) -> str:
    path = cam["hd_path"] if quality == "hd" else cam["sd_path"]
    return rtsp_url(cam["ip"], cam.get("port", 554), cam.get("username", ""), cam.get("password", ""), path)


def wait_threads(threads, timeout=10.0):
    """รอเธรดหยุดด้วย time.sleep (ปล่อย GIL) – ห้ามใช้ QThread.wait() ตรงๆ
    เพราะมันถือ GIL ไว้ ทำให้เธรดที่กำลังถอดรหัสภาพหยุดตัวเองไม่ได้ แล้วค้างทั้งคู่"""
    end = time.time() + timeout
    while time.time() < end and any(t.isRunning() for t in threads):
        time.sleep(0.05)


class StreamWorker(QThread):
    """อ่านภาพจาก RTSP ในเธรดแยก และต่อใหม่อัตโนมัติเมื่อหลุด

    ประหยัดเครื่อง:
    - ย่อภาพเหลือเท่าขนาดช่องบนจอตั้งแต่ในเธรดนี้ (หน้าจอไม่ต้องย่อภาพใหญ่เอง)
    - ช่องที่ไม่ได้แสดงอยู่ (เช่น ตอนขยายช่องอื่นเต็มจอ) ถอดรหัสเฉพาะภาพหลัก (keyframe) ไม่กี่ภาพต่อวินาที
    - อัดวิดีโอโดยคัดลอกข้อมูลจากกล้องลงไฟล์ตรงๆ (ไม่เข้ารหัสใหม่ = ไม่กิน CPU และได้คุณภาพเต็ม)
    """
    status = Signal(str)

    def __init__(self, url: str, transport: str = "tcp"):
        super().__init__()
        self.url = url
        # "auto" = ลอง TCP ก่อน ถ้าต่อไม่ได้สลับไป UDP (และสลับไปมาจนกว่าจะได้)
        self.transports = ["tcp", "udp"] if transport == "auto" else [transport if transport in FFMPEG_OPTS else "tcp"]
        self._ti = 0
        self._running = True
        self.last_frame = None  # av.VideoFrame ภาพเต็มล่าสุด (แปลงเป็นรูปเฉพาะตอนถ่ายภาพ)
        self.latest_image: QImage | None = None  # ภาพย่อสำหรับแสดงบนจอ
        self.src_size = (0, 0)
        self.frame_id = 0  # เพิ่มทุกครั้งที่มีภาพใหม่ หน้าจอจะมาดึงภาพล่าสุดเอง
        self.target = (640, 360)  # ขนาดที่จอต้องการ (MainWindow อัปเดตให้)
        self.active = True  # มีช่องที่แสดงสตรีมนี้อยู่หรือไม่
        # การอัด: หน้าจอแค่ตั้งค่า _rec_path (เริ่ม) / None (หยุด) เธรดนี้เป็นคนเปิด-เขียน-ปิดไฟล์เองทั้งหมด
        self._rec_path: Path | None = None
        self._out = None  # (output container, output stream, เวลาเริ่ม, path ที่ขอ, ค่าล่าสุดของ pts)
        # แพ็กเก็ตตั้งแต่ภาพหลักล่าสุด [(packet, เวลาที่มาถึง)] – กดอัดแล้วเริ่มได้ทันที ไม่ต้องรอภาพหลักถัดไป
        self._gop: list = []

    def request_stop(self):
        self._running = False

    def stop(self):
        self._running = False
        wait_threads([self])
        self.stop_record()

    def snapshot_image(self) -> QImage | None:
        f = self.last_frame
        return frame_to_qimage(f) if f is not None else None

    @property
    def recording(self) -> bool:
        return self._rec_path is not None

    def start_record(self, path: Path) -> bool:
        if self._rec_path is not None:  # มีอีกช่องที่ใช้สตรีมเดียวกันสั่งอัดไปแล้ว
            return True
        if self.last_frame is None:
            return False
        self._rec_path = path  # เธรดสตรีมจะเริ่มเขียนที่ภาพหลัก (keyframe) ถัดไป
        return True

    def stop_record(self):
        self._rec_path = None

    def _display_size(self, w, h):
        tw, th = self.target
        s = min(tw / w, th / h, 1.0)  # ย่ออย่างเดียว ไม่ขยาย
        return max(2, int(w * s) // 2 * 2), max(2, int(h * s) // 2 * 2)

    # ---------- การอัด (ทำในเธรดนี้เท่านั้น) ----------
    def _close_out(self):
        if self._out is not None:
            try:
                self._out[0].close()
            except Exception:  # noqa: BLE001
                pass
            self._out = None

    def _record_packet(self, packet, arrived, stream):
        path = self._rec_path
        if self._out is not None and (path is None or self._out[3] != path):
            self._close_out()
        if path is None:
            return
        if self._out is None:
            if not (self._gop and self._gop[0][0].is_keyframe):
                return  # ไฟล์ต้องเริ่มที่ภาพหลัก ไม่งั้นเปิดดูไม่ได้
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                actual, i = path, 2
                while actual.exists():  # สัญญาณหลุดแล้วต่อกลับระหว่างอัด → เริ่มไฟล์ใหม่ ไม่เขียนทับ
                    actual = path.with_name(f"{path.stem}_{i}{path.suffix}")
                    i += 1
                out = av.open(str(actual), "w", format="mp4")
                ost = out.add_stream_from_template(stream)
                self._out = [out, ost, self._gop[0][1], path, -1]
            except Exception:  # noqa: BLE001
                self._rec_path = None
                return
            for p, t in self._gop:  # ย้อนไปเขียนตั้งแต่ภาพหลักล่าสุด (รวมแพ็กเก็ตนี้แล้ว)
                self._mux(p, t)
            return
        self._mux(packet, arrived)

    def _mux(self, packet, arrived):
        """ใช้เวลาที่แพ็กเก็ตมาถึงเป็นเวลาในไฟล์ – กล้องบางยี่ห้อ (เช่น Yoosee) ส่งเวลามาไม่ครบ/ย้อนไปมา"""
        out, ost, t0, _, last = self._out
        ts = int((arrived - t0) / ost.time_base)
        if ts <= last:
            ts = last + 1
        self._out[4] = ts
        packet.pts = packet.dts = ts
        packet.stream = ost
        try:
            out.mux(packet)
        except Exception:  # noqa: BLE001  (แพ็กเก็ตเสีย – ข้ามไป)
            pass

    def run(self):
        while self._running:
            self.status.emit("กำลังเชื่อมต่อ…")
            tr = self.transports[self._ti % len(self.transports)]
            try:
                container = av.open(self.url, options=FFMPEG_OPTS[tr], timeout=(20 if tr == "udp" else 8, 8))
            except Exception as e:  # noqa: BLE001
                self._ti += 1
                if is_auth_error(e):
                    # รหัสผิด: ลองถี่ๆ ไม่ช่วย และทำให้กล้องบางรุ่น (Tapo) ล็อกชั่วคราว → เว้น 60 วินาที
                    self.status.emit("เชื่อมต่อไม่ได้ – กล้องไม่รับ Username/Password (401)\n"
                                     "ไปที่ ตั้งค่า → เลือกกล้อง → ทดสอบภาพ เพื่อดูวิธีแก้ · จะลองใหม่ใน 60 วินาที")
                    self._sleep(60)
                    continue
                self.status.emit(f"เชื่อมต่อไม่ได้ ({_hide_pw(e)}) – จะลองใหม่ใน 5 วินาที")
                self._sleep(5)
                continue
            try:
                stream = container.streams.video[0]
                cc = stream.codec_context
                cc.thread_type = "AUTO"
                cc.thread_count = 2  # หลายกล้องพร้อมกัน ใช้ 2 เธรดต่อกล้องพอ ไม่แย่งกันเอง
                self.status.emit("")
                skipping = False
                for packet in container.demux(stream):
                    if not self._running:
                        break
                    want_skip = not self.active
                    if want_skip != skipping:
                        try:
                            cc.skip_frame = "NONKEY" if want_skip else "DEFAULT"
                            skipping = want_skip
                        except Exception:  # noqa: BLE001
                            pass
                    try:
                        frames = packet.decode()
                    except av.error.InvalidDataError:
                        frames = []  # ข้อมูลเสียบางส่วน (UDP หาย) – ข้ามไป ไม่ต้องตัดการเชื่อมต่อ
                    for f in frames:
                        self.last_frame = f
                        self.src_size = (f.width, f.height)
                        if self.active:
                            w, h = self._display_size(f.width, f.height)
                            # ไม่ส่งทุกเฟรมเข้าคิวหน้าจอ (ถ้าหน้าจอวาดไม่ทันคิวจะพอกจนโปรแกรมค้าง)
                            # แค่เก็บภาพล่าสุดไว้ ให้ตัวจับเวลาของหน้าจอมาดึงไปวาดเอง
                            self.latest_image = frame_to_qimage(f, w, h)
                            self.frame_id += 1
                    if packet.size:
                        arrived = time.monotonic()
                        if packet.is_keyframe:
                            self._gop = []
                        if len(self._gop) < 600:
                            self._gop.append((packet, arrived))
                        if self._rec_path is not None or self._out is not None:
                            # หลังถอดรหัสแล้วเท่านั้น (mux จะยึดข้อมูลแพ็กเก็ตไป)
                            self._record_packet(packet, arrived, stream)
            except Exception:  # noqa: BLE001  สัญญาณหลุด / timeout
                pass
            finally:
                self._close_out()  # ปิดไฟล์อัดให้สมบูรณ์ (ถ้ายังอัดอยู่ จะเริ่มไฟล์ใหม่หลังต่อกลับ)
                self._gop = []
                container.close()
            if self._running:
                self.status.emit("สัญญาณหลุด – กำลังต่อใหม่…")
                self._sleep(2)
        self._close_out()

    def _sleep(self, sec):
        end = time.time() + sec
        while self._running and time.time() < end:
            time.sleep(0.1)


class CameraTile(QFrame):
    """ช่องภาพหนึ่งช่อง: การ์ดมุมมน มีจุดสถานะ (เขียว = ภาพมา, ส้ม = กำลังต่อ, แดง = ต่อไม่ได้) และป้าย REC"""
    double_clicked = Signal(object)
    clicked = Signal(object)

    def __init__(self, name: str, crop: str | None = None):
        super().__init__()
        self.setObjectName("tile")
        self.name = name
        self.crop = crop  # "top" / "bottom" สำหรับกล้องสองเลนส์ที่ส่งภาพซ้อนบน-ล่างมาในภาพเดียว
        self.worker: StreamWorker | None = None
        self.recording = False
        self._pix = None
        self._seen = -1
        self._state = None

        self.dot = QLabel("●")
        self.dot.setObjectName("dot")
        self.title = QLabel(name)
        self.title.setObjectName("tileTitle")
        self.info = QLabel("")
        self.info.setProperty("role", "muted")
        self.rec_label = QLabel("REC")
        self.rec_label.setObjectName("rec")
        self.rec_label.hide()
        self.video = QLabel("ยังไม่เชื่อมต่อ")
        self.video.setObjectName("video")
        self.video.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video.setWordWrap(True)
        self.video.setMinimumSize(280, 158)
        self.video.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Ignored)

        head = QHBoxLayout()
        head.setContentsMargins(12, 7, 10, 7)
        head.setSpacing(7)
        self.back_btn = theme.btn("ดูทุกกล้อง", "left", kind="ghost", tip="กลับไปดูแบบตาราง (Esc)")
        self.back_btn.clicked.connect(lambda: self.double_clicked.emit(self))
        self.back_btn.hide()
        head.addWidget(self.back_btn)
        head.addWidget(self.dot)
        head.addWidget(self.title)
        head.addStretch()
        head.addWidget(self.info)
        head.addWidget(self.rec_label)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        lay.addLayout(head)
        lay.addWidget(self.video, 1)
        self._set_state("wait")

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.dot.setProperty("state", state)
            theme.repolish(self.dot)

    def attach(self, worker: StreamWorker):
        """ผูกช่องนี้กับสตรีม (หลายช่องใช้สตรีมเดียวกันได้) – MainWindow เป็นคนเปิด/ปิดสตรีม"""
        self.detach()
        self.worker = worker
        self._seen = -1
        worker.status.connect(self.on_status)

    def detach(self):
        self.set_recording(False)
        if self.worker:
            try:
                self.worker.status.disconnect(self.on_status)
            except (RuntimeError, TypeError):
                pass
            self.worker = None

    def _crop_rect(self, w, h):
        if self.crop == "top":
            return 0, 0, w, h // 2
        if self.crop == "bottom":
            return 0, h // 2, w, h - h // 2
        return 0, 0, w, h

    def poll(self):
        """เรียกจากตัวจับเวลา: วาดเฉพาะเมื่อมีภาพใหม่ และช่องนี้แสดงอยู่"""
        w = self.worker
        if w is None or w.frame_id == self._seen or not self.isVisible():
            return
        self._seen = w.frame_id
        if w.latest_image is not None:
            self.on_frame(w.latest_image)

    def wanted_size(self):
        """ขนาดภาพ (พิกเซลจริง) ที่ช่องนี้ต้องการ – ส่งให้เธรดสตรีมย่อภาพมาให้พอดี"""
        r = self.video.devicePixelRatioF()
        w, h = int(self.video.width() * r), int(self.video.height() * r)
        return (w, h * 2) if self.crop else (w, h)  # ภาพซ้อนบน-ล่าง ต้องสูงเป็นสองเท่าก่อนตัดครึ่ง

    def on_frame(self, img: QImage):
        if self.crop:
            img = img.copy(*self._crop_rect(img.width(), img.height()))
        if self._state != "live":
            self._set_state("live")
        if self.worker:
            sw, sh = self.worker.src_size
            src = f"{sw}×{sh // 2 if self.crop else sh}"
            if self.info.text() != src:
                self.info.setText(src)
        self._pix = QPixmap.fromImage(img)
        self._render()

    @Slot(str)
    def on_status(self, text: str):
        if not text:
            return
        if text.startswith("กำลังเชื่อมต่อ") and self._pix is not None:
            self._set_state("wait")  # กำลังสลับ SD/HD – ค้างภาพเดิมไว้ ไม่ให้จอดำ
            return
        self._pix = None
        self.info.setText("")
        self.video.setPixmap(QPixmap())
        self.video.setText(text)
        self._set_state("err" if text.startswith("เชื่อมต่อไม่ได้") else "wait")

    def _render(self):
        if self._pix is None:
            return
        size = self.video.size() * self.video.devicePixelRatioF()
        pm = self._pix
        if abs(pm.width() - size.width()) > 2 and abs(pm.height() - size.height()) > 2:
            pm = pm.scaled(size.toSize() if hasattr(size, "toSize") else size,
                           Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        pm.setDevicePixelRatio(self.video.devicePixelRatioF())
        self.video.setPixmap(pm)

    def resizeEvent(self, e):
        self._render()
        super().resizeEvent(e)

    def mouseDoubleClickEvent(self, e):
        self.double_clicked.emit(self)

    def mousePressEvent(self, e):
        self.clicked.emit(self)

    def set_active(self, on: bool):
        if self.property("active") != on:
            self.setProperty("active", on)
            theme.repolish(self)

    def snapshot(self, folder: Path) -> Path | None:
        img = self.worker.snapshot_image() if self.worker else None
        if img is None:
            return None
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{safe(self.name)}_{stamp()}.jpg"  # Qt บันทึกชื่อไฟล์ภาษาไทยได้
        img = img.copy(*self._crop_rect(img.width(), img.height()))
        return path if img.save(str(path), "JPG", 92) else None

    def set_recording(self, on: bool, folder: Path | None = None) -> bool:
        if on and self.worker and folder:
            folder.mkdir(parents=True, exist_ok=True)
            # VideoWriter ไม่รองรับ path ภาษาไทยทุกเครื่อง จึงใช้ชื่อไฟล์ ASCII
            path = folder / f"cam{id(self) % 1000}_{stamp()}.mp4"
            self.recording = self.worker.start_record(path)
        else:
            if self.worker:
                self.worker.stop_record()
            self.recording = False
        self.rec_label.setVisible(self.recording)
        return self.recording


class PTZPanel(QFrame):
    """แผงหมุนกล้อง: กดค้างที่ปุ่มลูกศร (หรือปุ่มลูกศรบนคีย์บอร์ด) เพื่อหมุน ปล่อยเพื่อหยุด"""

    class _Bridge(QObject):
        status = Signal(str)
        presets = Signal(list)

    DIRS = {  # ไอคอน: (แถว, คอลัมน์, pan, tilt)
        "upleft": (0, 0, -1, 1), "up": (0, 1, 0, 1), "upright": (0, 2, 1, 1),
        "left": (1, 0, -1, 0), "right": (1, 2, 1, 0),
        "downleft": (2, 0, -1, -1), "down": (2, 1, 0, -1), "downright": (2, 2, 1, -1),
    }

    def __init__(self, cams: list[dict]):
        super().__init__()
        self.setProperty("card", True)
        # กล้องแต่ละตัวมีตัวควบคุม PTZ ของตัวเอง (ข้ามตัวที่ตั้ง "ptz": false)
        # กล้องหนึ่งตัว (หนึ่ง IP) อาจมีหลายเลนส์ แต่มีตัวควบคุม PTZ ตัวเดียว
        self.cams: list[tuple[str, OnvifPTZ]] = []
        self.cam_ips: list[str] = []
        for c in cams:
            if not c.get("ptz") or c["ip"] in self.cam_ips:
                continue
            self.cam_ips.append(c["ip"])
            self.cams.append((c["device"], OnvifPTZ(c["ip"], c["username"], c["password"], c["onvif_port"])))
        self.ready_set: set[int] = set()
        self.idx = 0
        self.moving = None
        self.pool = ThreadPoolExecutor(max_workers=1)  # คำสั่งเรียงตามลำดับเสมอ
        self.bridge = self._Bridge()
        self.bridge.status.connect(self._set_status)
        self.bridge.presets.connect(self._set_presets)

        self.setFixedWidth(232)
        self.cam_box = QComboBox()
        self.cam_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.cam_box.addItems([n for n, _ in self.cams])
        self.cam_box.currentIndexChanged.connect(self.select_cam)

        pad = QGridLayout()
        pad.setSpacing(8)
        for name, (r, c, pan, tilt) in self.DIRS.items():
            b = QPushButton()
            b.setIcon(theme.icon(name))
            b.setIconSize(theme.ICON_SIZE)
            b.setFixedSize(46, 46)
            b.setProperty("pad", True)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.pressed.connect(lambda p=pan, t=tilt: self.start_move(p, t))
            b.released.connect(self.stop_move)
            pad.addWidget(b, r, c)
        stop_btn = QPushButton()
        stop_btn.setIcon(theme.icon("stop", theme.MUTED))
        stop_btn.setIconSize(QSize(14, 14))
        stop_btn.setFixedSize(46, 46)
        stop_btn.setProperty("pad", True)
        stop_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        stop_btn.clicked.connect(self.stop_move)
        pad.addWidget(stop_btn, 1, 1)
        pad_wrap = QHBoxLayout()
        pad_wrap.addStretch()
        pad_wrap.addLayout(pad)
        pad_wrap.addStretch()

        self.speed = QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 10)
        self.speed.setValue(5)
        self.speed.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.speed_val = theme.label("5", "muted")
        self.speed.valueChanged.connect(lambda v: self.speed_val.setText(str(v)))
        sp_head = QHBoxLayout()
        sp_head.addWidget(theme.label("ความเร็ว"))
        sp_head.addStretch()
        sp_head.addWidget(self.speed_val)

        self.preset_box = QComboBox()
        self.preset_box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.preset_box.setPlaceholderText("ไม่มีตำแหน่งที่บันทึก")
        go = theme.btn("ไปตำแหน่งนี้")
        go.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        go.clicked.connect(self.goto_preset)
        retry = theme.btn("เชื่อมต่อใหม่", "refresh", kind="ghost")
        retry.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        retry.clicked.connect(self.connect_cam)

        self.status = theme.label("", "muted")
        self.status.setWordWrap(True)

        v = QVBoxLayout(self)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(10)
        v.addWidget(theme.label("หมุนกล้อง", "title"))
        v.addWidget(self.cam_box)
        v.addSpacing(4)
        v.addLayout(pad_wrap)
        hint = theme.label("กดค้างเพื่อหมุน · ใช้ปุ่มลูกศรบนคีย์บอร์ดได้", "muted")
        hint.setWordWrap(True)
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(hint)
        v.addSpacing(6)
        v.addLayout(sp_head)
        v.addWidget(self.speed)
        v.addSpacing(6)
        v.addWidget(theme.label("ตำแหน่งที่บันทึกไว้", "section"))
        v.addWidget(self.preset_box)
        v.addWidget(go)
        v.addStretch()
        v.addWidget(self.status)
        v.addWidget(retry)

        self.connect_cam()

    # ----- งานเบื้องหลัง -----
    def _run(self, fn, *args, ok_msg=None):
        def job():
            try:
                fn(*args)
                if ok_msg is not None:
                    self.bridge.status.emit(ok_msg)
            except OnvifError as e:
                self.bridge.status.emit(f"⚠ {e}")
            except Exception as e:  # noqa: BLE001
                self.bridge.status.emit(f"⚠ ผิดพลาด: {e}")
        self.pool.submit(job)

    @property
    def cam(self) -> OnvifPTZ:
        return self.cams[self.idx][1]

    @property
    def ready(self) -> bool:
        return bool(self.cams) and self.idx in self.ready_set

    def select_cam(self, i: int):
        if i < 0 or i == self.idx and self.ready:
            return
        if self.moving:
            self.stop_move()
        self.idx = i
        if self.cam_box.currentIndex() != i:
            self.cam_box.setCurrentIndex(i)
        if self.ready:
            self._set_presets(self.cam.presets)
            self.status.setText(f"{self.cams[i][0]}: พร้อมใช้งาน")
        else:
            self.connect_cam()

    def select_by_ip(self, ip: str):
        if ip in self.cam_ips:
            self.select_cam(self.cam_ips.index(ip))

    def current_ip(self):
        return self.cam_ips[self.idx] if self.cams else None

    def connect_cam(self):
        if not self.cams:
            self._set_status("ไม่มีกล้องที่เปิด PTZ")
            return
        i, (name, cam) = self.idx, self.cams[self.idx]
        self.status.setText(f"{name}: กำลังเชื่อมต่อ…")

        def job():
            try:
                cam.connect()
                self.ready_set.add(i)
                if i == self.idx:
                    self.bridge.presets.emit(cam.presets)
                self.bridge.status.emit(f"{name}: พร้อมใช้งาน")
            except OnvifError as e:
                self.ready_set.discard(i)
                self.bridge.status.emit(f"⚠ {name}: {e}")
        self.pool.submit(job)

    def start_move(self, pan, tilt):
        if not self.ready or self.moving == (pan, tilt):
            return
        self.moving = (pan, tilt)
        s = self.speed.value() / 10
        self._run(self.cam.move, pan * s, tilt * s, ok_msg="")

    def stop_move(self):
        if not self.ready:
            return
        self.moving = None
        self._run(self.cam.stop)

    def goto_preset(self):
        token = self.preset_box.currentData()
        if self.ready and token:
            self._run(self.cam.goto_preset, token, ok_msg=f"กำลังไป {self.preset_box.currentText()}")

    @Slot(str)
    def _set_status(self, text):
        if text or not self.status.text().startswith("⚠"):
            self.status.setText(text)

    @Slot(list)
    def _set_presets(self, presets):
        self.preset_box.clear()
        for token, name in presets:
            self.preset_box.addItem(name, token)

    def shutdown(self):
        if self.ready and self.moving:
            self.cam.stop()
        self.pool.shutdown(wait=False, cancel_futures=True)


ARROWS = {
    Qt.Key.Key_Up: (0, 1), Qt.Key.Key_Down: (0, -1),
    Qt.Key.Key_Left: (-1, 0), Qt.Key.Key_Right: (1, 0),
}


def stamp():
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe(s: str):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")  # ตัดเฉพาะตัวอักษรที่ Windows ห้าม


class MainWindow(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self.cfg = cfg
        self.setWindowTitle("LAN Camera Viewer")
        self.resize(1360, 800)
        self.focused: CameraTile | None = None

        # หนึ่งช่องต่อหนึ่งเลนส์ (กล้อง 2 เลนส์ที่ส่งภาพซ้อนกันจะใช้สตรีมเดียวแล้วตัดครึ่ง)
        self.workers: dict[str, StreamWorker] = {}
        self._retired: list[StreamWorker] = []  # สตรีมที่สั่งปิดแล้ว รอเธรดจบ
        self.tile_cams: list[tuple["CameraTile", dict]] = []
        self.cams = expand_cameras(cfg)
        for c in self.cams:
            t = CameraTile(c["name"], c.get("crop"))
            t.ip = c["ip"]
            self.tile_cams.append((t, c))
        self.tiles = [t for t, _ in self.tile_cams]
        for t in self.tiles:
            t.double_clicked.connect(self.toggle_focus)

        # ---------- แถบด้านบน ----------
        logo = QLabel()
        logo.setPixmap(theme.icon("video", theme.ACCENT).pixmap(22, 22))
        app_title = QLabel("LAN Camera Viewer")
        app_title.setObjectName("appTitle")
        n_dev = len({c["device"] for c in self.cams})
        self.count_pill = QLabel(f"{n_dev} กล้อง · {len(self.tiles)} ช่อง")
        self.count_pill.setObjectName("statusPill")

        # คุณภาพ: อัตโนมัติ = ช่องเล็กใช้ SD (ประหยัดเครื่อง) ช่องที่ขยายเต็มใช้ HD
        self.q_btns = {}
        group = QButtonGroup(self)
        seg = QHBoxLayout()
        seg.setSpacing(0)
        for key, text, tip, side in (
            ("auto", "อัตโนมัติ", "ช่องเล็กใช้ SD · ดับเบิลคลิกขยายแล้วเป็น HD (แนะนำ เครื่องไม่หนัก)", "left"),
            ("sd", "SD", "ภาพเล็กทุกช่อง เบาที่สุด", "mid"),
            ("hd", "HD", "ภาพชัดทุกช่อง (กินเครื่องมาก)", "right"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setCheckable(True)
            b.setProperty("seg", side)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            group.addButton(b)
            seg.addWidget(b)
            self.q_btns[key] = b
        self.q_btns.get(cfg.get("quality", "auto"), self.q_btns["auto"]).setChecked(True)
        group.buttonClicked.connect(lambda _b: self.sync_streams())

        btn_snap = theme.btn("ถ่ายภาพ", "camera", kind="ghost", tip="บันทึกภาพทุกช่อง (S)")
        btn_snap.clicked.connect(self.snapshot_all)
        self.btn_rec = theme.btn("อัดวิดีโอ", "record", kind="ghost", tip="อัดวิดีโอทุกช่อง (R)", color=theme.DANGER)
        self.btn_rec.setObjectName("recBtn")
        self.btn_rec.setCheckable(True)
        self.btn_rec.toggled.connect(self.toggle_record)
        btn_reconnect = theme.btn("", "refresh", kind="ghost", tip="เชื่อมต่อใหม่ทุกกล้อง")
        btn_reconnect.clicked.connect(self.connect_all)
        btn_full = theme.btn("", "maximize", kind="ghost", tip="เต็มจอ (F11)")
        btn_full.clicked.connect(self.toggle_fullscreen)
        btn_settings = theme.btn("ตั้งค่า", "settings", kind="primary", tip="เพิ่ม / แก้ไขกล้อง", color="#ffffff")
        btn_settings.clicked.connect(self.open_settings)
        btn_about = theme.btn("", "info", kind="ghost", tip="เกี่ยวกับโปรแกรม / ผู้จัดทำ")
        btn_about.clicked.connect(self.open_about)
        self.info = theme.label("คลิกภาพ = เลือกกล้องที่จะหมุน · ดับเบิลคลิก = ขยาย" if self.tiles else "", "muted")
        self.info.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)  # จอแคบให้หดได้ ไม่ทับปุ่ม

        bar_frame = QFrame()
        bar_frame.setProperty("card", True)
        bar = QHBoxLayout(bar_frame)
        bar.setContentsMargins(14, 8, 10, 8)
        bar.setSpacing(6)
        bar.addWidget(logo)
        bar.addWidget(app_title)
        bar.addSpacing(6)
        bar.addWidget(self.count_pill)
        self.btn_back = theme.btn("กลับไปดูทุกกล้อง", "left", kind="primary", tip="กลับไปดูแบบตาราง (Esc)")
        self.btn_back.clicked.connect(lambda: self.focused and self.toggle_focus(self.focused))
        self.btn_back.hide()
        bar.addSpacing(6)
        bar.addWidget(self.btn_back)
        bar.addSpacing(14)
        bar.addLayout(seg)
        bar.addSpacing(10)
        for w in (btn_snap, self.btn_rec, btn_reconnect, btn_full):
            bar.addWidget(w)
        bar.addStretch()
        bar.addWidget(self.info)
        bar.addSpacing(10)
        bar.addWidget(btn_about)
        bar.addWidget(btn_settings)
        for w in bar_frame.findChildren(QPushButton):
            w.setFocusPolicy(Qt.FocusPolicy.NoFocus)  # ให้ปุ่มลูกศรไปหมุนกล้อง ไม่ใช่เลื่อนโฟกัสปุ่ม

        self.grid = QGridLayout()
        self.grid.setSpacing(10)
        self.layout_grid()
        if not self.tiles:
            empty = QFrame()
            empty.setProperty("card", True)
            ev = QVBoxLayout(empty)
            ev.addStretch()
            ic = QLabel()
            ic.setPixmap(theme.icon("video", theme.MUTED).pixmap(56, 56))
            ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ev.addWidget(ic)
            t1 = theme.label("ยังไม่มีกล้อง", "title")
            t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ev.addWidget(t1)
            t2 = theme.label("กดปุ่ม \"ตั้งค่า\" มุมขวาบน เพื่อเพิ่มหรือค้นหากล้องในวง LAN", "muted")
            t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
            ev.addWidget(t2)
            add = theme.btn("เพิ่มกล้อง", "plus", kind="primary", color="#ffffff")
            add.clicked.connect(self.open_settings)
            row = QHBoxLayout()
            row.addStretch()
            row.addWidget(add)
            row.addStretch()
            ev.addSpacing(8)
            ev.addLayout(row)
            ev.addStretch()
            self.grid.addWidget(empty, 0, 0)

        root = QWidget()
        v = QVBoxLayout(root)
        v.setContentsMargins(12, 12, 12, 12)
        v.setSpacing(10)
        v.addWidget(bar_frame)
        body = QHBoxLayout()
        body.setSpacing(10)
        body.addLayout(self.grid, 1)
        has_ptz = any(c.get("ptz") for c in self.cams)
        self.ptz = PTZPanel(self.cams) if has_ptz and cfg.get("ptz", {}).get("enabled", True) else None
        # ไฟ / โหมดกลางคืน: เฉพาะกล้อง Tapo (Yoosee สั่งไฟได้แค่ผ่านแอป/คลาวด์)
        tapo_devs = list(dict.fromkeys((c["device"], c["ip"]) for c in self.cams
                                       if c.get("model", "").startswith("tapo")))
        self.light = LightPanel(tapo_devs, cfg.get("tapo_cloud_password", ""), lambda: self.open_settings(1)) \
            if tapo_devs and cfg.get("lights", {}).get("enabled", True) else None
        side = QVBoxLayout()
        side.setSpacing(10)
        if self.ptz:
            side.addWidget(self.ptz, 1)
            for t in self.tiles:
                t.clicked.connect(lambda tile: self.ptz.select_by_ip(tile.ip))
            self.ptz.cam_box.currentIndexChanged.connect(lambda _i: self._mark_active())
            self._mark_active()
        if self.light:
            side.addWidget(self.light)
            for t in self.tiles:
                t.clicked.connect(lambda tile: self.light.select_by_ip(tile.ip))
            if not self.ptz:
                side.addStretch()
        if self.ptz or self.light:
            body.addLayout(side)
        v.addLayout(body, 1)
        self.setCentralWidget(root)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._held = set()

        QShortcut(QKeySequence("S"), self, self.snapshot_all)
        QShortcut(QKeySequence("R"), self, self.btn_rec.toggle)
        QShortcut(QKeySequence("F11"), self, self.toggle_fullscreen)
        QShortcut(QKeySequence("Esc"), self, self.exit_fullscreen)

        self.connect_all()

        # ดึงภาพล่าสุดของทุกช่องมาวาด ~15 ครั้ง/วินาที ถ้าเครื่องช้าก็แค่ข้ามเฟรม ไม่ค้าง
        self.repaint_timer = QTimer(self)
        self.repaint_timer.timeout.connect(self._poll_tiles)
        self.repaint_timer.start(66)

        # ตรวจเวอร์ชันใหม่จาก GitHub (เฉพาะตัวที่ติดตั้ง) – ทำในเธรดพื้นหลัง ไม่ทำให้ภาพกระตุก
        from app_info import FROZEN  # noqa: PLC0415
        if FROZEN and cfg.get("auto_update", True) and not getattr(QApplication.instance(), "_update_checked", False):
            QApplication.instance()._update_checked = True
            QTimer.singleShot(5000, self._check_update)

    def _check_update(self):
        from updater import UpdateChecker  # noqa: PLC0415
        self._checker = UpdateChecker(self)
        self._checker.found.connect(self._on_update)
        self._checker.start()

    def _on_update(self, info):
        from updater import offer_update  # noqa: PLC0415
        if offer_update(self, info, self.cfg, save=save_config):
            self.close()

    def open_settings(self, tab=0):
        """เปิดหน้าตั้งค่า – ปิดสตรีมก่อน (กล้องบางรุ่นรับคนดูพร้อมกันได้น้อย จะได้ทดสอบได้)"""
        from settings_dialog import SettingsDialog  # noqa: PLC0415
        self.btn_rec.setChecked(False)
        self.stop_workers()
        for t in self.tiles:
            t._pix = None
            t.on_status("หยุดชั่วคราวระหว่างตั้งค่า")
        dlg = SettingsDialog(self.cfg, self, tab=tab if isinstance(tab, int) and not isinstance(tab, bool) else 0)
        if dlg.exec():
            save_config(dlg.cfg)
            new = MainWindow(dlg.cfg)
            new.setGeometry(self.geometry())
            QApplication.instance().main_window = new  # เก็บอ้างอิงไว้ ไม่ให้หน้าต่างใหม่ถูกลบ
            new.showFullScreen() if self.isFullScreen() else new.show()
            self.close()
        else:
            self.connect_all()

    def open_about(self):
        from about_dialog import AboutDialog  # noqa: PLC0415
        AboutDialog(self).exec()

    def _poll_tiles(self):
        # บอกแต่ละสตรีมว่าต้องการภาพขนาดเท่าไร และมีช่องแสดงอยู่ไหม (ไม่มี = ถอดรหัสแค่ภาพหลัก)
        need: dict[StreamWorker, tuple | None] = {}
        for t in self.tiles:
            w = t.worker
            if w is None:
                continue
            if t.isVisible():
                tw, th = t.wanted_size()
                cur = need.get(w) or (0, 0)
                need[w] = (max(cur[0], tw), max(cur[1], th))
            else:
                need.setdefault(w, None)
        for w, size in need.items():
            w.active = size is not None
            if size:
                w.target = size
        for t in self.tiles:
            t.poll()
        if self._retired:  # เก็บกวาดสตรีมเก่าที่ปิดเสร็จแล้ว
            self._retired = [w for w in self._retired if w.isRunning()]

    def layout_grid(self):
        for t in self.tiles:
            self.grid.removeWidget(t)
            t.hide()
        shown = [self.focused] if self.focused else self.tiles
        self.btn_back.setVisible(self.focused is not None)
        for t in self.tiles:
            t.back_btn.setVisible(t is self.focused)
            t.setToolTip("" if self.focused else "ดับเบิลคลิกเพื่อขยายดูกล้องนี้")
        cols = max(1, math.ceil(math.sqrt(len(shown))))  # 1→1, 2-4→2, 5-9→3 คอลัมน์
        for i, t in enumerate(shown):
            self.grid.addWidget(t, i // cols, i % cols)
            t.show()

    def quality(self) -> str:
        return next((k for k, b in self.q_btns.items() if b.isChecked()), "auto")

    def _tile_quality(self, tile) -> str:
        q = self.quality()
        if q != "auto":
            return q
        return "hd" if self.focused is tile else "sd"

    def sync_streams(self):
        """ให้แต่ละช่องใช้สตรีมที่ถูกต้อง (SD/HD) – เปิดเฉพาะที่ต้องเปลี่ยน ไม่รอสตรีมเก่าปิด หน้าจอจึงไม่ค้าง"""
        if self.btn_rec.isChecked():
            return  # กำลังอัด – ไม่สลับสตรีม ไม่งั้นไฟล์จะขาด
        wanted = {}
        for t, cam in self.tile_cams:
            url = build_url(self.cfg, cam, self._tile_quality(t))
            wanted[t] = (url, cam.get("transport", "tcp"))
        for t, (url, tr) in wanted.items():
            if t.worker is not None and t.worker.url == url:
                continue
            w = self.workers.get(url)
            if w is None:
                w = self.workers[url] = StreamWorker(url, tr)
                w.start()
            t.attach(w)
        used = {url for url, _ in wanted.values()}
        for url in [u for u in self.workers if u not in used]:
            self._retire(self.workers.pop(url))

    def _retire(self, w):
        w.request_stop()
        w.stop_record()
        self._retired.append(w)  # เก็บอ้างอิงไว้จนกว่าเธรดจะจบเอง

    def connect_all(self):
        """เชื่อมต่อใหม่ทุกกล้อง"""
        self.btn_rec.setChecked(False)
        for t in self.tiles:
            t.detach()
        for w in self.workers.values():
            self._retire(w)
        self.workers.clear()
        self.sync_streams()

    def stop_workers(self, wait=True):
        for t in self.tiles:
            t.detach()
        for w in self.workers.values():
            self._retire(w)
        self.workers.clear()
        if wait:  # รอให้กล้องปล่อยการเชื่อมต่อ (ใช้ตอนเปิดหน้าตั้งค่า กล้องบางรุ่นรับคนดูได้น้อย)
            wait_threads(self._retired, timeout=5)

    def toggle_focus(self, tile):
        self.focused = None if self.focused is tile else tile
        self.layout_grid()
        if self.quality() == "auto":
            self.sync_streams()

    def snapshot_all(self):
        folder = media_dir(self.cfg, "snapshot_dir")
        tiles = [self.focused] if self.focused else self.tiles
        saved = [p for p in (t.snapshot(folder) for t in tiles) if p]
        self.info.setText(f"บันทึกภาพ {len(saved)} ไฟล์ → {folder.name}\\" if saved else "ยังไม่มีภาพให้บันทึก")

    def toggle_record(self, on: bool):
        folder = media_dir(self.cfg, "record_dir")
        if on:
            n = sum(t.set_recording(True, folder) for t in self.tiles)
            if n == 0:
                self.info.setText("อัดไม่ได้ – ยังไม่มีภาพ")
                self.btn_rec.blockSignals(True)
                self.btn_rec.setChecked(False)
                self.btn_rec.blockSignals(False)
                return
            self.btn_rec.setIcon(theme.icon("record", "#ffffff"))
            self.info.setText(f"กำลังอัด {n} กล้อง → {folder.name}\\")
        else:
            for t in self.tiles:
                t.set_recording(False)
            self.btn_rec.setIcon(theme.icon("record", theme.DANGER))
            self.info.setText("หยุดอัดแล้ว")
            self.sync_streams()  # ระหว่างอัดไม่ได้สลับ SD/HD – สลับตอนนี้

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def exit_fullscreen(self):
        """Esc: ถ้ากำลังขยายดูกล้องเดียว → กลับไปตาราง ก่อน · กดอีกครั้งจึงออกจากเต็มจอ"""
        if self.focused is not None:
            self.toggle_focus(self.focused)
        elif self.isFullScreen():
            self.showNormal()

    def _mark_active(self):
        ip = self.ptz.current_ip() if self.ptz else None
        for t in self.tiles:
            t.set_active(t.ip == ip)

    # ----- ปุ่มลูกศรบนคีย์บอร์ด: กดค้างเพื่อหมุน (กดพร้อมกันสองปุ่มได้ เช่น ขึ้น+ขวา) -----
    def _arrow_move(self):
        if not self.ptz:
            return
        if not self._held:
            self.ptz.stop_move()
            return
        pan = sum(ARROWS[k][0] for k in self._held)
        tilt = sum(ARROWS[k][1] for k in self._held)
        if pan == 0 and tilt == 0:
            self.ptz.stop_move()
        else:
            self.ptz.start_move(pan, tilt)

    def keyPressEvent(self, e):
        if e.key() in ARROWS and self.ptz:
            if not e.isAutoRepeat():
                self._held.add(e.key())
                self._arrow_move()
            return
        super().keyPressEvent(e)

    def keyReleaseEvent(self, e):
        if e.key() in ARROWS and self.ptz:
            if not e.isAutoRepeat():
                self._held.discard(e.key())
                self._arrow_move()
            return
        super().keyReleaseEvent(e)

    def closeEvent(self, e):
        if self.ptz:
            self.ptz.shutdown()
        if self.light:
            self.light.shutdown()
        self.hide()  # ซ่อนหน้าต่างทันที ผู้ใช้ไม่ต้องรอ
        self.stop_workers(wait=False)
        wait_threads(self._retired, timeout=3)  # ให้เธรดปิดไฟล์วิดีโอที่อัดอยู่ให้สมบูรณ์ก่อน
        super().closeEvent(e)


def main():
    from app_info import FROZEN, RES_DIR  # noqa: PLC0415
    if FROZEN:  # ให้ Qt หาปลั๊กอินในโฟลเดอร์โปรแกรมเจอเสมอ
        os.environ.setdefault("QT_PLUGIN_PATH", str(RES_DIR / "PySide6" / "plugins"))
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    theme.apply(app)
    ico = ASSETS / "app.ico"
    if ico.exists():
        from PySide6.QtGui import QIcon  # noqa: PLC0415
        app.setWindowIcon(QIcon(str(ico)))
    try:
        cfg = load_config()
    except Exception as ex:  # noqa: BLE001
        QMessageBox.critical(None, "LAN Camera Viewer", f"อ่าน config.json ไม่ได้:\n{ex}")
        return 1
    if cfg is None:  # เปิดครั้งแรก – ไปหน้าตั้งค่าเลย
        from settings_dialog import SettingsDialog  # noqa: PLC0415
        dlg = SettingsDialog({"version": 2, "cameras": [], "quality": "auto"})
        if not dlg.exec():
            return 0
        cfg = dlg.cfg
        save_config(cfg)
    app.main_window = MainWindow(cfg)
    app.main_window.show()
    code = app.exec()
    hard_exit(code)


def hard_exit(code: int = 0):
    """ปิดโปรแกรมทันทีแบบไม่เก็บกวาด – ถ้าใช้ os._exit ขณะเธรดยังค้างอยู่ใน FFmpeg Windows จะแจ้งว่าโปรแกรมแครช"""
    sys.stdout.flush() if sys.stdout else None
    if sys.platform == "win32":
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415
        k32 = ctypes.WinDLL("kernel32")
        k32.GetCurrentProcess.restype = wintypes.HANDLE  # ต้องระบุ ไม่งั้น handle 64 บิตถูกตัดเหลือ 32 บิต
        k32.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
        k32.TerminateProcess(k32.GetCurrentProcess(), code & 0xFFFFFFFF)
    os._exit(code)


if __name__ == "__main__":
    sys.exit(main())

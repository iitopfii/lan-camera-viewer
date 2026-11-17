"""หน้าตั้งค่า: เพิ่ม/แก้/ลบกล้อง เลือกยี่ห้อรุ่น ทดสอบภาพ ค้นหากล้องในวง LAN และตรวจหาสตรีมอัตโนมัติ"""
from __future__ import annotations

import copy
import ipaddress
import threading

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QIntValidator, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QScrollArea, QSizePolicy,
    QTabWidget, QVBoxLayout, QWidget,
)

import theme
from app_info import DEFAULT_RECORD_DIR, DEFAULT_SNAPSHOT_DIR
from camera_models import (
    LAYOUTS, MODELS, lenses_of, probe_stream, rtsp_url, scan_lan, setting, split_rtsp_uri,
)
from onvif_ptz import discover_streams


class _Bridge(QObject):
    """ส่งผลจากเธรดเบื้องหลังกลับมาที่หน้าจอ"""
    done = Signal(str, object)  # (งาน, ผลลัพธ์)


def run_bg(bridge: _Bridge, task: str, fn, *args):
    def job():
        try:
            bridge.done.emit(task, ("ok", fn(*args)))
        except Exception as e:  # noqa: BLE001
            bridge.done.emit(task, ("err", str(e)))
    threading.Thread(target=job, daemon=True).start()


def short_model(model: str) -> str:
    return MODELS.get(model, MODELS["custom"])["label"].split(" (")[0]


def brand(model: str) -> str:
    return short_model(model).split(" –")[0] if model != "custom" else "ไม่ทราบยี่ห้อ"


class PortEdit(QLineEdit):
    """ช่องใส่เลขพอร์ต (ใช้แทน QSpinBox ที่แสดงเลขไทยและปุ่มเล็กเกินไป)"""
    valueChanged = Signal(int)

    def __init__(self, default: int):
        super().__init__()
        self.default = default
        self.setValidator(QIntValidator(1, 65535, self))
        self.setPlaceholderText(str(default))
        self.textChanged.connect(lambda _t: self.valueChanged.emit(self.value()))

    def setValue(self, v):
        self.setText(str(int(v)))

    def value(self) -> int:
        t = self.text().strip()
        return int(t) if t.isdigit() and 0 < int(t) < 65536 else self.default


LABEL_W = 120


def flabel(text):
    """ป้ายหน้าช่องกรอก กว้างเท่ากันทุกฟอร์ม จะได้เรียงตรงกัน"""
    lb = QLabel(text)
    lb.setFixedWidth(LABEL_W)
    return lb


def form():
    f = QFormLayout()
    f.setHorizontalSpacing(16)
    f.setVerticalSpacing(10)
    f.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return f


def card_section(title: str):
    """การ์ดพร้อมหัวข้อ → (การ์ด, layout ด้านใน)"""
    c = QFrame()
    c.setProperty("card", True)
    v = QVBoxLayout(c)
    v.setContentsMargins(18, 14, 18, 16)
    v.setSpacing(10)
    v.addWidget(theme.label(title, "section"))
    return c, v


class CamRow(QWidget):
    """แถวในรายการกล้อง: ติ๊กเปิด/ปิด + ชื่อ + ยี่ห้อ·IP"""
    toggled = Signal(bool)

    def __init__(self, cam: dict):
        super().__init__()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.chk = QCheckBox()
        self.chk.setToolTip("ติ๊กออก = ซ่อนกล้องนี้ไว้ก่อน (ไม่ลบ)")
        self.chk.toggled.connect(self.toggled)
        self.name = QLabel()
        self.name.setStyleSheet("font-weight:600;background:transparent;")
        self.sub = theme.label("", "muted")
        self.sub.setStyleSheet("background:transparent;")
        txt = QVBoxLayout()
        txt.setSpacing(1)
        txt.addWidget(self.name)
        txt.addWidget(self.sub)
        h = QHBoxLayout(self)
        h.setContentsMargins(6, 4, 6, 4)
        h.setSpacing(10)
        h.addWidget(self.chk)
        h.addLayout(txt, 1)
        self.update_from(cam)

    def update_from(self, cam):
        self.chk.blockSignals(True)
        self.chk.setChecked(cam.get("enabled", True))
        self.chk.blockSignals(False)
        self.name.setText(cam.get("name") or "(ไม่มีชื่อ)")
        self.sub.setText(f"{short_model(cam.get('model', 'custom'))} · {cam.get('ip') or 'ยังไม่ใส่ IP'}")
        self.name.setEnabled(cam.get("enabled", True))


class SettingsDialog(QDialog):
    def __init__(self, cfg: dict, parent=None, tab: int = 0):
        super().__init__(parent)
        self.setWindowTitle("ตั้งค่ากล้อง")
        self.setWindowIcon(theme.icon("settings", theme.ACCENT))
        self.resize(1120, 760)
        self.cfg = copy.deepcopy(cfg)
        self.cfg.setdefault("cameras", [])
        self.cams: list[dict] = self.cfg["cameras"]
        self.cur: dict | None = None
        self._loading = False
        self.bridge = _Bridge()
        self.bridge.done.connect(self._on_bg)
        self._busy = set()

        tabs = QTabWidget()
        tabs.addTab(self._build_cameras_tab(), "กล้อง")
        tabs.addTab(self._build_general_tab(), "ทั่วไป")
        tabs.setCurrentIndex(tab)

        cancel = theme.btn("ยกเลิก", kind="ghost")
        cancel.clicked.connect(self.reject)
        save = theme.btn("บันทึกและใช้งาน", kind="primary")
        save.setDefault(True)
        save.clicked.connect(self._save)
        foot = QHBoxLayout()
        foot.addWidget(theme.label("การเปลี่ยนแปลงจะมีผลเมื่อกดบันทึก", "muted"))
        foot.addStretch()
        foot.addWidget(cancel)
        foot.addWidget(save)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 14, 18, 16)
        v.setSpacing(12)
        v.addWidget(tabs, 1)
        v.addLayout(foot)

        self._refresh_list()
        if self.cams:
            self.listw.setCurrentRow(0)
        else:
            self._select(-1)

    # ================================================================ แท็บกล้อง
    def _build_cameras_tab(self):
        w = QWidget()
        h = QHBoxLayout(w)
        h.setContentsMargins(0, 12, 0, 0)
        h.setSpacing(14)

        # ---- ซ้าย: รายการกล้อง
        left = QFrame()
        left.setProperty("card", True)
        left.setFixedWidth(340)
        lv = QVBoxLayout(left)
        lv.setContentsMargins(12, 14, 12, 12)
        lv.setSpacing(8)
        head = QHBoxLayout()
        head.addWidget(theme.label("กล้องทั้งหมด", "section"))
        head.addStretch()
        self.count_lbl = theme.label("", "muted")
        head.addWidget(self.count_lbl)
        lv.addLayout(head)
        self.listw = QListWidget()
        self.listw.currentRowChanged.connect(self._select)
        lv.addWidget(self.listw, 3)

        row = QHBoxLayout()
        row.setSpacing(6)
        b_add = theme.btn("เพิ่มกล้อง", "plus", kind="primary", color="#ffffff")
        b_add.clicked.connect(lambda: self._add_camera())
        self.b_del = theme.btn("", "trash", kind="danger", tip="ลบกล้องที่เลือก")
        self.b_del.clicked.connect(self._delete)
        b_up = theme.btn("", "up", tip="เลื่อนขึ้น (ลำดับช่องบนจอ)")
        b_down = theme.btn("", "down", tip="เลื่อนลง")
        b_up.clicked.connect(lambda: self._move(-1))
        b_down.clicked.connect(lambda: self._move(1))
        row.addWidget(b_add, 1)
        for b in (self.b_del, b_up, b_down):
            row.addWidget(b)
        lv.addLayout(row)
        self.b_scan = theme.btn("ค้นหากล้องในวง LAN", "radar")
        self.b_scan.clicked.connect(self._scan)
        lv.addWidget(self.b_scan)

        self.scan_box = QWidget()
        sv = QVBoxLayout(self.scan_box)
        sv.setContentsMargins(0, 6, 0, 0)
        sv.setSpacing(4)
        sv.addWidget(theme.label("ผลการค้นหา · ดับเบิลคลิกเพื่อเพิ่ม", "section"))
        self.scan_list = QListWidget()
        self.scan_list.itemDoubleClicked.connect(self._add_from_scan)
        sv.addWidget(self.scan_list)
        self.scan_box.hide()
        lv.addWidget(self.scan_box, 2)
        h.addWidget(left)

        # ---- ขวา: รายละเอียดกล้องที่เลือก (เลื่อนดูได้)
        content = QWidget()
        rv = QVBoxLayout(content)
        rv.setContentsMargins(0, 0, 8, 0)
        rv.setSpacing(12)

        c1, v1 = card_section("ข้อมูลกล้อง")
        self.f_name = QLineEdit()
        self.f_name.setPlaceholderText("เช่น หน้าบ้าน")
        self.f_model = QComboBox()
        for key, m in MODELS.items():
            self.f_model.addItem(m["label"], key)
        self.f_help = theme.label("", "hint")
        self.f_help.setWordWrap(True)
        self.f_ip = QLineEdit()
        self.f_ip.setPlaceholderText("เช่น 192.168.1.157")
        self.f_user = QLineEdit()
        self.f_pw = QLineEdit()
        self.f_pw.setEchoMode(QLineEdit.EchoMode.Password)
        eye = self.f_pw.addAction(theme.icon("eye", theme.MUTED), QLineEdit.ActionPosition.TrailingPosition)
        eye.setToolTip("แสดง/ซ่อนรหัส")
        eye.triggered.connect(lambda: self.f_pw.setEchoMode(
            QLineEdit.EchoMode.Normal if self.f_pw.echoMode() == QLineEdit.EchoMode.Password
            else QLineEdit.EchoMode.Password))
        self.f_ptz = QCheckBox("กล้องนี้หมุนได้ (แสดงในแผงหมุนกล้อง)")
        self.f_lenses = theme.label("", "muted")
        self.f_lenses.setWordWrap(True)
        fa = form()
        fa.addRow(flabel("ชื่อกล้อง"), self.f_name)
        fa.addRow(flabel("ยี่ห้อ / รุ่น"), self.f_model)
        v1.addLayout(fa)
        hint_row = QHBoxLayout()
        hint_row.addSpacing(LABEL_W + 16)
        hint_row.addWidget(self.f_help, 1)
        v1.addLayout(hint_row)
        fb = form()
        fb.addRow(flabel("IP กล้อง"), self.f_ip)
        fb.addRow(flabel("Username"), self.f_user)
        fb.addRow(flabel("Password"), self.f_pw)
        fb.addRow(flabel(""), self.f_ptz)
        fb.addRow(flabel("ช่องบนจอ"), self.f_lenses)
        v1.addLayout(fb)
        rv.addWidget(c1)

        # ---- ตั้งค่าขั้นสูง (สำหรับรุ่นอื่นๆ)
        self.adv, v2 = card_section("ขั้นสูง · RTSP")
        af = form()
        self.f_port = PortEdit(554)
        self.f_hd = QLineEdit()
        self.f_hd.setPlaceholderText("path ภาพชัด เช่น stream1")
        self.f_sd = QLineEdit()
        self.f_sd.setPlaceholderText("path ภาพเล็ก (เว้นว่าง = ใช้ภาพชัด)")
        self.f_tr = QComboBox()
        self.f_tr.addItem("อัตโนมัติ (ลอง TCP แล้ว UDP)", "auto")
        self.f_tr.addItem("TCP", "tcp")
        self.f_tr.addItem("UDP", "udp")
        self.f_layout = QComboBox()
        for k, v in LAYOUTS.items():
            self.f_layout.addItem(v, k)
        self.f_onvif = PortEdit(80)
        af.addRow(flabel("พอร์ต RTSP"), self.f_port)
        af.addRow(flabel("Path ภาพชัด (HD)"), self.f_hd)
        af.addRow(flabel("Path ภาพเล็ก (SD)"), self.f_sd)
        af.addRow(flabel("การส่งภาพ"), self.f_tr)
        af.addRow(flabel("รูปแบบภาพ"), self.f_layout)
        af.addRow(flabel("พอร์ต ONVIF"), self.f_onvif)
        v2.addLayout(af)
        rv.addWidget(self.adv)

        # ---- ทดสอบ
        c3, v3 = card_section("ทดสอบการเชื่อมต่อ")
        th = QHBoxLayout()
        th.setSpacing(16)
        tl = QVBoxLayout()
        tl.setSpacing(8)
        self.b_test = theme.btn("ทดสอบภาพ", "play", kind="primary", color="#ffffff")
        self.b_test.clicked.connect(self._test)
        self.b_detect = theme.btn("ตรวจหาอัตโนมัติ", "wand",
                                  tip="ถามที่อยู่สตรีมจากกล้องผ่าน ONVIF – ใช้กับรุ่นที่ไม่มีในรายการ")
        self.b_detect.clicked.connect(self._detect)
        brow = QHBoxLayout()
        brow.setSpacing(8)
        brow.addWidget(self.b_test)
        brow.addWidget(self.b_detect)
        brow.addStretch()
        tl.addLayout(brow)
        self.result = theme.label("กด \"ทดสอบภาพ\" เพื่อดูว่าต่อกล้องได้หรือไม่", "muted")
        self.result.setWordWrap(True)
        tl.addWidget(self.result)
        tl.addStretch()
        th.addLayout(tl, 1)
        self.preview = theme.label("ภาพตัวอย่าง", "preview")
        self.preview.setFixedSize(320, 180)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        th.addWidget(self.preview)
        v3.addLayout(th)
        rv.addWidget(c3)
        rv.addStretch()

        self.form_scroll = QScrollArea()
        self.form_scroll.setWidget(content)
        self.form_scroll.setWidgetResizable(True)
        self.form_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        h.addWidget(self.form_scroll, 1)

        # ---- ตอนยังไม่มีกล้อง
        self.empty = QFrame()
        self.empty.setProperty("card", True)
        ev = QVBoxLayout(self.empty)
        ev.addStretch()
        ic = QLabel()
        ic.setPixmap(theme.icon("video", theme.MUTED).pixmap(56, 56))
        ic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(ic)
        t1 = theme.label("ยังไม่มีกล้อง", "title")
        t1.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(t1)
        t2 = theme.label("กด \"เพิ่มกล้อง\" หรือ \"ค้นหากล้องในวง LAN\" ทางซ้าย", "muted")
        t2.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ev.addWidget(t2)
        ev.addStretch()
        h.addWidget(self.empty, 1)

        # ทุกช่องแก้แล้วบันทึกลงรายการทันที
        for wdg in (self.f_name, self.f_ip, self.f_user, self.f_pw, self.f_hd, self.f_sd):
            wdg.textChanged.connect(self._form_changed)
        for wdg in (self.f_model, self.f_tr, self.f_layout):
            wdg.currentIndexChanged.connect(self._form_changed)
        self.f_ptz.toggled.connect(self._form_changed)
        self.f_port.valueChanged.connect(self._form_changed)
        self.f_onvif.valueChanged.connect(self._form_changed)
        for sb in (self.f_port, self.f_onvif):
            sb.setFixedWidth(120)
            sb.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return w

    def _show_form(self, on: bool):
        self.form_scroll.setVisible(on)
        self.empty.setVisible(not on)
        self.b_del.setEnabled(on)

    # ---- รายการ
    def _refresh_list(self):
        self.listw.blockSignals(True)
        row = self.listw.currentRow()
        self.listw.clear()
        for i, c in enumerate(self.cams):
            it = QListWidgetItem()
            r = CamRow(c)
            r.toggled.connect(lambda on, cam=c, rw=r: self._toggle_enabled(cam, rw, on))
            it.setSizeHint(QSize(0, max(58, r.sizeHint().height() + 8)))
            self.listw.addItem(it)
            self.listw.setItemWidget(it, r)
        self.listw.blockSignals(False)
        on = sum(c.get("enabled", True) for c in self.cams)
        self.count_lbl.setText(f"{on}/{len(self.cams)} เปิดใช้" if self.cams else "")
        if self.cams:
            self.listw.setCurrentRow(min(max(row, 0), len(self.cams) - 1))

    def _toggle_enabled(self, cam, row, on):
        cam["enabled"] = on
        row.update_from(cam)
        n = sum(c.get("enabled", True) for c in self.cams)
        self.count_lbl.setText(f"{n}/{len(self.cams)} เปิดใช้")

    def _current_row_widget(self):
        it = self.listw.currentItem()
        return self.listw.itemWidget(it) if it else None

    def _select(self, i):
        if not (0 <= i < len(self.cams)):
            self.cur = None
            self._show_form(False)
            return
        self._show_form(True)
        self.cur = self.cams[i]
        self._load_form()

    def _add_camera(self, ip="", model="tapo_single"):
        n = len(self.cams) + 1
        names = {c.get("name") for c in self.cams}
        while f"กล้อง {n}" in names:
            n += 1
        cam = {"name": f"กล้อง {n}", "model": model, "ip": ip, "username": MODELS[model]["user"],
               "password": "", "enabled": True, "ptz": MODELS[model]["ptz"]}
        self.cams.append(cam)
        self._refresh_list()
        self.listw.setCurrentRow(len(self.cams) - 1)
        self.f_name.setFocus()
        self.f_name.selectAll()

    def _delete(self):
        i = self.listw.currentRow()
        if not (0 <= i < len(self.cams)):
            return
        if QMessageBox.question(self, "ลบกล้อง", f"ลบ \"{self.cams[i].get('name')}\" ออกจากรายการ?") \
                != QMessageBox.StandardButton.Yes:
            return
        del self.cams[i]
        self._refresh_list()
        if not self.cams:
            self._select(-1)

    def _move(self, d):
        i = self.listw.currentRow()
        j = i + d
        if 0 <= i < len(self.cams) and 0 <= j < len(self.cams):
            self.cams[i], self.cams[j] = self.cams[j], self.cams[i]
            self._refresh_list()
            self.listw.setCurrentRow(j)

    # ---- ฟอร์ม
    def _load_form(self):
        c = self.cur
        self._loading = True
        model = c.get("model", "custom")
        self.f_name.setText(c.get("name", ""))
        self.f_model.setCurrentIndex(max(0, self.f_model.findData(model)))
        self.f_ip.setText(c.get("ip", ""))
        self.f_user.setText(c.get("username", ""))
        self.f_pw.setText(c.get("password", ""))
        self.f_pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.f_ptz.setChecked(bool(c.get("ptz", MODELS.get(model, {}).get("ptz", False))))
        self.f_port.setValue(int(setting(c, "port") or 554))
        self.f_hd.setText(c.get("hd_path", ""))
        self.f_sd.setText(c.get("sd_path", ""))
        self.f_tr.setCurrentIndex(max(0, self.f_tr.findData(setting(c, "transport") or "auto")))
        self.f_layout.setCurrentIndex(max(0, self.f_layout.findData(c.get("layout", "single"))))
        self.f_onvif.setValue(int(setting(c, "onvif_port") or 80))
        self._loading = False
        self._update_view()
        self._set_result("กด \"ทดสอบภาพ\" เพื่อดูว่าต่อกล้องได้หรือไม่", "muted")
        self.preview.setPixmap(QPixmap())
        self.preview.setText("ภาพตัวอย่าง")

    def _form_changed(self, *_):
        if self._loading or self.cur is None:
            return
        c = self.cur
        model = self.f_model.currentData()
        if model != c.get("model"):  # เปลี่ยนรุ่น → ใส่ค่าเริ่มต้นของรุ่นนั้นให้
            m = MODELS[model]
            c["model"] = model
            c["ptz"] = m["ptz"]
            if model != "custom":
                for k in ("port", "transport", "onvif_port", "hd_path", "sd_path", "layout"):
                    c.pop(k, None)
            self._loading = True
            if m["user"] and not self.f_user.text():
                self.f_user.setText(m["user"])
            self.f_ptz.setChecked(m["ptz"])
            self.f_port.setValue(int(setting(c, "port") or 554))
            self.f_tr.setCurrentIndex(max(0, self.f_tr.findData(setting(c, "transport") or "auto")))
            self.f_onvif.setValue(int(setting(c, "onvif_port") or 80))
            self._loading = False
        c["name"] = self.f_name.text().strip()
        c["ip"] = self.f_ip.text().strip()
        c["username"] = self.f_user.text()
        c["password"] = self.f_pw.text()
        c["ptz"] = self.f_ptz.isChecked()
        if model == "custom":
            c["port"] = self.f_port.value()
            c["hd_path"] = self.f_hd.text().strip().lstrip("/")
            c["sd_path"] = self.f_sd.text().strip().lstrip("/")
            c["transport"] = self.f_tr.currentData()
            c["layout"] = self.f_layout.currentData()
            c["onvif_port"] = self.f_onvif.value()
        self._update_view()
        r = self._current_row_widget()
        if r:
            r.update_from(c)

    def _update_view(self):
        c = self.cur
        model = c.get("model", "custom")
        m = MODELS.get(model, MODELS["custom"])
        self.f_help.setText(m["help"])
        self.f_user.setPlaceholderText(m["user"] or "Username")
        self.adv.setVisible(model == "custom")
        names = [f"{c.get('name', '')} {lens['label']}".strip() for lens in lenses_of(c) if lens.get("hd")]
        self.f_lenses.setText("   ·   ".join(names) if names else "ยังไม่ได้ใส่ path ของสตรีม")

    def _set_result(self, text, role):
        self.result.setText(text)
        self.result.setProperty("role", role)
        theme.repolish(self.result)

    # ---- ทดสอบ / ตรวจหา
    def _need_ip(self):
        try:
            ipaddress.ip_address(self.cur.get("ip", ""))
        except ValueError:
            self._set_result("ใส่ IP ของกล้องก่อน (ดูได้จากแอปของกล้อง หรือกด \"ค้นหากล้องในวง LAN\")", "err")
            return False
        return True

    def _test(self):
        if not self.cur or not self._need_ip() or "test" in self._busy:
            return
        c = self.cur
        lenses = [x for x in lenses_of(c) if x.get("hd")]
        if not lenses:
            self._set_result("ยังไม่ได้ใส่ path ของสตรีม – กด \"ตรวจหาอัตโนมัติ\" หรือใส่เอง", "err")
            return
        url = rtsp_url(c["ip"], int(setting(c, "port") or 554), c.get("username", ""), c.get("password", ""),
                       lenses[0].get("sd") or lenses[0]["hd"])
        tr = setting(c, "transport") or "auto"
        transports = ("tcp", "udp") if tr == "auto" else (tr,)
        self._busy.add("test")
        self.b_test.setEnabled(False)
        self._set_result("กำลังเชื่อมต่อ… (อาจใช้เวลาถึง 20 วินาที)", "muted")
        self.preview.setPixmap(QPixmap())
        self.preview.setText("กำลังโหลด…")
        self._test_cam = c
        run_bg(self.bridge, "test", probe_stream, url, transports, 20.0 if "udp" in transports else 10.0)

    def _detect(self):
        if not self.cur or not self._need_ip() or "detect" in self._busy:
            return
        c = self.cur
        self._busy.add("detect")
        self.b_detect.setEnabled(False)
        self._set_result("กำลังถามกล้องผ่าน ONVIF…", "muted")
        self._detect_cam = c
        run_bg(self.bridge, "detect", discover_streams, c["ip"], c.get("username", ""), c.get("password", ""))

    def _scan(self):
        if "scan" in self._busy:
            return
        self._busy.add("scan")
        self.b_scan.setEnabled(False)
        self.b_scan.setText("กำลังค้นหา… (10-30 วินาที)")
        run_bg(self.bridge, "scan", scan_lan)

    def _on_bg(self, task, res):
        self._busy.discard(task)
        kind, val = res
        if task == "test":
            self.b_test.setEnabled(True)
            if self.cur is not self._test_cam:
                return
            if kind == "err":
                self._set_result(f"ต่อไม่ได้: {val}", "err")
                self.preview.setText("ไม่มีภาพ")
                return
            tr, img = val
            w, h = img.width(), img.height()
            if self.cur.get("model") == "custom" and setting(self.cur, "transport") == "auto":
                self.cur["transport"] = tr
                self._load_form()
            self.preview.setPixmap(QPixmap.fromImage(img).scaled(
                self.preview.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
            self._set_result(f"✓ ใช้ได้ – ภาพ {w}×{h} · ส่งแบบ {tr.upper()}", "ok")
        elif task == "detect":
            self.b_detect.setEnabled(True)
            if self.cur is not self._detect_cam:
                return
            if kind == "err":
                self._set_result(f"ตรวจหาไม่ได้: {val}", "err")
                return
            self._apply_detect(val)
        elif task == "scan":
            self.b_scan.setEnabled(True)
            self.b_scan.setText("ค้นหากล้องในวง LAN")
            self.scan_list.clear()
            self.scan_box.show()
            if kind == "err" or not val:
                self.scan_list.addItem("ไม่พบกล้องที่เปิด RTSP" if kind == "ok" else f"ค้นหาไม่ได้: {val}")
                return
            known = {c.get("ip") for c in self.cams}
            for d in val:
                mark = "   ✓ มีแล้ว" if d["ip"] in known else ""
                it = QListWidgetItem(f"{d['ip']}  ·  {brand(d['model'])}{mark}")
                it.setData(Qt.ItemDataRole.UserRole, d)
                if mark:
                    it.setForeground(Qt.GlobalColor.gray)
                self.scan_list.addItem(it)

    def _apply_detect(self, info):
        c = self.cur
        streams = info["streams"]
        hd, sd = streams[0], streams[1 if len(streams) > 2 else -1]  # SD = ชุดกลาง (ไม่เล็กเกินไป)
        port, hd_path = split_rtsp_uri(hd[3])
        _, sd_path = split_rtsp_uri(sd[3])
        c.update({"model": "custom", "port": port, "hd_path": hd_path, "sd_path": sd_path,
                  "transport": c.get("transport") if c.get("model") == "custom" else "auto",
                  "layout": "split" if hd[2] > hd[1] * 1.5 else "single",  # สูงกว่ากว้างมาก = 2 เลนส์ซ้อนกัน
                  "onvif_port": info["onvif_port"], "ptz": info["ptz"]})
        self._load_form()
        lst = ", ".join(f"{w}×{h}" for _, w, h, _ in streams)
        extra = " · ดูเหมือนกล้อง 2 เลนส์ซ้อนบน-ล่าง จึงแบ่งเป็น 2 ช่องให้" if c["layout"] == "split" else ""
        self._set_result(f"✓ เจอสตรีม {len(streams)} ชุด ({lst}) ผ่าน ONVIF พอร์ต {info['onvif_port']}"
                         f"{' · หมุนได้' if info['ptz'] else ''}{extra}\nกด \"ทดสอบภาพ\" เพื่อดูภาพ", "ok")
        r = self._current_row_widget()
        if r:
            r.update_from(c)

    def _add_from_scan(self, it):
        d = it.data(Qt.ItemDataRole.UserRole)
        if not d:
            return
        for i, c in enumerate(self.cams):
            if c.get("ip") == d["ip"]:
                self.listw.setCurrentRow(i)
                return
        self._add_camera(d["ip"], d["model"])

    # ================================================================ แท็บทั่วไป
    def _build_general_tab(self):
        w = QWidget()
        outer = QVBoxLayout(w)
        outer.setContentsMargins(0, 12, 0, 0)
        c, v = card_section("การแสดงผลและไฟล์")
        c.setMaximumWidth(720)
        f = QFormLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(12)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.g_quality = QComboBox()
        self.g_quality.addItem("อัตโนมัติ – ช่องเล็กใช้ SD, ขยายแล้วเป็น HD (แนะนำ)", "auto")
        self.g_quality.addItem("SD – เบาที่สุด", "sd")
        self.g_quality.addItem("HD ทุกช่อง – ชัด แต่กินเครื่องมาก", "hd")
        self.g_quality.setCurrentIndex(max(0, self.g_quality.findData(self.cfg.get("quality", "auto"))))
        self.g_ptz = QCheckBox("แสดงแผงหมุนกล้อง")
        self.g_ptz.setChecked(self.cfg.get("ptz", {}).get("enabled", True))
        self.g_update = QCheckBox("ตรวจหาเวอร์ชันใหม่อัตโนมัติตอนเปิดโปรแกรม")
        self.g_update.setChecked(self.cfg.get("auto_update", True))
        self.g_snap = QLineEdit(self.cfg.get("snapshot_dir") or DEFAULT_SNAPSHOT_DIR)
        self.g_rec = QLineEdit(self.cfg.get("record_dir") or DEFAULT_RECORD_DIR)

        def folder_row(edit):
            r = QHBoxLayout()
            r.addWidget(edit)
            b = theme.btn("เลือก…", "folder")
            b.clicked.connect(lambda: (lambda d: d and edit.setText(d))(
                QFileDialog.getExistingDirectory(self, "เลือกโฟลเดอร์", edit.text())))
            r.addWidget(b)
            return r

        f.addRow("คุณภาพภาพเริ่มต้น", self.g_quality)
        f.addRow("", self.g_ptz)
        f.addRow("", self.g_update)
        f.addRow("โฟลเดอร์ภาพถ่าย", folder_row(self.g_snap))
        f.addRow("โฟลเดอร์วิดีโอ", folder_row(self.g_rec))
        v.addLayout(f)
        note = theme.label("รหัสผ่านกล้องเก็บไว้ในไฟล์ config.json ในเครื่องนี้เท่านั้น (ตัวอัปเดตไม่ส่งข้อมูลใดๆ ออกไป) – อย่าส่งไฟล์นี้ให้คนอื่น", "hint")
        note.setWordWrap(True)
        v.addWidget(note)
        outer.addWidget(c)

        # ---- บัญชี TP-Link สำหรับสั่งไฟ Tapo
        c2, v2 = card_section("ไฟกล้อง Tapo")
        c2.setMaximumWidth(720)
        f2 = QFormLayout()
        f2.setHorizontalSpacing(16)
        f2.setVerticalSpacing(12)
        f2.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.g_lights = QCheckBox("แสดงแผงไฟ / โหมดกลางคืน")
        self.g_lights.setChecked(self.cfg.get("lights", {}).get("enabled", True))
        self.g_cloud = QLineEdit(self.cfg.get("tapo_cloud_password", ""))
        self.g_cloud.setEchoMode(QLineEdit.EchoMode.Password)
        self.g_cloud.setPlaceholderText("รหัสที่ใช้ล็อกอินแอป Tapo")
        eye = self.g_cloud.addAction(theme.icon("eye", theme.MUTED), QLineEdit.ActionPosition.TrailingPosition)
        eye.triggered.connect(lambda: self.g_cloud.setEchoMode(
            QLineEdit.EchoMode.Normal if self.g_cloud.echoMode() == QLineEdit.EchoMode.Password
            else QLineEdit.EchoMode.Password))
        f2.addRow("", self.g_lights)
        f2.addRow("รหัสบัญชี TP-Link", self.g_cloud)
        v2.addLayout(f2)
        note2 = theme.label("การเปิด/ปิดสปอตไลท์และเปลี่ยนโหมดกลางคืนต้องใช้รหัสผ่านบัญชี TP-Link "
                            "(อีเมลที่ล็อกอินแอป Tapo) – Camera Account ใช้สั่งไฟไม่ได้ · "
                            "ถ้าใส่ผิดหลายครั้ง กล้องจะล็อกชั่วคราวประมาณ 30 นาที · "
                            "กล้อง Yoosee สั่งไฟได้เฉพาะในแอป Yoosee", "hint")
        note2.setWordWrap(True)
        v2.addWidget(note2)
        outer.addWidget(c2)
        outer.addStretch()
        return w

    # ================================================================ บันทึก
    def _save(self):
        problems = []
        names = set()
        for c in self.cams:
            n = c.get("name", "").strip()
            if not n:
                problems.append("มีกล้องที่ยังไม่ได้ตั้งชื่อ")
            elif n in names:
                problems.append(f"ชื่อ \"{n}\" ซ้ำกัน")
            names.add(n)
            if c.get("enabled", True):
                try:
                    ipaddress.ip_address(c.get("ip", ""))
                except ValueError:
                    problems.append(f"\"{n}\": IP ไม่ถูกต้อง")
                if c.get("model") == "custom" and not c.get("hd_path"):
                    problems.append(f"\"{n}\": ยังไม่มี path ของสตรีม")
        if problems:
            QMessageBox.warning(self, "ยังบันทึกไม่ได้", "\n".join(dict.fromkeys(problems)))
            return
        self.cfg["version"] = 2
        self.cfg["quality"] = self.g_quality.currentData()
        self.cfg["snapshot_dir"] = self.g_snap.text().strip() or DEFAULT_SNAPSHOT_DIR
        self.cfg["record_dir"] = self.g_rec.text().strip() or DEFAULT_RECORD_DIR
        self.cfg.setdefault("ptz", {})["enabled"] = self.g_ptz.isChecked()
        self.cfg.setdefault("lights", {})["enabled"] = self.g_lights.isChecked()
        self.cfg["auto_update"] = self.g_update.isChecked()
        if self.g_cloud.text():
            self.cfg["tapo_cloud_password"] = self.g_cloud.text()
        else:
            self.cfg.pop("tapo_cloud_password", None)
        self.accept()

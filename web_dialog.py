"""หน้าต่าง "ดูผ่านเว็บ" (เปิดจากปุ่มบนแถบเมนู) – เปิด/ปิด ตั้งรหัส และดูลิงก์ บันทึกแล้วมีผลทันที"""
from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QVBoxLayout

import theme
from app_info import APP_NAME, FROZEN
from settings_dialog import PortEdit, card_section
from web_server import DEFAULT_PORT, hash_password

SECURITY_TEXT = (
    "<b>เราเป็นแค่ทางผ่าน – เข้าถึงภาพกล้องของคุณไม่ได้</b><br>"
    "• การเชื่อมต่อเข้ารหัส HTTPS ตั้งแต่เบราว์เซอร์ของผู้ชม และไปถอดรหัสที่โปรแกรมนี้ในเครื่องของคุณเท่านั้น<br>"
    "• กุญแจถอดรหัสสร้างและเก็บในเครื่องนี้ ไม่เคยถูกส่งออกไป (ส่งไปแค่คำขอใบรับรองที่มีกุญแจสาธารณะ)<br>"
    "• cam.hypebizsoft.com เห็นแค่ข้อมูลที่เข้ารหัส – ดูภาพ วิดีโอ หรือรหัสผ่านไม่ได้ และไม่เก็บภาพใดๆ<br>"
    "• ผู้ชมยังต้องล็อกอินด้วยชื่อผู้ใช้/รหัสด้านบน · ปิดสวิตช์นี้เมื่อไหร่ การเชื่อมต่อจากนอกบ้านหยุดทันที<br>"
    "<span style='color:#8a93a6'>สิ่งที่เซิร์ฟเวอร์กลางยังเห็น: ชื่อบัญชี, IP ผู้ชม, เวลาและปริมาณข้อมูล (ใช้ส่งต่อเท่านั้น)</span>"
)


def set_autostart(on: bool):
    """เปิดโปรแกรมพร้อม Windows (เฉพาะตัวที่ติดตั้ง) – ใส่/ลบรายการใน HKCU\\...\\Run"""
    if not FROZEN:
        return
    import winreg  # noqa: PLC0415
    key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    name = APP_NAME.replace(" ", "")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            if on:
                winreg.SetValueEx(k, name, 0, winreg.REG_SZ, f'"{sys.executable}" --background')
            else:
                try:
                    winreg.DeleteValue(k, name)
                except FileNotFoundError:
                    pass
    except OSError:
        pass


class WebDialog(QDialog):
    """apply(cfg) → เซิร์ฟเวอร์ที่ทำงานอยู่ (หรือ None) · หน้าต่างหลักเป็นคนบันทึก config และเปิด/ปิดเซิร์ฟเวอร์"""

    def __init__(self, cfg: dict, server, apply, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.server = server
        self.apply = apply
        self.setWindowTitle("ดูผ่านเว็บ")
        self.setWindowIcon(theme.icon("globe", theme.ACCENT))
        self.setMinimumWidth(560)
        web = cfg.get("web") or {}

        # ---- สถานะ + ลิงก์
        status_card, sv = card_section("สถานะ")
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.RichText)
        self.status.setOpenExternalLinks(True)
        self.status.setWordWrap(False)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        sv.addWidget(self.status)
        row = QHBoxLayout()
        self.b_open = theme.btn("เปิดในเบราว์เซอร์", "link", kind="ghost")
        self.b_open.clicked.connect(lambda: self.urls() and QDesktopServices.openUrl(QUrl(self.urls()[0])))
        self.b_copy = theme.btn("คัดลอกลิงก์", "copy", kind="ghost")
        self.b_copy.clicked.connect(self._copy)
        row.addWidget(self.b_open)
        row.addWidget(self.b_copy)
        row.addStretch()
        sv.addLayout(row)

        # ---- ค่าตั้ง
        set_card, fv = card_section("ตั้งค่า")
        f = QFormLayout()
        f.setHorizontalSpacing(16)
        f.setVerticalSpacing(12)
        f.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.enabled = QCheckBox("เปิดให้ดูกล้องผ่านเบราว์เซอร์")
        self.enabled.setChecked(bool(web.get("enabled")))
        self.port = PortEdit(DEFAULT_PORT)
        self.port.setValue(int(web.get("port") or DEFAULT_PORT))
        self.port.setMaximumWidth(120)
        self.user = QLineEdit(web.get("username", ""))
        self.user.setPlaceholderText("เช่น admin")
        self.pw = QLineEdit()
        self.pw.setEchoMode(QLineEdit.EchoMode.Password)
        self.pw.setPlaceholderText("เว้นว่าง = ใช้รหัสเดิม" if (web.get("password") or {}).get("hash")
                                   else "อย่างน้อย 8 ตัวอักษร")
        eye = self.pw.addAction(theme.icon("eye", theme.MUTED), QLineEdit.ActionPosition.TrailingPosition)
        eye.triggered.connect(lambda: self.pw.setEchoMode(
            QLineEdit.EchoMode.Normal if self.pw.echoMode() == QLineEdit.EchoMode.Password
            else QLineEdit.EchoMode.Password))
        self.autostart = QCheckBox("เปิดโปรแกรมพร้อม Windows (ทำงานเงียบๆ ที่ถาด)")
        self.autostart.setChecked(bool(web.get("autostart")))
        if not FROZEN:
            self.autostart.setEnabled(False)
            self.autostart.setToolTip("ใช้ได้กับตัวที่ติดตั้งแล้วเท่านั้น")
        f.addRow("", self.enabled)
        f.addRow("พอร์ต", self.port)
        f.addRow("ชื่อผู้ใช้", self.user)
        f.addRow("รหัสผ่าน", self.pw)
        f.addRow("", self.autostart)
        fv.addLayout(f)
        note = theme.label(
            "ดูในบ้าน: เปิดลิงก์ข้างบนจากมือถือที่ต่อ Wi-Fi เดียวกัน · ครั้งแรกเบราว์เซอร์จะเตือนเรื่องใบรับรอง "
            "ให้กด \"ขั้นสูง → ไปต่อ\" (ใบรับรองที่โปรแกรมสร้างเอง ข้อมูลยังเข้ารหัส HTTPS)\n"
            "ดูนอกบ้าน: แนะนำ Tailscale (ฟรี ไม่ต้องเปิดพอร์ต) · ถ้าจะ forward พอร์ตที่เราเตอร์ ให้ forward "
            "เฉพาะพอร์ตนี้มาที่เครื่องนี้ ห้าม forward พอร์ตของกล้อง (554 / 2020 / 5000) และตั้งรหัสให้ยาก – "
            "ใส่ผิด 5 ครั้งจะล็อก 15 นาที", "hint")
        note.setWordWrap(True)
        fv.addWidget(note)

        # ---- เข้าจากนอกบ้านผ่าน cam.hypebizsoft.com
        relay = cfg.get("relay") or {}
        out_card, ov = card_section("เข้าจากนอกบ้านผ่าน cam.hypebizsoft.com")
        self.relay_on = QCheckBox("เปิดให้เข้าจากนอกบ้าน (ไม่ต้องเปิดพอร์ตเราเตอร์)")
        self.relay_on.setChecked(bool(relay.get("enabled")))
        ov.addWidget(self.relay_on)
        self.relay_status = QLabel()
        self.relay_status.setTextFormat(Qt.TextFormat.RichText)
        self.relay_status.setOpenExternalLinks(True)
        self.relay_status.setWordWrap(True)
        ov.addWidget(self.relay_status)
        lrow = QHBoxLayout()
        self.b_portal = theme.btn("เปิดเว็บ cam.hypebizsoft.com", "globe", kind="ghost",
                                  tip="สมัคร / เข้าสู่ระบบ / สร้างรหัสจับคู่")
        self.b_portal.clicked.connect(self._open_portal)
        self.b_remote = theme.btn("เปิดลิงก์นอกบ้าน", "link", kind="ghost")
        self.b_remote.clicked.connect(lambda: self._remote_url() and QDesktopServices.openUrl(QUrl(self._remote_url())))
        self.b_remote_copy = theme.btn("คัดลอกลิงก์นอกบ้าน", "copy", kind="ghost")
        self.b_remote_copy.clicked.connect(self._copy_remote)
        lrow.addWidget(self.b_portal)
        lrow.addWidget(self.b_remote)
        lrow.addWidget(self.b_remote_copy)
        lrow.addStretch()
        ov.addLayout(lrow)
        prow = QHBoxLayout()
        self.pair_code = QLineEdit()
        self.pair_code.setPlaceholderText("รหัสจับคู่จากเว็บ เช่น ABCD-2345")
        self.pair_code.setMaxLength(12)
        self.b_pair = theme.btn("จับคู่", "link", kind="ghost")
        self.b_pair.clicked.connect(self._pair)
        self.b_unpair = theme.btn("ยกเลิกการจับคู่", kind="ghost")
        self.b_unpair.clicked.connect(self._unpair)
        prow.addWidget(self.pair_code, 1)
        prow.addWidget(self.b_pair)
        prow.addWidget(self.b_unpair)
        ov.addLayout(prow)
        how = theme.label("สมัคร/เข้าสู่ระบบที่ cam.hypebizsoft.com → กด \"สร้างรหัสจับคู่\" → ใส่รหัสที่นี่ → กดจับคู่ "
                          "แล้วกดบันทึก", "hint")
        how.setWordWrap(True)
        ov.addWidget(how)
        sec = QLabel(SECURITY_TEXT)
        sec.setTextFormat(Qt.TextFormat.RichText)
        sec.setWordWrap(True)
        sec.setProperty("role", "hint")
        ov.addWidget(sec)
        self._relay_timer = QTimer(self)
        self._relay_timer.timeout.connect(self._refresh_relay)
        self._relay_timer.start(1000)

        save = theme.btn("บันทึก", kind="primary")
        save.clicked.connect(self._save)
        close = theme.btn("ปิด", kind="ghost")
        close.clicked.connect(self.reject)
        foot = QHBoxLayout()
        foot.addStretch()
        foot.addWidget(close)
        foot.addWidget(save)

        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(12)
        v.addWidget(status_card)
        v.addWidget(set_card)
        v.addWidget(out_card)
        v.addLayout(foot)
        self._refresh()
        self._refresh_relay()

    # ----
    def urls(self) -> list[str]:
        return self.server.urls() if self.server is not None else []

    def _refresh(self):
        urls = self.urls()
        if self.server is not None:
            links = "<br>".join(f'<a href="{u}" style="color:{theme.ACCENT};text-decoration:none;">{u}</a>'
                                for u in urls) or f"พอร์ต {self.server.port}"
            self.status.setText(f'<span style="color:#22c55e;font-weight:600;">● เปิดอยู่</span><br>{links}')
        else:
            self.status.setText(f'<span style="color:{theme.MUTED};font-weight:600;">● ปิดอยู่</span><br>'
                                "ติ๊ก \"เปิดให้ดูกล้องผ่านเบราว์เซอร์\" ตั้งชื่อผู้ใช้/รหัส แล้วกดบันทึก")
        self.b_open.setEnabled(bool(urls))
        self.b_copy.setEnabled(bool(urls))
        self.status.adjustSize()
        self.adjustSize()  # จำนวนบรรทัดของลิงก์เปลี่ยน → ปรับความสูงหน้าต่าง

    def _portal_url(self) -> str:
        from relay_client import DEFAULT_SERVER  # noqa: PLC0415
        return ((self.cfg.get("relay") or {}).get("server") or DEFAULT_SERVER).rstrip("/")

    def _open_portal(self):
        paired = bool((self.cfg.get("relay") or {}).get("token"))
        QDesktopServices.openUrl(QUrl(self._portal_url() + ("/dashboard" if paired else "/")))

    def _remote_url(self) -> str:
        host = (self.cfg.get("relay") or {}).get("host", "")
        return f"https://{host}" if host else ""

    def _copy_remote(self):
        url = self._remote_url()
        if url:
            QGuiApplication.clipboard().setText(url)
            self.b_remote_copy.setText("คัดลอกแล้ว")
            QTimer.singleShot(1500, lambda: self.b_remote_copy.setText("คัดลอกลิงก์นอกบ้าน"))

    def _refresh_relay(self):
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415
        app = QApplication.instance()
        r = self.cfg.get("relay") or {}
        paired = bool(r.get("token"))
        connected = getattr(app, "relay", None) is not None and getattr(app, "relay_status", ("", False))[1]
        self.b_remote.setEnabled(bool(paired and connected))
        self.b_remote_copy.setEnabled(bool(paired and r.get("host")))
        # ยังไม่จับคู่ → ปุ่มไปเว็บเด่นขึ้น (ขั้นแรกที่ต้องทำ)
        kind = "ghost" if paired else "primary"
        if self.b_portal.property("kind") != kind:
            self.b_portal.setProperty("kind", kind)
            self.b_portal.setIcon(theme.icon("globe", "#ffffff" if kind == "primary" else theme.TEXT))
            theme.repolish(self.b_portal)
        self.b_portal.setText("จัดการบัญชีที่ cam.hypebizsoft.com" if paired else "สมัคร / รับรหัสจับคู่ที่ cam.hypebizsoft.com")
        self.b_unpair.setEnabled(paired)
        self.b_pair.setText("จับคู่ใหม่" if paired else "จับคู่")
        if getattr(app, "relay", None) is not None:
            text, ok = getattr(app, "relay_status", ("กำลังเชื่อมต่อ…", False))
            host = r.get("host", "")
            color = "#22c55e" if ok else "#f59e0b"
            link = (f'<br><a href="https://{host}" style="color:{theme.ACCENT};text-decoration:none;">https://{host}</a>'
                    if ok and host else "")
            self.relay_status.setText(f'<span style="color:{color};font-weight:600;">●</span> {text}{link}')
        elif paired:
            self.relay_status.setText(f'<span style="color:{theme.MUTED};">●</span> จับคู่กับบัญชี <b>{r.get("slug", "")}</b> แล้ว '
                                      "– ติ๊กเปิดและกดบันทึกเพื่อเริ่มเชื่อมต่อ (ต้องเปิดหน้าเว็บด้านบนด้วย)")
        else:
            self.relay_status.setText(f'<span style="color:{theme.MUTED};">●</span> ยังไม่ได้จับคู่บัญชี')

    def _pair(self):
        import socket  # noqa: PLC0415
        from relay_client import DEFAULT_SERVER, pair  # noqa: PLC0415
        code = self.pair_code.text().strip()
        if not code:
            QMessageBox.information(self, "จับคู่", "ใส่รหัสจับคู่จากหน้าเว็บ cam.hypebizsoft.com ก่อน")
            return
        r = dict(self.cfg.get("relay") or {})
        try:
            res = pair(r.get("server") or DEFAULT_SERVER, code, socket.gethostname())
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "จับคู่ไม่สำเร็จ", str(e))
            return
        r.update({"token": res["token"], "slug": res["slug"], "host": res["host"], "enabled": True})
        self.cfg["relay"] = r
        self.relay_on.setChecked(True)
        self.pair_code.clear()
        QMessageBox.information(self, "จับคู่สำเร็จ", f"จับคู่กับบัญชี {res['slug']} แล้ว\n"
                                f"กดบันทึกเพื่อเริ่มเชื่อมต่อ แล้วเปิด https://{res['host']} ได้จากทุกที่")
        self._refresh_relay()

    def _unpair(self):
        if QMessageBox.question(self, "ยกเลิกการจับคู่", "ลบการจับคู่ในเครื่องนี้และหยุดการเข้าจากนอกบ้าน?") \
                != QMessageBox.StandardButton.Yes:
            return
        r = dict(self.cfg.get("relay") or {})
        for k in ("token", "slug", "host"):
            r.pop(k, None)
        r["enabled"] = False
        self.cfg["relay"] = r
        self.relay_on.setChecked(False)
        self.server = self.apply(self.cfg)
        self._refresh()
        self._refresh_relay()

    def _copy(self):
        urls = self.urls()
        if urls:
            QGuiApplication.clipboard().setText(urls[0])
            self.b_copy.setText("คัดลอกแล้ว")
            QTimer.singleShot(1500, lambda: self.b_copy.setText("คัดลอกลิงก์"))

    def _save(self):
        web = dict(self.cfg.get("web") or {})
        new_pw = self.pw.text()
        if self.enabled.isChecked():
            problem = None
            if not self.user.text().strip():
                problem = "ใส่ชื่อผู้ใช้ก่อน"
            elif new_pw and len(new_pw) < 8:
                problem = "รหัสผ่านต้องยาวอย่างน้อย 8 ตัวอักษร"
            elif not new_pw and not (web.get("password") or {}).get("hash"):
                problem = "ตั้งรหัสผ่านก่อน"
            if problem:
                QMessageBox.warning(self, "ยังบันทึกไม่ได้", problem)
                return
        if new_pw:
            web["password"] = hash_password(new_pw)
        web.update({"enabled": self.enabled.isChecked(), "port": self.port.value() or DEFAULT_PORT,
                    "username": self.user.text().strip(),
                    "autostart": self.autostart.isChecked() and self.enabled.isChecked()})
        self.cfg["web"] = web
        relay = dict(self.cfg.get("relay") or {})
        if self.relay_on.isChecked() and not relay.get("token"):
            QMessageBox.warning(self, "ยังบันทึกไม่ได้", "เข้าจากนอกบ้าน: จับคู่กับบัญชีที่ cam.hypebizsoft.com ก่อน")
            return
        if self.relay_on.isChecked() and not web["enabled"]:
            QMessageBox.warning(self, "ยังบันทึกไม่ได้", "เข้าจากนอกบ้านต้องเปิด \"ให้ดูกล้องผ่านเบราว์เซอร์\" ด้วย")
            return
        relay["enabled"] = self.relay_on.isChecked()
        self.cfg["relay"] = relay
        set_autostart(web["autostart"])
        self.pw.clear()
        self.pw.setPlaceholderText("เว้นว่าง = ใช้รหัสเดิม" if (web.get("password") or {}).get("hash")
                                   else "อย่างน้อย 8 ตัวอักษร")
        self.server = self.apply(self.cfg)
        self._refresh()

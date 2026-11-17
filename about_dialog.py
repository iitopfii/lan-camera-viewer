"""หน้า "เกี่ยวกับโปรแกรม" – รายละเอียดผู้จัดทำ"""
from PySide6.QtCore import QSize, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QGuiApplication, QPixmap
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout

import theme
from app_info import (
    APP_NAME, APP_VERSION, ASSETS, AUTHOR_ALIAS, AUTHOR_COMPANY, AUTHOR_FACEBOOK, AUTHOR_NAME, AUTHOR_PHONE,
)


def _row(icon_name, text, button=None):
    h = QHBoxLayout()
    h.setSpacing(12)
    ic = QLabel()
    ic.setPixmap(theme.icon(icon_name, theme.MUTED).pixmap(18, 18))
    ic.setFixedWidth(20)
    h.addWidget(ic)
    lb = QLabel(text)
    lb.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    h.addWidget(lb, 1)
    if button:
        h.addWidget(button)
    return h


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"เกี่ยวกับ {APP_NAME}")
        self.setWindowIcon(theme.icon("info", theme.ACCENT))
        self.setFixedWidth(460)

        # ---- หัว: ชื่อโปรแกรม
        logo = QLabel()
        app_png = QPixmap(str(ASSETS / "app.png"))
        logo.setPixmap(app_png.scaled(QSize(44, 44), Qt.AspectRatioMode.KeepAspectRatio,
                                      Qt.TransformationMode.SmoothTransformation)
                       if not app_png.isNull() else theme.icon("video", theme.ACCENT).pixmap(40, 40))
        title = theme.label(APP_NAME, "title")
        ver = theme.label(f"เวอร์ชัน {APP_VERSION} · ดูกล้องวงจรปิดในวง LAN", "muted")
        tv = QVBoxLayout()
        tv.setSpacing(2)
        tv.addWidget(title)
        tv.addWidget(ver)
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(logo)
        head.addLayout(tv, 1)

        # ---- การ์ดผู้จัดทำ
        card = QFrame()
        card.setProperty("card", True)
        cv = QVBoxLayout(card)
        cv.setContentsMargins(20, 18, 20, 18)
        cv.setSpacing(12)
        cv.addWidget(theme.label("ผู้จัดทำ", "section"))

        avatar = QLabel()
        pm = QPixmap(str(ASSETS / "author.png"))
        if not pm.isNull():
            avatar.setPixmap(pm.scaled(QSize(84, 84), Qt.AspectRatioMode.KeepAspectRatio,
                                       Qt.TransformationMode.SmoothTransformation))
        avatar.setFixedSize(84, 84)
        name = QLabel(AUTHOR_NAME)
        name.setStyleSheet("font-size: 15pt; font-weight: 700;")
        alias = theme.label(f"({AUTHOR_ALIAS})", "muted")
        nv = QVBoxLayout()
        nv.setSpacing(2)
        nv.addStretch()
        nv.addWidget(name)
        nv.addWidget(alias)
        nv.addStretch()
        who = QHBoxLayout()
        who.setSpacing(16)
        who.addWidget(avatar)
        who.addLayout(nv, 1)
        cv.addLayout(who)

        line = QFrame()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER};")
        cv.addWidget(line)

        self.copy_btn = theme.btn("คัดลอก", "copy", kind="ghost", tip="คัดลอกเบอร์โทร")
        self.copy_btn.clicked.connect(self._copy_phone)
        fb_btn = theme.btn("เปิด", "link", kind="ghost", tip="เปิด Facebook ในเบราว์เซอร์")
        fb_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(AUTHOR_FACEBOOK)))
        fb = QLabel(f'<a href="{AUTHOR_FACEBOOK}" style="color:{theme.ACCENT};text-decoration:none;">'
                    f'facebook.com/Ev.Unsmile</a>')
        fb.setOpenExternalLinks(True)
        fb_row = QHBoxLayout()
        fb_row.setSpacing(12)
        fic = QLabel("f")
        fic.setFixedWidth(20)
        fic.setAlignment(Qt.AlignmentFlag.AlignCenter)
        fic.setStyleSheet(f"color:{theme.MUTED}; font-weight:800; font-size:13pt; font-family:'Segoe UI';")
        fb_row.addWidget(fic)
        fb_row.addWidget(fb, 1)
        fb_row.addWidget(fb_btn)

        cv.addLayout(_row("building", AUTHOR_COMPANY))
        cv.addLayout(_row("phone", AUTHOR_PHONE, self.copy_btn))
        cv.addLayout(fb_row)

        credits = theme.label(
            "สร้างด้วย Python · PySide6 (Qt) · FFmpeg ผ่าน PyAV · pytapo · ไอคอน Lucide", "muted")
        credits.setWordWrap(True)
        credits.setStyleSheet("font-size: 8.5pt;")

        self.upd_btn = theme.btn("ตรวจหาอัปเดต", "refresh", kind="ghost")
        self.upd_btn.clicked.connect(self._check_update)
        close = theme.btn("ปิด", kind="primary")
        close.clicked.connect(self.accept)
        foot = QHBoxLayout()
        foot.addWidget(credits, 1)
        foot.addWidget(self.upd_btn)
        foot.addWidget(close)

        v = QVBoxLayout(self)
        v.setContentsMargins(22, 20, 22, 18)
        v.setSpacing(16)
        v.addLayout(head)
        v.addWidget(card)
        v.addLayout(foot)

    def _check_update(self):
        from updater import UpdateChecker  # noqa: PLC0415
        self.upd_btn.setEnabled(False)
        self.upd_btn.setText("กำลังตรวจ…")
        self._checker = UpdateChecker(self)
        self._checker.found.connect(self._on_found)
        self._checker.none.connect(lambda: self._upd_done("เป็นเวอร์ชันล่าสุดแล้ว"))
        self._checker.failed.connect(lambda m: self._upd_done("ตรวจไม่ได้ (ไม่มีเน็ต?)"))
        self._checker.start()

    def _upd_done(self, text):
        self.upd_btn.setText(text)
        QTimer.singleShot(3000, lambda: (self.upd_btn.setText("ตรวจหาอัปเดต"), self.upd_btn.setEnabled(True)))

    def _on_found(self, info):
        from updater import offer_update  # noqa: PLC0415
        self._upd_done(f"มีเวอร์ชัน {info['version']}")
        if offer_update(self, info, manual=True):
            self.accept()
            win = self.parent()
            if win is not None:
                win.close()

    def _copy_phone(self):
        QGuiApplication.clipboard().setText(AUTHOR_PHONE.replace("-", ""))
        self.copy_btn.setText("คัดลอกแล้ว")
        QTimer.singleShot(1500, lambda: self.copy_btn.setText("คัดลอก"))


"""ธีมมืดแบบทันสมัย + ไอคอนเส้น (วาดจาก SVG ในตัว ไม่ต้องมีไฟล์รูป)

ไอคอนดัดแปลงจาก Lucide (lucide.dev, ISC License)
"""
from PySide6.QtCore import QByteArray, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPalette, QPixmap
from PySide6.QtSvg import QSvgRenderer

BG = "#0e1014"
SURFACE = "#161a22"
SURFACE2 = "#1d2230"
HOVER = "#262d3d"
BORDER = "#262c3a"
TEXT = "#e7eaf0"
MUTED = "#8a93a6"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2f6fdc"
DANGER = "#ef4444"
SUCCESS = "#22c55e"
WARN = "#f59e0b"

_ICONS = {
    "camera": '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
              '<circle cx="12" cy="13" r="3"/>',
    "record": '<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="4" fill="{c}"/>',
    "refresh": '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
               '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>',
    "maximize": '<path d="M8 3H5a2 2 0 0 0-2 2v3"/><path d="M21 8V5a2 2 0 0 0-2-2h-3"/>'
                '<path d="M3 16v3a2 2 0 0 0 2 2h3"/><path d="M16 21h3a2 2 0 0 0 2-2v-3"/>',
    "settings": '<line x1="21" x2="14" y1="4" y2="4"/><line x1="10" x2="3" y1="4" y2="4"/>'
                '<line x1="21" x2="12" y1="12" y2="12"/><line x1="8" x2="3" y1="12" y2="12"/>'
                '<line x1="21" x2="16" y1="20" y2="20"/><line x1="12" x2="3" y1="20" y2="20"/>'
                '<line x1="14" x2="14" y1="2" y2="6"/><line x1="8" x2="8" y1="10" y2="14"/>'
                '<line x1="16" x2="16" y1="18" y2="22"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
             '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "up": '<path d="m18 15-6-6-6 6"/>',
    "down": '<path d="m6 9 6 6 6-6"/>',
    "left": '<path d="m15 18-6-6 6-6"/>',
    "right": '<path d="m9 18 6-6-6-6"/>',
    "upleft": '<path d="M17 17 7 7"/><path d="M7 17V7h10"/>',
    "upright": '<path d="M7 7h10v10"/><path d="M7 17 17 7"/>',
    "downleft": '<path d="M17 7 7 17"/><path d="M17 17H7V7"/>',
    "downright": '<path d="m7 7 10 10"/><path d="M17 7v10H7"/>',
    "stop": '<rect x="7" y="7" width="10" height="10" rx="2" fill="{c}"/>',
    "play": '<polygon points="7 4 20 12 7 20 7 4" fill="{c}"/>',
    "wand": '<path d="m21.64 3.64-1.28-1.28a1.21 1.21 0 0 0-1.72 0L2.36 18.64a1.21 1.21 0 0 0 0 1.72l1.28 1.28'
            'a1.2 1.2 0 0 0 1.72 0L21.64 5.36a1.2 1.2 0 0 0 0-1.72"/><path d="m14 7 3 3"/>'
            '<path d="M5 6v4"/><path d="M19 14v4"/><path d="M10 2v2"/><path d="M7 8H3"/><path d="M21 16h-4"/>',
    "bulb": '<path d="M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5.7.7 1.3 1.5 1.5 2.5"/>'
            '<path d="M9 18h6"/><path d="M10 22h4"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "phone": '<path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 '
             '19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 '
             '2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 '
             '2.81.7A2 2 0 0 1 22 16.92z"/>',
    "link": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
            '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "building": '<rect x="4" y="2" width="16" height="20" rx="2"/><path d="M9 22v-4h6v4"/>'
                '<path d="M8 6h.01"/><path d="M12 6h.01"/><path d="M16 6h.01"/><path d="M8 10h.01"/>'
                '<path d="M12 10h.01"/><path d="M16 10h.01"/><path d="M8 14h.01"/><path d="M12 14h.01"/>'
                '<path d="M16 14h.01"/>',
    "copy": '<rect width="14" height="14" x="8" y="8" rx="2"/>'
            '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>',
    "eye":'<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>',
    "video": '<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/>'
             '<rect x="2" y="6" width="14" height="12" rx="2"/>',
    "folder": '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4'
              'a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
    "radar": '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/><path d="M4 6h.01"/><path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/>'
             '<path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/><path d="M12 18h.01"/><path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/>'
             '<circle cx="12" cy="12" r="2"/><path d="m13.41 10.59 5.66-5.66"/>',
}


def icon(name: str, color: str = TEXT, size: int = 48) -> QIcon:
    body = _ICONS[name].replace("{c}", color)
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{color}" '
           f'stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
    pm = QPixmap(size, size)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    QSvgRenderer(QByteArray(svg.encode())).render(p)
    p.end()
    return QIcon(pm)


ICON_SIZE = QSize(18, 18)

QSS = f"""
QWidget {{ color: {TEXT}; font-family: "Leelawadee UI", "Segoe UI"; font-size: 10pt; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {SURFACE2}; color: {TEXT}; border: 1px solid {BORDER}; padding: 6px 8px; border-radius: 6px; }}

/* ---------- ปุ่ม ---------- */
QPushButton {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px; padding: 7px 14px; }}
QPushButton:hover {{ background: {HOVER}; border-color: #34405a; }}
QPushButton:pressed {{ background: #151a26; }}
QPushButton:disabled {{ color: #586074; background: {SURFACE}; }}
QPushButton[kind="primary"] {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {ACCENT_HOVER}; }}
QPushButton[kind="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[kind="ghost"]:hover {{ background: {SURFACE2}; border-color: {BORDER}; }}
QPushButton[kind="danger"]:hover {{ background: #3a1d22; border-color: #6b2730; color: #ffb4b4; }}
QPushButton#recBtn:checked {{ background: {DANGER}; border-color: {DANGER}; color: white; font-weight: 600; }}
QPushButton[seg="left"], QPushButton[seg="right"] {{ padding: 6px 14px; border-radius: 0; background: {SURFACE}; color: {MUTED}; }}
QPushButton[seg="left"] {{ border-top-left-radius: 8px; border-bottom-left-radius: 8px; }}
QPushButton[seg="right"] {{ border-top-right-radius: 8px; border-bottom-right-radius: 8px; border-left: none; }}
QPushButton[seg]:checked {{ background: {ACCENT}; border-color: {ACCENT}; color: white; font-weight: 600; }}
QPushButton#lightBtn {{ padding: 9px 14px; font-weight: 600; }}
QPushButton#lightBtn:checked {{ background: #f5b301; border-color: #f5b301; color: #1a1300; }}
QPushButton[seg="mid"] {{ padding: 6px 10px; border-radius: 0; background: {SURFACE}; color: {MUTED}; border-left: none; }}
QPushButton[pad="true"] {{ border-radius: 22px; padding: 0; background: {SURFACE2}; }}
QPushButton[pad="true"]:pressed {{ background: {ACCENT}; border-color: {ACCENT}; }}

/* ---------- ช่องกรอก ---------- */
QLineEdit, QSpinBox, QComboBox {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 10px; selection-background-color: {ACCENT}; min-height: 20px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QLineEdit:hover, QSpinBox:hover, QComboBox:hover {{ border-color: #36405a; }}
QComboBox::drop-down {{ border: none; width: 26px; }}
QComboBox QAbstractItemView {{ background: {SURFACE2}; border: 1px solid {BORDER}; border-radius: 8px;
    selection-background-color: {ACCENT}; outline: 0; padding: 4px; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 18px; border: none; background: transparent; }}

/* ---------- รายการ / แท็บ / กลุ่ม ---------- */
QListWidget {{ background: transparent; border: none; outline: 0; }}
QListWidget::item {{ padding: 8px 8px; margin: 2px 0; border-radius: 10px; border: 1px solid transparent; }}
QListWidget::item:hover {{ background: {SURFACE2}; }}
QListWidget::item:selected {{ background: #18294a; border: 1px solid #2c4d86; color: {TEXT}; }}
QTabWidget::pane {{ border: none; }}
QTabBar::tab {{ background: transparent; color: {MUTED}; padding: 8px 18px; margin-right: 4px;
    border-bottom: 2px solid transparent; font-weight: 600; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom-color: {ACCENT}; }}
QTabBar::tab:hover {{ color: {TEXT}; }}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #4a5468; background: {SURFACE}; }}
QCheckBox::indicator:hover {{ border-color: {ACCENT}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; image: url("{{CHECK}}"); }}
QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #2c3344; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #3a4358; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: white; border: 3px solid {ACCENT}; width: 12px; height: 12px;
    margin: -7px 0; border-radius: 9px; }}

/* ---------- การ์ด / ป้ายข้อความ ---------- */
QFrame[card="true"] {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 14px; }}
QFrame[card="true"] QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: 12pt; font-weight: 700; }}
QLabel[role="section"] {{ color: {MUTED}; font-size: 9pt; font-weight: 700; }}
QLabel[role="muted"] {{ color: {MUTED}; }}
QLabel[role="hint"] {{ background: #121d30; color: #a9c7f5; border: 1px solid #22385c; border-radius: 10px; padding: 8px 12px; }}
QLabel[role="ok"] {{ color: #6ee7a0; }}
QLabel[role="err"] {{ color: #fca5a5; }}
QLabel[role="preview"] {{ background: #07080b; color: #4b5263; border: 1px solid {BORDER}; border-radius: 10px; }}

/* ---------- ช่องภาพกล้อง ---------- */
QFrame#tile {{ background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 12px; }}
QFrame#tile[active="true"] {{ border: 2px solid {ACCENT}; }}
QFrame#tile QLabel {{ background: transparent; }}
QLabel#tileTitle {{ font-weight: 600; }}
QLabel#video {{ background: #05060a; color: #59606f; border-bottom-left-radius: 11px; border-bottom-right-radius: 11px; }}
QLabel#dot {{ font-size: 8pt; color: {WARN}; }}
QLabel#dot[state="live"] {{ color: {SUCCESS}; }}
QLabel#dot[state="err"] {{ color: {DANGER}; }}
QLabel#rec {{ background: {DANGER}; color: white; border-radius: 8px; padding: 1px 8px; font-size: 8pt; font-weight: 700; }}
QLabel#appTitle {{ font-size: 12pt; font-weight: 700; }}
QLabel#statusPill {{ background: {SURFACE2}; color: {MUTED}; border: 1px solid {BORDER}; border-radius: 11px; padding: 2px 10px; }}
"""


def apply(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, col in (
        (QPalette.ColorRole.Window, BG), (QPalette.ColorRole.Base, SURFACE),
        (QPalette.ColorRole.AlternateBase, SURFACE2), (QPalette.ColorRole.Button, SURFACE2),
        (QPalette.ColorRole.Text, TEXT), (QPalette.ColorRole.WindowText, TEXT),
        (QPalette.ColorRole.ButtonText, TEXT), (QPalette.ColorRole.Highlight, ACCENT),
        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, SURFACE2),
        (QPalette.ColorRole.ToolTipText, TEXT), (QPalette.ColorRole.PlaceholderText, "#5f687c"),
    ):
        pal.setColor(role, QColor(col))
    app.setPalette(pal)
    # เครื่องหมายถูกของ checkbox (QSS ต้องอ้างไฟล์รูป จึงเขียน SVG เล็กๆ ไว้ในโฟลเดอร์ชั่วคราว)
    import tempfile  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415
    check = Path(tempfile.gettempdir()) / "lancam_check.svg"
    try:
        check.write_text('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" '
                         'stroke-width="3.5" stroke-linecap="round" stroke-linejoin="round">'
                         '<path d="M20 6 9 17l-5-5"/></svg>', encoding="utf-8")
    except OSError:
        pass
    app.setStyleSheet(QSS.replace("{CHECK}", check.as_posix()))


def repolish(w):
    """เรียกหลังเปลี่ยน property ที่ใช้ใน QSS"""
    w.style().unpolish(w)
    w.style().polish(w)


def card(widget_cls=None):
    from PySide6.QtWidgets import QFrame  # noqa: PLC0415
    f = (widget_cls or QFrame)()
    f.setProperty("card", True)
    return f


def btn(text="", icon_name=None, kind=None, tip=None, color=TEXT):
    from PySide6.QtWidgets import QPushButton  # noqa: PLC0415
    b = QPushButton(text)
    if icon_name:
        b.setIcon(icon(icon_name, color))
        b.setIconSize(ICON_SIZE)
    if kind:
        b.setProperty("kind", kind)
    if tip:
        b.setToolTip(tip)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def label(text="", role=None):
    from PySide6.QtWidgets import QLabel  # noqa: PLC0415
    lb = QLabel(text)
    if role:
        lb.setProperty("role", role)
    return lb

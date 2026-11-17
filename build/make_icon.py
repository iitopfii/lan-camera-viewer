"""สร้างไอคอนโปรแกรม assets/app.ico (และ app.png) – รันครั้งเดียวตอน build"""
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent.parent
app = QGuiApplication(sys.argv)

CAM = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="white" stroke-width="2" '
       'stroke-linecap="round" stroke-linejoin="round">'
       '<path d="m16 13 5.223 3.482a.5.5 0 0 0 .777-.416V7.87a.5.5 0 0 0-.752-.432L16 10.5"/>'
       '<rect x="2" y="6" width="14" height="12" rx="2"/><circle cx="9" cy="12" r="2.2" fill="white"/></svg>')


def render(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32)
    img.fill(Qt.GlobalColor.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    m = size * 0.04
    r = QRectF(m, m, size - 2 * m, size - 2 * m)
    path = QPainterPath()
    path.addRoundedRect(r, size * 0.22, size * 0.22)
    g = QLinearGradient(r.topLeft(), r.bottomRight())
    g.setColorAt(0, QColor("#4f94ff"))
    g.setColorAt(1, QColor("#1d4ed8"))
    p.fillPath(path, g)
    pad = size * 0.17
    QSvgRenderer(QByteArray(CAM.encode())).render(p, QRectF(pad, pad, size - 2 * pad, size - 2 * pad))
    p.end()
    return img


big = render(256)
big.save(str(ROOT / "assets" / "app.png"))
im = Image.open(ROOT / "assets" / "app.png").convert("RGBA")
im.save(ROOT / "assets" / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
print("icon ok")

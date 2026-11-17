# -*- mode: python ; coding: utf-8 -*-
# สร้างโปรแกรม: python -m PyInstaller build/LANCameraViewer.spec --noconfirm
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent

# ปลั๊กอิน Qt ที่หน้าต่างต้องใช้ (บาง PySide6 hook ไม่ได้ใส่มาให้) – platforms จำเป็นที่สุด
import PySide6
_PL = Path(PySide6.__file__).parent / "plugins"
QT_PLUGINS = [(str(f), f"PySide6/plugins/{f.parent.name}")
              for sub in ("platforms", "styles", "imageformats", "iconengines")
              for f in (_PL / sub).glob("*.dll")
              if sub != "imageformats" or f.stem.lower() in ("qjpeg", "qsvg", "qico", "qgif")]

a = Analysis(
    [str(ROOT / "tapo_viewer.py")],
    pathex=[str(ROOT)],
    datas=[(str(ROOT / "assets"), "assets")],
    binaries=QT_PLUGINS,
    hiddenimports=collect_submodules("pytapo") + ["settings_dialog", "about_dialog", "light_control", "updater", "web_server", "web_dialog", "relay_client"]
                  + collect_submodules("websockets")
                  + collect_submodules("cryptography.x509") + collect_submodules("cryptography.hazmat"),
    excludes=["tkinter", "matplotlib", "cv2", "numpy", "PIL", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.QtQml", "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
              "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization"],
    noarchive=False,
)
# ตัดไฟล์ที่ไม่ใช้ออก ให้ตัวติดตั้งเล็กลง
DROP = ("opengl32sw.dll", "Qt6Pdf", "Qt6Qml", "Qt6Quick", "Qt6VirtualKeyboard", "Qt6OpenGL")
a.binaries = [b for b in a.binaries if not any(d.lower() in b[0].lower() for d in DROP)]
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/").split("/")]
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="LANCameraViewer",
    icon=str(ROOT / "assets" / "app.ico"),
    console=False,
    upx=False,
    version=str(ROOT / "build" / "version_info.txt"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="LANCameraViewer")

"""ข้อมูลโปรแกรม ผู้จัดทำ และตำแหน่งไฟล์ (แยกกรณีรันจากโค้ด กับติดตั้งเป็น .exe)"""
import os
import sys
from pathlib import Path

APP_NAME = "LAN Camera Viewer"
APP_VERSION = "1.1.3"
APP_ID = "LANCameraViewer"
GITHUB_REPO = "iitopfii/lan-camera-viewer"  # ใช้ตรวจอัปเดตจาก GitHub Releases

AUTHOR_NAME = "Topf'ii Chanwit"
AUTHOR_ALIAS = "Ei-Zale"
AUTHOR_COMPANY = "Hypebizsoft CO., LTD."
AUTHOR_PHONE = "061-119-4393"
AUTHOR_FACEBOOK = "https://www.facebook.com/Ev.Unsmile"

FROZEN = getattr(sys, "frozen", False)
# โฟลเดอร์ของตัวโปรแกรม (อ่านอย่างเดียวเมื่อติดตั้งใน Program Files)
APP_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).resolve().parent
# ไฟล์ที่แนบมากับโปรแกรม (รูป ฯลฯ)
RES_DIR = Path(getattr(sys, "_MEIPASS", APP_DIR))
ASSETS = RES_DIR / "assets"


def _user_dir(env: str, fallback: Path) -> Path:
    base = os.environ.get(env)
    return Path(base) if base else fallback


if FROZEN:
    # ติดตั้งแล้ว: เก็บค่าตั้งไว้ใน AppData ของผู้ใช้ (Program Files เขียนไม่ได้)
    DATA_DIR = _user_dir("APPDATA", Path.home() / "AppData" / "Roaming") / APP_NAME
    DEFAULT_SNAPSHOT_DIR = str(Path.home() / "Pictures" / APP_NAME)
    DEFAULT_RECORD_DIR = str(Path.home() / "Videos" / APP_NAME)
else:
    DATA_DIR = APP_DIR
    DEFAULT_SNAPSHOT_DIR = "snapshots"
    DEFAULT_RECORD_DIR = "recordings"

DATA_DIR.mkdir(parents=True, exist_ok=True)


def media_dir(cfg: dict, key: str) -> Path:
    """โฟลเดอร์เก็บภาพ/วิดีโอ – ถ้าใส่เป็นชื่อเฉยๆ จะอยู่ในโฟลเดอร์ข้อมูลของโปรแกรม"""
    default = DEFAULT_SNAPSHOT_DIR if key == "snapshot_dir" else DEFAULT_RECORD_DIR
    value = cfg.get(key)
    if FROZEN and value in ("snapshots", "recordings"):  # ค่าที่ติดมาจาก config รุ่นรันจากโค้ด → ใช้ Pictures/Videos
        value = None
    p = Path(value or default)
    return p if p.is_absolute() else DATA_DIR / p

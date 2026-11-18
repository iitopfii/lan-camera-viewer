"""อัปเดตอัตโนมัติจาก GitHub Releases

ตรวจรุ่นล่าสุดผ่าน GitHub API → ถ้าใหม่กว่า ถามผู้ใช้ → ดาวน์โหลดตัวติดตั้ง (ตรวจ SHA-256 ถ้า GitHub ให้มา)
→ รันตัวติดตั้งแบบเงียบ แล้วปิดโปรแกรม ตัวติดตั้งจะเปิดโปรแกรมให้ใหม่เองเมื่อเสร็จ
ใช้แค่ไลบรารีมาตรฐาน ไม่ต้องติดตั้งอะไรเพิ่ม
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
import threading
import urllib.request
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from app_info import APP_DIR, APP_NAME, APP_VERSION, FROZEN, GITHUB_REPO

API_LATEST = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{GITHUB_REPO}/releases"
UA = {"User-Agent": f"{APP_NAME.replace(' ', '')}/{APP_VERSION}", "Accept": "application/vnd.github+json"}


def parse_version(v: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", v or "")
    return tuple(int(n) for n in nums[:4]) or (0,)


def is_newer(latest: str, current: str = APP_VERSION) -> bool:
    return parse_version(latest) > parse_version(current)


def fetch_latest(timeout: float = 8) -> dict | None:
    """คืน {"version", "notes", "url", "name", "size", "sha256", "page"} ของรุ่นล่าสุด หรือ None ถ้าไม่มีไฟล์ติดตั้ง"""
    req = urllib.request.Request(API_LATEST, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 (URL คงที่ของ GitHub)
        data = json.load(r)
    asset = next((a for a in data.get("assets", [])
                  if a.get("name", "").lower().endswith(".exe") and "setup" in a["name"].lower()), None)
    if not asset:
        return None
    digest = asset.get("digest") or ""
    return {
        "version": data.get("tag_name", "").lstrip("vV"),
        "notes": (data.get("body") or "").strip(),
        "url": asset["browser_download_url"],
        "name": asset["name"],
        "size": asset.get("size", 0),
        "sha256": digest.split(":", 1)[1].lower() if digest.startswith("sha256:") else "",
        "page": data.get("html_url") or RELEASES_PAGE,
    }


def download(info: dict, progress=None, cancelled=lambda: False) -> Path:
    """ดาวน์โหลดตัวติดตั้งไปไว้ในโฟลเดอร์ temp แล้วตรวจขนาด/SHA-256"""
    # ตัวติดตั้งจะถูกรันในเครื่อง → รับเฉพาะไฟล์จาก GitHub ของโปรเจกต์นี้ ที่มีค่า SHA-256 ให้ตรวจ
    if not str(info.get("url", "")).startswith(f"https://github.com/{GITHUB_REPO}/releases/download/"):
        raise IOError("ที่อยู่ไฟล์อัปเดตไม่ใช่ของโปรเจกต์นี้")
    if not re.fullmatch(r"[0-9a-f]{64}", info.get("sha256") or ""):
        raise IOError("รุ่นนี้ไม่มีค่า SHA-256 ให้ตรวจสอบไฟล์")
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(str(info["name"])).name) or "setup.exe"
    dest = Path(tempfile.mkdtemp(prefix="LANCameraViewer-update-")) / name
    part = dest.with_suffix(".part")
    h = hashlib.sha256()
    req = urllib.request.Request(info["url"], headers={"User-Agent": UA["User-Agent"]})
    with urllib.request.urlopen(req, timeout=30) as r, open(part, "wb") as f:  # noqa: S310
        total = int(r.headers.get("Content-Length") or info.get("size") or 0)
        done = 0
        while chunk := r.read(256 * 1024):
            if cancelled():
                raise InterruptedError("ยกเลิก")
            f.write(chunk)
            h.update(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)
    if info.get("size") and part.stat().st_size != info["size"]:
        part.unlink(missing_ok=True)
        raise IOError("ไฟล์ที่ดาวน์โหลดไม่ครบ")
    if h.hexdigest() != info["sha256"]:
        part.unlink(missing_ok=True)
        raise IOError("ไฟล์ไม่ตรงกับที่ประกาศไว้ (SHA-256 ไม่ตรง)")
    dest.unlink(missing_ok=True)
    part.rename(dest)
    return dest


def run_installer(path: Path):
    """รันตัวติดตั้งแบบเงียบ ติดตั้งทับที่เดิม แล้วให้มันเปิดโปรแกรมใหม่เอง"""
    local = os.environ.get("LOCALAPPDATA", "")
    scope = "/CURRENTUSER" if local and str(APP_DIR).lower().startswith(local.lower()) else "/ALLUSERS"
    args = [str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS", scope, "/relaunch=1"]
    subprocess.Popen(args, close_fds=True, creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))  # noqa: S603


class UpdateChecker(QObject):
    """ตรวจในเธรดพื้นหลัง แล้วแจ้งผลกลับมาที่หน้าจอหลัก (UI ไม่ค้างแม้เน็ตช้า)"""
    found = Signal(dict)
    none = Signal()
    failed = Signal(str)

    def start(self):
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            info = fetch_latest()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e)[:120])
            return
        if info and is_newer(info["version"]):
            self.found.emit(info)
        else:
            self.none.emit()


class _DL(QObject):
    progress = Signal(int, int)
    done = Signal(str)
    error = Signal(str)


def offer_update(parent, info: dict, cfg: dict | None = None, manual: bool = False, save=None) -> bool:
    """ถามผู้ใช้ → ดาวน์โหลด → ติดตั้ง คืน True ถ้ากำลังติดตั้ง (โปรแกรมควรปิดตัว)"""
    if not manual and cfg is not None and cfg.get("skip_version") == info["version"]:
        return False
    notes = info["notes"][:900] + ("…" if len(info["notes"]) > 900 else "")
    box = QMessageBox(parent)
    box.setWindowTitle("มีเวอร์ชันใหม่")
    box.setIcon(QMessageBox.Icon.Information)
    box.setText(f"<b>{APP_NAME} {info['version']}</b> พร้อมให้อัปเดต (ตอนนี้ใช้ {APP_VERSION})")
    box.setInformativeText(("มีอะไรใหม่:\n" + notes + "\n\n" if notes else "")
                           + f"ขนาดดาวน์โหลด {info['size'] / 1e6:.1f} MB · โปรแกรมจะปิดแล้วเปิดใหม่เองหลังติดตั้ง")
    b_now = box.addButton("อัปเดตเลย", QMessageBox.ButtonRole.AcceptRole)
    box.addButton("ไว้ทีหลัง", QMessageBox.ButtonRole.RejectRole)
    b_skip = None if manual else box.addButton("ข้ามเวอร์ชันนี้", QMessageBox.ButtonRole.DestructiveRole)
    box.exec()
    if box.clickedButton() is b_skip and cfg is not None:
        cfg["skip_version"] = info["version"]
        if save:
            save(cfg)
        return False
    if box.clickedButton() is not b_now:
        return False
    if not FROZEN:
        QMessageBox.information(parent, "อัปเดต", "กำลังรันจากโค้ด – อัปเดตด้วย git pull แทน\n" + info["page"])
        return False

    dlg = QProgressDialog("กำลังดาวน์โหลดตัวติดตั้ง…", "ยกเลิก", 0, 100, parent)
    dlg.setWindowTitle("อัปเดต")
    dlg.setWindowModality(Qt.WindowModality.WindowModal)
    dlg.setMinimumDuration(0)
    dlg.setAutoReset(False)
    dlg.setAutoClose(False)
    stop = threading.Event()
    dlg.canceled.connect(stop.set)
    dlg.setValue(0)
    sig = _DL()
    result: dict = {}
    sig.progress.connect(lambda d, t: dlg.setValue(int(d * 100 / t)) if t else None)
    sig.done.connect(lambda p: (result.setdefault("path", p), dlg.done(1)))
    sig.error.connect(lambda m: (result.setdefault("error", m), dlg.done(0)))

    def work():
        try:
            sig.done.emit(str(download(info, sig.progress.emit, stop.is_set)))
        except Exception as e:  # noqa: BLE001
            sig.error.emit(str(e)[:200])

    threading.Thread(target=work, daemon=True).start()
    dlg.exec()
    stop.set()
    if "path" not in result:
        if result.get("error") and result["error"] != "ยกเลิก":
            QMessageBox.warning(parent, "อัปเดต", f"ดาวน์โหลดไม่สำเร็จ: {result['error']}\n\n"
                                f"ดาวน์โหลดเองได้ที่ {info['page']}")
        return False
    try:
        run_installer(Path(result["path"]))
    except OSError as e:
        QMessageBox.warning(parent, "อัปเดต", f"เปิดตัวติดตั้งไม่ได้: {e}")
        return False
    return True

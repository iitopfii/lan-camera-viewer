# LAN Camera Viewer

โปรแกรมดูกล้องวงจรปิดในวง LAN บน Windows ดูหลายกล้องพร้อมกัน ไม่ต้องผ่านคลาวด์

![icon](assets/app.png)

## ความสามารถ
- ดูภาพสดผ่าน RTSP ได้หลายกล้องพร้อมกัน รองรับกล้องหลายเลนส์ (เช่น Tapo C545D, Yoosee 2 เลนส์)
- คุณภาพอัตโนมัติ: ช่องเล็กใช้ภาพ SD ดับเบิลคลิกเพื่อขยายเป็น HD ทำให้เครื่องไม่หนัก
- หมุนกล้อง (Pan/Tilt) ผ่าน ONVIF
- ถ่ายภาพ อัดวิดีโอ (เก็บสตรีมเดิมโดยไม่แปลงไฟล์ จึงกิน CPU น้อย)
- เปิด/ปิดสปอตไลท์และเปลี่ยนโหมดกลางคืนของกล้อง Tapo
- หน้าตั้งค่า: เพิ่มกล้อง สแกนหาในวง LAN ตรวจหาสตรีมผ่าน ONVIF ทดสอบการเชื่อมต่อ
- อัปเดตตัวเองจาก GitHub Releases

รุ่นที่ตั้งค่าไว้ให้แล้ว: TP-Link Tapo, Yoosee, Hikvision, EZVIZ, Dahua, Reolink, VStarcam ส่วนรุ่นอื่นเลือก "กำหนดเอง" แล้วใส่ path ของ RTSP

## ดาวน์โหลด
ไปที่ [Releases](../../releases/latest) แล้วโหลด `LANCameraViewer-Setup-x.y.z.exe` มาติดตั้ง ไม่ต้องใช้สิทธิ์แอดมิน

> Windows SmartScreen อาจเตือนเพราะไฟล์ยังไม่มีลายเซ็นดิจิทัล ให้กด "More info" แล้วกด "Run anyway"

ค่าตั้งกล้องอยู่ที่ `%APPDATA%\LAN Camera Viewer\config.json` และอยู่ในเครื่องคุณเท่านั้น
ตัวอัปเดตจะเรียก GitHub API เพื่อตรวจเวอร์ชันอย่างเดียว ไม่ส่งข้อมูลใดๆ ออกไป ปิดได้ในหน้าตั้งค่า → ทั่วไป

## รันจากซอร์สโค้ด
ต้องใช้ Python 3.11 ขึ้นไป
```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
venv\Scripts\python tapo_viewer.py
```
คัดลอก `config.example.json` ไปเป็น `config.json` หรือตั้งค่าในโปรแกรมก็ได้

## สร้างตัวติดตั้งเอง
ต้องมี [Inno Setup 6](https://jrsoftware.org/isinfo.php) แล้วรัน `build\build.bat`

## ปล่อยเวอร์ชันใหม่
1. แก้เลขเวอร์ชันใน `app_info.py`, `build/installer.iss`, `build/version_info.txt`
2. รัน `build\build.bat`
3. `gh release create vX.Y.Z dist\LANCameraViewer-Setup-X.Y.Z.exe --notes "..."`

โปรแกรมที่ติดตั้งไว้จะเห็นเวอร์ชันใหม่เองตอนเปิดครั้งถัดไป

## ผู้จัดทำ
Topf'ii Chanwit (Ei-Zale) · Hypebizsoft CO., LTD. · [Facebook](https://www.facebook.com/Ev.Unsmile)

## License
[MIT](LICENSE) – ใช้ แก้ไข และแจกจ่ายต่อได้ แต่ต้องคงข้อความลิขสิทธิ์ไว้

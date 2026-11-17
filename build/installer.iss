; ตัวติดตั้ง LAN Camera Viewer (Inno Setup 6)
; สร้าง: ISCC.exe build\installer.iss  → dist\LANCameraViewer-Setup-1.1.1.exe

#define AppName "LAN Camera Viewer"
#define AppVersion "1.1.1"
#define AppPublisher "Hypebizsoft CO., LTD."
#define AppURL "https://github.com/iitopfii/lan-camera-viewer"
#define AppExe "LANCameraViewer.exe"

[Setup]
AppId={{6C1E2B7A-3F4D-4A8E-9B1C-5D2E7F8A9C30}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}
AppContact=061-119-4393
AppCopyright=Topf'ii Chanwit (Ei-Zale) - {#AppPublisher}
VersionInfoVersion={#AppVersion}
VersionInfoCompany={#AppPublisher}
VersionInfoDescription={#AppName} Setup
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
; ติดตั้งได้ทั้งแบบไม่ต้องเป็นแอดมิน (เฉพาะผู้ใช้นี้) หรือสำหรับทุกคนในเครื่อง
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist
OutputBaseFilename=LANCameraViewer-Setup-{#AppVersion}
SetupIconFile=..\assets\app.ico
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
WizardStyle=modern
Compression=lzma2/ultra64
SolidCompression=yes
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\LANCameraViewer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent
; อัปเดตจากในโปรแกรม (ติดตั้งแบบเงียบ + /relaunch=1) → เปิดโปรแกรมให้ใหม่หลังติดตั้งเสร็จ
Filename: "{app}\{#AppExe}"; Flags: nowait; Check: ShouldRelaunch

; หมายเหตุ: ค่าตั้งกล้อง (config.json) อยู่ที่ %APPDATA%\LAN Camera Viewer – ถอนการติดตั้งแล้วยังเก็บไว้
; ภาพถ่าย/วิดีโอ อยู่ที่ Pictures\LAN Camera Viewer และ Videos\LAN Camera Viewer

[Code]
function ShouldRelaunch: Boolean;
begin
  Result := WizardSilent and (ExpandConstant('{param:relaunch|0}') = '1');
end;


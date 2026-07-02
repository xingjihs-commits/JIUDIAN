; Solid 酒店管理系统 — 安装向导
; 使用 Inno Setup 6 编译
; 编译: ISCC.exe SolidInstaller.iss
;
; ── 图标（2026-06-17 新磨砂玻璃图标）──
;   源图   assets/app_icon.png  — 透明磨砂，应用内四主题通用
;   桌面   assets/app_icon.ico  — 墨绿渐变底+烫金边（小尺寸只留金色 emblem）
;   生成   python tools/build_app_icon.py（一键打包.bat [3.5/8] 自动执行）
;   EXE    Solid_onefile.spec icon=assets/app_icon.ico → 任务栏/快捷方式内嵌图标
;   安装包 SetupIconFile=..\assets\app_icon.ico → 安装向导标题栏图标
;
; ── 静默安装模式 ──
;   /SILENT       进度条可见，不需要交互
;   /VERYSILENT   完全无界面，不需要交互
;   /DIR="D:\MyPath"  指定安装目录
;   /LANG=en           指定语言
;   /TASKS="desktopicon"   可选任务
;   /LOG="install.log"     生成安装日志
; 示例:
;   SolidPMS_Setup_1.0.0.exe /VERYSILENT /DIR="D:\SolidPMS" /LANG=en /LOG="install.log"
; 远程推送安装:
;   SolidPMS_Setup_1.0.0.exe /VERYSILENT /SUPPRESSMSGBOXES /NORESTART

#define MyAppName "Solid PMS"
#define MyAppShortName "Solid PMS"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Solid 厂家直供"
#define MyAppURL "https://www.example.com"
#define MyAppExeName "Solid.exe"

[Setup]
AppId={{B8F4A3D2-1C5E-4A7B-9D6F-8E2C1A3B5D7F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
AppUpdatesURL={#MyAppURL}
DefaultDirName=D:\{#MyAppShortName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableReadyPage=yes
OutputDir=..\Output
OutputBaseFilename=SolidPMS_Setup_{#MyAppVersion}
SetupIconFile=..\assets\app_icon.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
Compression=lzma2/ultra
SolidCompression=yes
WizardStyle=modern
DisableWelcomePage=no
ShowLanguageDialog=auto
LanguageDetectionMethod=locale
VersionInfoVersion={#MyAppVersion}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription=Solid PMS
VersionInfoTextVersion={#MyAppVersion}
PrivilegesRequired=admin
UsedUserAreasWarning=no
CloseApplications=force
SetupLogging=yes
Uninstallable=yes
SetupMutex=SolidPMS_Setup_Mutex

[Languages]
Name: "chinesesimp"; MessagesFile: "languages\ChineseSimplified.isl"
Name: "en"; MessagesFile: "compiler:Default.isl"

[Files]
; 主程序
Source: "..\dist\Solid.exe"; DestDir: "{app}"; Flags: ignoreversion
; 32位桥接发卡程序
Source: "..\dist\rfl_bridge_32.exe"; DestDir: "{app}"; Flags: ignoreversion
; ── 资源目录（PyInstaller 单文件 EXE 内嵌了 themes/translations，
;    但目录版和后续维护需要外部资源，一并打包） ──
Source: "..\themes\*"; DestDir: "{app}\themes"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\translations\*"; DestDir: "{app}\translations"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\USB_LOCK_PROFILES\*"; DestDir: "{app}\USB_LOCK_PROFILES"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\POWER_CONTROLLER_PROFILES\*"; DestDir: "{app}\POWER_CONTROLLER_PROFILES"; Flags: ignoreversion recursesubdirs createallsubdirs
; ── 安装程序欢迎图（可选，不存在则跳过） ──
Source: "images\welcome.bmp"; DestDir: "{tmp}"; Flags: ignoreversion
; ── 品牌配置 ──
Source: "..\brand.json"; DestDir: "{app}"; Flags: ignoreversion
; ── Access 数据库引擎安装包（酒店现场无 ODBC 驱动时静默安装） ──
Source: "..\redist\access\*"; DestDir: "{app}\redist\access"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"
Name: "{autoprograms}\{#MyAppName} 卸载"; Filename: "{uninstallexe}"; WorkingDir: "{app}"
Name: "{commondesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "快捷方式："; Flags: checkedonce

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "立即运行 Solid PMS"; Flags: postinstall nowait skipifsilent unchecked shellexec

[Code]
var
  WelcomeImage: TBitmapImage;
  WelcomeBgPanel: TPanel;

procedure InitializeWizard;
var
  ImageFile: String;
begin
  ImageFile := ExpandConstant('{src}\images\welcome.bmp');
  if FileExists(ImageFile) then
  begin
    WelcomeBgPanel := TPanel.Create(WizardForm);
    WelcomeBgPanel.Parent := WizardForm.WelcomePage;
    WelcomeBgPanel.Left := 0;
    WelcomeBgPanel.Top := 0;
    WelcomeBgPanel.Width := WizardForm.WelcomePage.ClientWidth;
    WelcomeBgPanel.Height := WizardForm.WelcomePage.ClientHeight;
    WelcomeBgPanel.BevelOuter := bvNone;
    WelcomeBgPanel.BorderStyle := bsNone;

    WelcomeImage := TBitmapImage.Create(WizardForm);
    WelcomeImage.Parent := WelcomeBgPanel;
    WelcomeImage.AutoSize := False;
    WelcomeImage.Stretch := True;
    WelcomeImage.Left := 0;
    WelcomeImage.Top := 0;
    WelcomeImage.Width := WizardForm.WelcomePage.ClientWidth;
    WelcomeImage.Height := WizardForm.WelcomePage.ClientHeight;
    WelcomeImage.Bitmap.LoadFromFile(ImageFile);
  end;
end;

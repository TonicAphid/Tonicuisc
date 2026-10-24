; Tonicuisc Windows 安装包（Inno Setup 6）
;
; CI 里这样调用：
;   ISCC.exe /DAppVersion=0.0.1 ^
;            /DSourceDir=<...>\app\build\windows\x64\runner\Release ^
;            /DOutputDir=<...>\dist ^
;            tool\installer\windows\tonicuisc.iss
;
; 产物：dist\tonicuisc-windows-x64-setup.exe

#ifndef AppName
  #define AppName "Tonicuisc"
#endif

#ifndef AppVersion
  #define AppVersion "0.0.1"
#endif

#ifndef SourceDir
  #define SourceDir "..\..\..\app\build\windows\x64\runner\Release"
#endif

#ifndef OutputDir
  #define OutputDir "..\..\..\dist"
#endif

[Setup]
; 固定 AppId：升级时才会认成同一个程序，而不是装出两份
AppId={{5E2F0C0F-6B7A-4C2E-9F3D-2A1B7C8D9E40}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher=Tonicuisc
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=tonicuisc-windows-x64-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
PrivilegesRequired=admin
; 允许普通用户改成装到自己的用户目录（不需要管理员）
PrivilegesRequiredOverridesAllowed=dialog
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\tonicuisc.exe
VersionInfoCompany=Tonicuisc
VersionInfoDescription={#AppName} installer
VersionInfoProductName={#AppName}
VersionInfoProductVersion={#AppVersion}
VersionInfoVersion={#AppVersion}.0
CloseApplications=yes
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\tonicuisc.exe"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\tonicuisc.exe"; WorkingDir: "{app}"; Tasks: desktopicon

[Run]
Filename: "{app}\tonicuisc.exe"; Description: "Launch {#AppName}"; Flags: nowait postinstall skipifsilent

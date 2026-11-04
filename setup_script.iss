; Inno Setup script for ScreenSplitter

#define MyAppName "ScreenSplitter"
#define VersionFile FileOpen(AddBackslash(SourcePath) + "VERSION")
#define MyAppVersion Trim(FileRead(VersionFile))
#expr FileClose(VersionFile)
#define MyAppPublisher "Bevis"
#define MyAppExeName "run_webserver.bat"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EFF123456789}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
WizardStyle=modern
OutputBaseFilename=ScreenSplitter_Setup
OutputDir=build
Compression=lzma
SolidCompression=yes
SetupIconFile=app.ico
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Dirs]
Name: "C:\ScreenSplitter_Data"; Permissions: users-modify

[Files]
; Run `python build_release.py` first.
Source: "build\ScreenSplitter_Dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove only the incompatible runtime libraries shipped in 4.3.5.
Type: files; Name: "{app}\Player\_internal\icuuc.dll"
Type: files; Name: "{app}\Player\_internal\icudt78.dll"
Type: files; Name: "{app}\AdminTool\_internal\icuuc.dll"
Type: files; Name: "{app}\AdminTool\_internal\icudt78.dll"

[Icons]
Name: "{autoprograms}\ScreenSplitter\使用說明"; Filename: "{app}\docs\usage.html"
Name: "{autodesktop}\ScreenSplitter WebServer"; Filename: "{app}\run_webserver.bat"; IconFilename: "{app}\WebServer\WebServer.exe"
Name: "{autodesktop}\ScreenSplitter Player"; Filename: "{app}\run_player.bat"; IconFilename: "{app}\Player\Player.exe"
Name: "{autodesktop}\ScreenSplitter Admin"; Filename: "{app}\run_admin.bat"; IconFilename: "{app}\AdminTool\AdminTool.exe"
Name: "{userstartup}\ScreenSplitter Players"; Filename: "{app}\run_player.bat"; WorkingDir: "{app}"; IconFilename: "{app}\Player\Player.exe"

[Run]
Filename: "{app}\run_webserver.bat"; Description: "Launch ScreenSplitter WebServer"; Flags: shellexec postinstall skipifsilent

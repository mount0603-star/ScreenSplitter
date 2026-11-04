; Inno Setup script for the ScreenSplitter SOURCE-MODE distribution (4.4.0 and later):
; a git checkout plus a portable Python runtime and MinGit, updated afterwards with update.bat.
; Run `venv\Scripts\python.exe build_source_release.py` first (it fills build\ScreenSplitter_Source_Dist).
; The PyInstaller layout is still built by build_release.py + setup_script.iss.

#define MyAppName "ScreenSplitter"
#define VersionFile FileOpen(AddBackslash(SourcePath) + "VERSION")
#define MyAppVersion Trim(FileRead(VersionFile))
#expr FileClose(VersionFile)
#define MyAppPublisher "Bevis"

[Setup]
AppId={{A1B2C3D4-E5F6-7890-ABCD-EFF123456789}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
WizardStyle=modern
OutputBaseFilename=ScreenSplitter_Setup_Source
OutputDir=build
Compression=lzma
SolidCompression=yes
SetupIconFile=app.ico
PrivilegesRequired=lowest

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "build\ScreenSplitter_Source_Dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; Remove the PyInstaller layout of 4.3.x. Site data (config.json, profiles\, web_profile\, logs)
; and a custom Player\music folder are not listed here and stay in place.
Type: filesandordirs; Name: "{app}\Player\_internal"
Type: filesandordirs; Name: "{app}\Player\WebViewPlayer"
Type: filesandordirs; Name: "{app}\Player\VLC"
Type: files; Name: "{app}\Player\Player.exe"
Type: files; Name: "{app}\Player\player.ico"
Type: filesandordirs; Name: "{app}\AdminTool"
Type: filesandordirs; Name: "{app}\WebServer"

[Icons]
Name: "{autoprograms}\ScreenSplitter\使用說明"; Filename: "{app}\docs\usage.html"
Name: "{autoprograms}\ScreenSplitter\更新 ScreenSplitter"; Filename: "{app}\update.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{autodesktop}\ScreenSplitter WebServer"; Filename: "{app}\run_webserver.bat"; WorkingDir: "{app}"; IconFilename: "{app}\app.ico"
Name: "{autodesktop}\ScreenSplitter Player"; Filename: "{app}\run_player.bat"; WorkingDir: "{app}"; IconFilename: "{app}\player.ico"
Name: "{autodesktop}\ScreenSplitter Admin"; Filename: "{app}\run_admin.bat"; WorkingDir: "{app}"; IconFilename: "{app}\admin.ico"
Name: "{userstartup}\ScreenSplitter Players"; Filename: "{app}\run_player.bat"; WorkingDir: "{app}"; IconFilename: "{app}\player.ico"

[Run]
Filename: "{app}\run_webserver.bat"; Description: "Launch ScreenSplitter WebServer"; Flags: shellexec postinstall skipifsilent

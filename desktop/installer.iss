; Windows installer for GreenBox (Inno Setup 6). Build with:
;   iscc /DAppVersion=0.2.0 desktop\installer.iss
; Installs per user (no administrator rights needed) and creates Start menu / desktop shortcuts.

#ifndef AppVersion
  #define AppVersion "0.2.0"
#endif

[Setup]
AppId={{6F4C2B1E-9A7D-4E3B-8C51-2D0F7A9E4B13}
AppName=GreenBox
AppVersion={#AppVersion}
AppVerName=GreenBox {#AppVersion}
AppPublisher=Maynooth University
AppPublisherURL=https://doi.org/10.5281/zenodo.23687749
DefaultDirName={autopf}\GreenBox
DefaultGroupName=GreenBox
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=dist
OutputBaseFilename=GreenBox-Setup
SetupIconFile=assets\GreenBox.ico
UninstallDisplayIcon={app}\GreenBox.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "dist\GreenBox\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\GreenBox"; Filename: "{app}\GreenBox.exe"
Name: "{autodesktop}\GreenBox"; Filename: "{app}\GreenBox.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\GreenBox.exe"; Description: "{cm:LaunchProgram,GreenBox}"; Flags: nowait postinstall skipifsilent

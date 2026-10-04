; Inno Setup script: iscc /DAppVersion=<x.y.z> packaging\windows\installer.iss
; Packs dist\somaari-disk (PyInstaller folder build) into dist\somaari-disk-<x.y.z>-setup.exe.
; Per-user install by default (no admin); WebView2 is checked at app start, not bundled.

#ifndef AppVersion
  #error Pass /DAppVersion=<version>
#endif

[Setup]
AppId={{6F1E5C2A-3B7D-4E8F-9A41-5D2C7B0E9F13}
AppName=Somaari Disk
AppVersion={#AppVersion}
AppPublisher=somaari-disk contributors
AppPublisherURL=https://github.com/ashwinkrpi/somaari-disk
DefaultDirName={autopf}\Somaari Disk
DefaultGroupName=Somaari Disk
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\..\dist
OutputBaseFilename=somaari-disk-{#AppVersion}-setup
SetupIconFile=..\icons\somaari-disk.ico
UninstallDisplayIcon={app}\somaari-disk.exe
LicenseFile=..\..\LICENSE
Compression=lzma2
SolidCompression=yes
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\..\dist\somaari-disk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\THIRD-PARTY-NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\Somaari Disk"; Filename: "{app}\somaari-disk.exe"
Name: "{autodesktop}\Somaari Disk"; Filename: "{app}\somaari-disk.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\somaari-disk.exe"; Description: "{cm:LaunchProgram,Somaari Disk}"; Flags: nowait postinstall skipifsilent

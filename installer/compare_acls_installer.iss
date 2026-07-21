; Inno Setup script for TFSync (Total File Sync).
; Builds a proper Windows installer (Start Menu shortcuts, uninstaller,
; optional PATH entry) around the PyInstaller-built .exe files.
;
; Compile with:
;   iscc installer\compare_acls_installer.iss /DMyAppVersion=1.0.0
;
; Expects the following to already exist (built by build.bat or CI first):
;   dist\compare_acls.exe
;   dist\tfsync_gui.exe
;   dist\sync_shares.exe
;   dist\run_scheduled_job.exe
;   dist\decode_mask.exe

#define MyAppName "TFSync"
#ifndef MyAppVersion
  #define MyAppVersion "0.0.0-dev"
#endif
#define MyAppPublisher "Internal Tools"
#define MyAppExeName "tfsync_gui.exe"

[Setup]
AppId={{6C8B1B1E-6E2E-4C7A-9C7B-ACL0COMPARE01}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
; Without these, Inno Setup silently reuses whatever install folder/Start
; Menu group was recorded under this AppId from a PRIOR install (e.g. if
; someone still has an old build installed from before the TFSync rename) -
; ignoring DefaultDirName/DefaultGroupName above entirely. Forcing them off
; guarantees every install (fresh or upgrade) lands in ...\TFSync.
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
OutputDir=installer_output
OutputBaseFilename=TFSync_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
UninstallDisplayIcon={app}\{#MyAppExeName}
WizardStyle=modern
DisableWelcomePage=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop icon for the GUI"; GroupDescription: "Additional icons:"; Flags: unchecked
Name: "addtopath"; Description: "Add install folder to PATH (lets you run compare_acls / decode_mask from any Command Prompt)"; GroupDescription: "Additional options:"; Flags: unchecked

[Files]
Source: "..\dist\compare_acls.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\tfsync_gui.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\sync_shares.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\run_scheduled_job.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\decode_mask.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\TFSync Command Prompt"; Filename: "{cmd}"; Parameters: "/K cd /d ""{app}"""; WorkingDir: "{app}"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; \
    ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath('{app}')

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function NeedsAddPath(Param: string): boolean;
var
  OrigPath: string;
begin
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', OrigPath) then
  begin
    Result := True;
    exit;
  end;
  { Only add it if it's not already there, to avoid an ever-growing PATH
    across repeated installs/upgrades. }
  Result := Pos(';' + Param + ';', ';' + OrigPath + ';') = 0;
end;

#ifndef SourceDir
  #define SourceDir "build\app"
#endif

#ifndef OutputDir
  #define OutputDir "..\installer"
#endif

#ifndef AppVersion
  #define AppVersion "2.2.0"
#endif

[Setup]
AppId={{3E570825-D53B-4B92-94C5-2A4C792BC9F1}
AppName=z-paper
AppVersion={#AppVersion}
AppPublisher=z-paper
DefaultDirName={localappdata}\Programs\z-paper
DefaultGroupName=z-paper
DisableProgramGroupPage=yes
OutputDir={#OutputDir}
OutputBaseFilename=z-paper-{#AppVersion}-setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayName=z-paper
UninstallDisplayIcon={app}\z-paper.ico
SetupIconFile={#SourceDir}\z-paper.ico

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional tasks"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: filesandordirs; Name: "{app}\backend\app"
Type: filesandordirs; Name: "{app}\backend\alembic"
Type: filesandordirs; Name: "{app}\backend\python"
Type: filesandordirs; Name: "{app}\frontend\dist"

[Dirs]
Name: "{app}\backend\data"
Name: "{app}\backend\uploads"
Name: "{app}\backend\logs"

[Icons]
Name: "{group}\z-paper"; Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\z-paper.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\z-paper.ico"
Name: "{group}\Stop z-paper"; Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\z-paper-stop.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\z-paper.ico"
Name: "{autodesktop}\z-paper"; Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\z-paper.vbs"""; WorkingDir: "{app}"; IconFilename: "{app}\z-paper.ico"; Tasks: desktopicon

[Run]
Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\z-paper.vbs"""; Description: "Launch z-paper"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{win}\System32\wscript.exe"; Parameters: """{app}\z-paper-stop.vbs"""; Flags: runhidden; RunOnceId: "StopZPaper"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  StopScript: String;
begin
  Result := '';
  StopScript := ExpandConstant('{app}\z-paper-stop.vbs');
  if FileExists(StopScript) then
  begin
    Exec(ExpandConstant('{win}\System32\wscript.exe'), '"' + StopScript + '"', ExpandConstant('{app}'), SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

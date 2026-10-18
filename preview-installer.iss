#define AppName "纸飞机架构验证版 M1"
#ifndef SourcePath
#define SourcePath "dist\PaperPlaneStudioPreview"
#endif
#ifndef OutputPath
#define OutputPath "dist\preview-installer"
#endif
[Setup]
AppId={{9F197ED9-BE3C-4823-BEB0-693353517D05}
AppName={#AppName}
AppVersion=0.2.0.1
AppVerName={#AppName} 0.2.0-m1.1
DefaultDirName={localappdata}\Programs\PaperPlaneStudioPreview
DisableDirPage=yes
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir={#OutputPath}
OutputBaseFilename=PaperPlaneStudioPreview-M1-Setup-0.2.0.1
SetupIconFile=web\icon.ico
UninstallDisplayIcon={app}\PaperPlaneStudioPreview.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
AppMutex=PaperPlaneStudioPreview
UsePreviousAppDir=no
[Files]
Source: "{#SourcePath}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PaperPlaneStudioPreview.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PaperPlaneStudioPreview.exe"
[Run]
Filename: "{app}\PaperPlaneStudioPreview.exe"; Description: "打开文章编辑验证版"; Flags: nowait postinstall skipifsilent
[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  Result := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
     ExpandConstant('{localappdata}\Programs\PaperPlaneStudioPreview')) <> 0 then
    Result := '验证版必须安装到独立的 PaperPlaneStudioPreview 目录。';
end;

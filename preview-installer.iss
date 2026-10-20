#define AppName "纸飞机架构验证版"
#ifndef SourcePath
#define SourcePath "dist\PaperPlaneStudioPreview"
#endif
#ifndef OutputPath
#define OutputPath "dist\preview-installer"
#endif
#ifndef UpgradeHelperPath
#define UpgradeHelperPath "build\upgrade-helper\upgrade_paths.dll"
#endif
[Setup]
AppId={{9F197ED9-BE3C-4823-BEB0-693353517D05}
AppName={#AppName}
AppVersion=0.4.0.5
AppVerName={#AppName} 0.4.0-m4.2.1
DefaultDirName={localappdata}\Programs\PaperPlaneStudioPreview
DisableDirPage=yes
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir={#OutputPath}
OutputBaseFilename=PaperPlaneStudioPreview-Setup-0.4.0.5
SetupIconFile=web\icon.ico
UninstallDisplayIcon={app}\PaperPlaneStudioPreview.exe
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=no
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
[InstallDelete]
Type: filesandordirs; Name: "{app}\_internal"; Check: CanCleanRuntime
Type: files; Name: "{app}\PaperPlaneStudioPreview.exe"; Check: CanCleanRuntime
Type: files; Name: "{autodesktop}\纸飞机架构验证版 M1.lnk"; Check: CanCleanRuntime
Type: files; Name: "{userprograms}\纸飞机架构验证版 M1\纸飞机架构验证版 M1.lnk"; Check: CanCleanRuntime
Type: dirifempty; Name: "{userprograms}\纸飞机架构验证版 M1"; Check: CanCleanRuntime
[Files]
Source: "tools\prepare_desktop_upgrade.ps1"; Flags: dontcopy
Source: "{#UpgradeHelperPath}"; DestName: "upgrade_paths.dll"; Flags: dontcopy
Source: "{#SourcePath}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PaperPlaneStudioPreview.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PaperPlaneStudioPreview.exe"
[Run]
Filename: "{app}\PaperPlaneStudioPreview.exe"; Description: "打开纸飞机架构验证版"; Flags: nowait postinstall skipifsilent
[Code]
var
  PreparedDirectory: String;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ExitCode: Integer;
  Command: String;
  PowerShell: String;
begin
  Result := '';
  PreparedDirectory := '';
  if CompareText(RemoveBackslashUnlessRoot(ExpandConstant('{app}')),
     ExpandConstant('{localappdata}\Programs\PaperPlaneStudioPreview')) <> 0 then begin
    Result := '验证版必须安装到独立的 PaperPlaneStudioPreview 目录。';
    Exit;
  end;
  ExtractTemporaryFile('prepare_desktop_upgrade.ps1');
  ExtractTemporaryFile('upgrade_paths.dll');
  WizardForm.StatusLabel.Caption := '正在关闭旧架构验证版并准备清理旧程序…';
  Command := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\prepare_desktop_upgrade.ps1') + '" -InstallDir "' +
    RemoveBackslashUnlessRoot(ExpandConstant('{app}')) + '" -ExecutableName PaperPlaneStudioPreview';
  if IsWin64 then
    PowerShell := ExpandConstant('{sysnative}\WindowsPowerShell\v1.0\powershell.exe')
  else
    PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  if not ExecAndLogOutput(PowerShell,
    Command, '', SW_HIDE, ewWaitUntilTerminated, ExitCode, nil) then
    Result := '无法启动旧版清理程序，安装尚未开始。请重试。'
  else if ExitCode <> 0 then
    Result := '无法安全关闭或清理旧版，安装已停止。请检查程序占用或安装目录；文章和配置未删除。'
  else
    PreparedDirectory := ExpandConstant('{app}');
end;

function CanCleanRuntime: Boolean;
begin
  Result := (PreparedDirectory <> '') and
    (CompareText(PreparedDirectory, ExpandConstant('{app}')) = 0);
end;

function UpdateReadyMemo(Space, NewLine, MemoUserInfoInfo, MemoDirInfo,
  MemoTypeInfo, MemoComponentsInfo, MemoGroupInfo, MemoTasksInfo: String): String;
begin
  Result := MemoDirInfo + NewLine + NewLine +
    '安装时将自动强制退出旧架构验证版、清理旧程序并安装新版。' + NewLine +
    'M1 文章、素材和验证版设置保留；正式版纸飞机独立保留。请先保存尚未保存的编辑内容。';
end;

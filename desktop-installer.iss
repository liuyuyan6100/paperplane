#define AppName "纸飞机工作台"
#ifndef AppVersion
#define AppVersion "0.1.5"
#endif
#ifndef SourcePath
#define SourcePath "dist\PaperPlaneStudio"
#endif
#ifndef OutputPath
#define OutputPath "dist\installer"
#endif
#ifndef UpgradeHelperPath
#define UpgradeHelperPath "build\upgrade-helper\upgrade_paths.dll"
#endif
#ifdef TestBuild
#define InstallerAppId "{{E3C73A62-0C0B-49FC-A91E-1A1B58C25A66}"
#else
#define InstallerAppId "{{F2F4994B-9DCE-4E1F-B6C6-A578AFC3024E}"
#endif

[Setup]
AppId={#InstallerAppId}
AppName={#AppName}
AppVersion={#AppVersion}
DefaultDirName={localappdata}\Programs\PaperPlaneStudio
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
OutputDir={#OutputPath}
OutputBaseFilename=PaperPlaneStudio-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
SetupIconFile=web\icon.ico
WizardStyle=modern
UninstallDisplayIcon={app}\PaperPlaneStudio.exe
UsePreviousAppDir=yes
CloseApplications=yes
RestartApplications=no
#ifdef TestBuild
Uninstallable=no
CreateUninstallRegKey=no
#endif

[InstallDelete]
; Only the application's owned runtime is replaced. User data is outside {app}.
Type: filesandordirs; Name: "{app}\_internal"; Check: CanCleanRuntime
Type: files; Name: "{app}\PaperPlaneStudio.exe"; Check: CanCleanRuntime

[Files]
Source: "tools\prepare_desktop_upgrade.ps1"; Flags: dontcopy
Source: "{#UpgradeHelperPath}"; DestName: "upgrade_paths.dll"; Flags: dontcopy
Source: "{#SourcePath}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

#ifndef TestBuild
[Icons]
Name: "{group}\{#AppName}"; Filename: "{app}\PaperPlaneStudio.exe"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\PaperPlaneStudio.exe"; Tasks: desktopicon

[Tasks]
Name: desktopicon; Description: "创建桌面快捷方式"; Flags: unchecked

[Run]
Filename: "{app}\PaperPlaneStudio.exe"; Description: "打开{#AppName}"; Flags: nowait postinstall skipifsilent
#endif

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
  ExtractTemporaryFile('prepare_desktop_upgrade.ps1');
  ExtractTemporaryFile('upgrade_paths.dll');
  WizardForm.StatusLabel.Caption := '正在关闭旧版纸飞机并准备清理旧程序…';
  Command := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "' +
    ExpandConstant('{tmp}\prepare_desktop_upgrade.ps1') + '" -InstallDir "' +
    RemoveBackslashUnlessRoot(ExpandConstant('{app}')) + '"';
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
    '安装时将自动强制退出旧版纸飞机、清理旧程序并安装新版。' + NewLine +
    '文章、素材、账号配置和登录数据保留。请先保存尚未保存的编辑内容。';
end;

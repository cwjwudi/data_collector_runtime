#ifndef SourceRoot
  #error SourceRoot must be supplied by build_installer_package.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build_installer_package.ps1
#endif
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

#define AppName "SD SMA Runtime"
#define AppPublisher "SmartData"
#define LauncherRelativePath "_Launcher\SD_SMA_Launcher.exe"

[Setup]
AppId={{C54F5E47-9517-4D29-9E4B-7380996A19C4}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\SmartData\SD SMA
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=SD_SMA_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#LauncherRelativePath}
SetupLogging=yes

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#AppName}"; Filename: "{cmd}"; Parameters: "/c start """" ""http://127.0.0.1:8090/"""
Name: "{autodesktop}\{#AppName}"; Filename: "{cmd}"; Parameters: "/c start """" ""http://127.0.0.1:8090/"""; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[Run]
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\_Service\Initialize-SD_SMA.ps1"""; Flags: runhidden waituntilterminated
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\_Service\Install-SD_SMA-Service.ps1"""; Flags: runhidden waituntilterminated

[UninstallRun]
Filename: "{app}\_Service\SD_SMA_Service.exe"; Parameters: "stop"; WorkingDir: "{app}\_Service"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\_Service\SD_SMA_Service.exe"; Parameters: "uninstall"; WorkingDir: "{app}\_Service"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\_Service\Configure-SD_SMA-Firewall.ps1"" -Mode Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewallRule"

[Code]
var
  PurgeDataConfirmed: Boolean;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ServiceExe: String;
  Command: String;
begin
  Result := '';
  ServiceExe := ExpandConstant('{app}\_Service\SD_SMA_Service.exe');
  if FileExists(ServiceExe) then
    Exec(ServiceExe, 'stop', ExpandConstant('{app}\_Service'), SW_HIDE, ewWaitUntilTerminated, ResultCode);

  Command := '-NoLogo -NoProfile -ExecutionPolicy Bypass -Command "' +
    '$ports=8090,8091,8092,8093,8094; Start-Sleep -Seconds 2; ' +
    '$busy=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$ports -contains $_.LocalPort}); ' +
    'if($busy.Count -gt 0){exit 42}"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := '无法执行端口预检查。'
  else if ResultCode = 42 then
    Result := '端口 8090-8094 仍被旧 Launcher 或其他程序占用。请先停止旧版本后重试。';
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  PurgeDataConfirmed := False;
  if MsgBox('卸载默认保留 ProgramData 中的配置、日志和备份。是否同时删除这些数据？',
    mbConfirmation, MB_YESNO) = IDYES then
    PurgeDataConfirmed := MsgBox('二次确认：永久删除全部配置、日志和备份，且不可恢复。确定继续吗？',
      mbConfirmation, MB_YESNO) = IDYES;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  ResultCode: Integer;
  DataRoot: String;
begin
  if (CurUninstallStep = usPostUninstall) and PurgeDataConfirmed then begin
    DataRoot := ExpandConstant('{commonappdata}\SmartData\SD SMA');
    Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
      '-NoLogo -NoProfile -Command "Remove-Item -LiteralPath ''' + DataRoot + ''' -Recurse -Force"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  end;
end;

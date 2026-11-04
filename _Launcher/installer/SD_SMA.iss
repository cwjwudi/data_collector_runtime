#ifndef SourceRoot
  #error SourceRoot must be supplied by build_installer_package.ps1
#endif
#ifndef OutputDir
  #error OutputDir must be supplied by build_installer_package.ps1
#endif
#ifndef AppVersion
  #define AppVersion "1.0.2"
#endif
#ifndef VCRuntimeVersion
  #error VCRuntimeVersion must be supplied by build_installer_package.ps1
#endif

#define AppName "SD SMA Runtime 托管数据库版"
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
OutputBaseFilename=SD_SMA_ManagedDB_Setup_{#AppVersion}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no
UninstallDisplayIcon={app}\{#LauncherRelativePath}
SetupLogging=yes

[Files]
Source: "{#SourceRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\_Service\Manage-SD_SMA-Database.ps1"; DestName: "database-preflight.ps1"; Flags: dontcopy
Source: "{#SourceRoot}\_Launcher\launcher_config.json"; DestName: "managed-db-config.json"; Flags: dontcopy
Source: "{#SourceRoot}\_Service\VC_redist.x64.exe"; Flags: dontcopy

[Icons]
Name: "{group}\{#AppName}"; Filename: "{cmd}"; Parameters: "/c start """" ""http://127.0.0.1:8090/"""; Tasks: servicemode
Name: "{autodesktop}\{#AppName}"; Filename: "{cmd}"; Parameters: "/c start """" ""http://127.0.0.1:8090/"""; Tasks: desktopicon and servicemode
Name: "{group}\{#AppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; Tasks: manualmode
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\start.bat"; WorkingDir: "{app}"; Tasks: desktopicon and manualmode

[Tasks]
Name: "servicemode"; Description: "注册为 Windows 服务（开机自动运行）"; GroupDescription: "运行方式："; Flags: exclusive
Name: "manualmode"; Description: "手动启动（通过快捷方式启动，需管理员权限）"; GroupDescription: "运行方式："; Flags: exclusive unchecked
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："

[UninstallRun]
Filename: "{app}\_Service\SD_SMA_Service.exe"; Parameters: "stop"; WorkingDir: "{app}\_Service"; Flags: runhidden waituntilterminated; RunOnceId: "StopService"
Filename: "{app}\_Service\SD_SMA_Service.exe"; Parameters: "uninstall"; WorkingDir: "{app}\_Service"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveService"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\_Service\Manage-SD_SMA-Database.ps1"" -Action Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveDatabaseService"
Filename: "{sys}\WindowsPowerShell\v1.0\powershell.exe"; Parameters: "-NoLogo -NoProfile -ExecutionPolicy Bypass -File ""{app}\_Service\Configure-SD_SMA-Firewall.ps1"" -Mode Remove"; Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewallRule"

[Code]
var
  PurgeDataConfirmed: Boolean;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
  ServiceExe: String;
  Command: String;
  InstalledVersion: String;
  Installed: Cardinal;
  Version: Int64;
  RequiredVersion: Int64;
begin
  Result := '';
  if not StrToVersion('{#VCRuntimeVersion}', RequiredVersion) then
    RaiseException('无效的 Windows 运行库版本。');
  if not (RegQueryDWordValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Installed', Installed) and
    (Installed = 1) and
    RegQueryStringValue(HKLM64, 'SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64', 'Version', InstalledVersion) and
    StrToVersion(Copy(InstalledVersion, 2, Length(InstalledVersion)), Version) and
    (ComparePackedVersion(Version, RequiredVersion) >= 0)) then begin
    ExtractTemporaryFile('VC_redist.x64.exe');
    if not Exec(ExpandConstant('{tmp}\VC_redist.x64.exe'), '/install /quiet /norestart', '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '无法启动离线 Windows 运行库安装。'
    else if ResultCode = 3010 then begin
      NeedsRestart := True;
      Result := 'Windows 运行库需要重启，请重启计算机后重新运行本安装程序。';
    end
    else if ResultCode <> 0 then
      Result := 'Windows 运行库安装失败，错误码：' + IntToStr(ResultCode);
    if Result <> '' then Exit;
  end;
  ExtractTemporaryFile('database-preflight.ps1');
  ExtractTemporaryFile('managed-db-config.json');
  Command := '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{tmp}\database-preflight.ps1') +
    '" -Action Preflight -ProgramRoot "' + ExpandConstant('{app}') + '" -ConfigPath "' + ExpandConstant('{tmp}\managed-db-config.json') + '"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := '无法执行托管数据库预检查。'
  else if ResultCode <> 0 then
    Result := '托管数据库预检查失败：请检查数据库端口占用、旧服务是否已停止，以及是否存在标准版或其他安装目录。';
  if Result <> '' then Exit;
  ServiceExe := ExpandConstant('{app}\_Service\SD_SMA_Service.exe');
  if FileExists(ServiceExe) then
    Exec(ServiceExe, 'stop', ExpandConstant('{app}\_Service'), SW_HIDE, ewWaitUntilTerminated, ResultCode);

  Command := '-NoLogo -NoProfile -ExecutionPolicy Bypass -Command "' +
    '$ports=8090,8091,8092,8093,8094; Start-Sleep -Seconds 2; ' +
    '$busy=@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$ports -contains $_.LocalPort}); ' +
    'if($busy.Count -gt 0){exit 42}"';
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    Result := '无法执行端口预检查。'
  else if ResultCode <> 0 then
    Result := '端口 8090-8094 仍被旧 Launcher 或其他程序占用。请先停止旧版本后重试。';

  if (Result = '') and FileExists(ServiceExe) and WizardIsTaskSelected('manualmode') then begin
    if not Exec(ServiceExe, 'uninstall', ExpandConstant('{app}\_Service'), SW_HIDE, ewWaitUntilTerminated, ResultCode) then
      Result := '无法移除旧 Windows 服务。'
    else if ResultCode <> 0 then begin
      // WinSW also returns nonzero when no service is installed.
      Command := '-NoLogo -NoProfile -Command "if (Get-Service -Name SD_SMA -ErrorAction SilentlyContinue) {exit 1}"';
      if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'), Command, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
        Result := '无法检查旧 Windows 服务。'
      else if ResultCode <> 0 then
        Result := '旧 Windows 服务仍存在，无法切换为手动启动。';
    end;
  end;
end;

procedure RunRequiredScript(Name: String; Arguments: String);
var
  ResultCode: Integer;
begin
  if not Exec(ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe'),
    '-NoLogo -NoProfile -ExecutionPolicy Bypass -File "' + ExpandConstant('{app}\_Service\') + Name + '" ' + Arguments,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode) then
    RaiseException('无法执行安装步骤：' + Name);
  if ResultCode <> 0 then
    RaiseException('安装步骤失败：' + Name + '。现有数据库和凭据已保留，请排除问题后重新运行安装程序。');
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then begin
    RunRequiredScript('Initialize-SD_SMA.ps1', '');
    RunRequiredScript('Manage-SD_SMA-Database.ps1', '-Action Initialize');
    if WizardIsTaskSelected('servicemode') then
      RunRequiredScript('Install-SD_SMA-Service.ps1', '');
  end;
end;

function InitializeUninstall(): Boolean;
begin
  Result := True;
  PurgeDataConfirmed := False;
  if UninstallSilent then Exit;
  if MsgBox('卸载默认保留 ProgramData 中的数据库、凭据、配置、日志和备份。是否同时删除这些数据？',
    mbConfirmation, MB_YESNO) = IDYES then
    PurgeDataConfirmed := MsgBox('二次确认：永久删除全部数据库、凭据、配置、日志和备份，且不可恢复。确定继续吗？',
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

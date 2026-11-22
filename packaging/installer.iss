[Setup]
AppId={{8D6E4A7F-3B2C-4F18-9E21-7C1D42A50B01}
AppName=WattWatch 電量偵測管家
AppVersion=1.0.0
AppPublisher=WattWatch
DefaultDirName={autopf}\WattWatch
DefaultGroupName=WattWatch 電量偵測管家
AllowNoIcons=yes
OutputDir=..\release
OutputBaseFilename=WattWatch_Setup_v1.0
SetupIconFile=wattwatch.ico
UninstallDisplayIcon={app}\WattWatch.exe
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern

[Languages]
Name: "default"; MessagesFile: "compiler:Default.isl"

[Messages]
SetupAppTitle=WattWatch 電量偵測管家 安裝程式
SetupWindowTitle=安裝 - WattWatch 電量偵測管家
WelcomeLabel1=歡迎安裝 [name]
WelcomeLabel2=此安裝程式將自動為您的電腦安裝 [name]（含內建執行環境與 PawnIO 硬體感測驅動），安裝完成即可直接使用，無需手動設定。
FinishedHeadingLabel=已完成安裝 [name]
FinishedLabel=WattWatch 電量偵測管家 已安裝完成，您現在可以立即啟動監控。

[Tasks]
Name: "desktopicon"; Description: "建立桌面捷徑 (WattWatch 電量偵測管家)"; GroupDescription: "附加選項："
Name: "autostart"; Description: "開機時自動於背景靜默啟動監控（無跳窗、低耗電）"; GroupDescription: "附加選項："

[Files]
Source: "..\dist\WattWatch\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "redist\PawnIO_setup.exe"; DestDir: "{tmp}"; Flags: deleteafterinstall

[Icons]
Name: "{group}\WattWatch 電量偵測管家"; Filename: "{app}\WattWatch.exe"
Name: "{group}\解除安裝 WattWatch"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WattWatch 電量偵測管家"; Filename: "{app}\WattWatch.exe"; Tasks: desktopicon

[Run]
Filename: "{tmp}\PawnIO_setup.exe"; Parameters: "-install -silent"; StatusMsg: "正在安裝 PawnIO 硬體感測驅動程式..."; Flags: runhidden waituntilterminated; Check: NeedsPawnIO
Filename: "schtasks.exe"; Parameters: "/Create /TN ""WattWatch"" /SC ONLOGON /RL HIGHEST /F /TR ""\""{app}\WattWatch.exe\"" --no-browser"""; StatusMsg: "正在設定開機自動背景啟動..."; Flags: runhidden waituntilterminated; Tasks: autostart
Filename: "{app}\WattWatch.exe"; Description: "立即啟動 WattWatch 電量偵測管家"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "taskkill.exe"; Parameters: "/F /IM WattWatch.exe /T"; Flags: runhidden; RunOnceId: "KillWattWatch"
Filename: "taskkill.exe"; Parameters: "/F /IM LhmBridge.exe /T"; Flags: runhidden; RunOnceId: "KillLhmBridge"
Filename: "schtasks.exe"; Parameters: "/Delete /TN ""WattWatch"" /F"; Flags: runhidden; RunOnceId: "DelSchTask"

[Code]
function NeedsPawnIO(): Boolean;
begin
  Result := not RegKeyExists(HKLM, 'SYSTEM\CurrentControlSet\Services\PawnIO');
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec('taskkill.exe', '/F /IM WattWatch.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Exec('taskkill.exe', '/F /IM LhmBridge.exe /T', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  Sleep(400);
  Result := '';
end;

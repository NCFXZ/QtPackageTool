; SPDX-License-Identifier: GPL-3.0-or-later
; Version, names and source directories are supplied by build_windows.py.

#ifndef AppVersion
  #error Use packaging/build_windows.py to supply release metadata.
#endif

[Setup]
AppId={{6A810F1B-57CE-45D3-9679-1DD9951F3069}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=NCFXZ
AppPublisherURL=https://github.com/{#Repository}
AppSupportURL=https://github.com/{#Repository}/issues
AppUpdatesURL=https://github.com/{#Repository}/releases
VersionInfoVersion={#AppVersion}.0
DefaultDirName={localappdata}\Programs\QtPackageTool
DefaultGroupName={#AppName}
PrivilegesRequired=lowest
#if VER >= EncodeVer(7, 0, 0)
SetupArchitecture=x64
#endif
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
AppMutex={#ApplicationMutex}
SetupMutex=QtPackageTool.Setup
CloseApplications=no
RestartApplications=no
DisableProgramGroupPage=yes
DisableStartupPrompt=yes
UninstallDisplayIcon={app}\QtPackageTool.exe
WizardStyle=modern
SetupIconFile=..\resource\images\logo.ico
LicenseFile=..\LICENSE
Compression=lzma2
SolidCompression=yes
OutputDir={#OutputDir}
OutputBaseFilename={#InstallerBaseName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
#if FileExists(AddBackslash(CompilerPath) + "Languages\ChineseSimplified.isl")
Name: "chinesesimp"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
#endif

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

#ifdef DeltaUpdate
#include DeltaEntries
#else
#include PayloadFilesInclude

#endif

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\QtPackageTool.exe"; AppUserModelID: "{#AppUserModelID}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\QtPackageTool.exe"; Tasks: desktopicon; AppUserModelID: "{#AppUserModelID}"

[Run]
Filename: "{app}\QtPackageTool.exe"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent; Check: not RestartRequested
Filename: "{app}\QtPackageTool.exe"; Flags: nowait; Check: RestartRequested

[Code]
#include ManagedPathsInclude
#include "managed_paths.iss"

const
  SynchronizeAccess = $00100000;
  WaitSignaled = 0;
  WaitTimeout = 258;

function OpenProcess(DesiredAccess: LongWord; InheritHandle: Boolean; ProcessId: LongWord): THandle;
  external 'OpenProcess@kernel32.dll stdcall';
function WaitForSingleObject(Handle: THandle; Milliseconds: LongWord): LongWord;
  external 'WaitForSingleObject@kernel32.dll stdcall';
function CloseHandle(Handle: THandle): Boolean;
  external 'CloseHandle@kernel32.dll stdcall';

function RestartRequested: Boolean;
begin
  Result := ExpandConstant('{param:RESTARTAPP|0}') = '1';
end;

procedure WaitForPreviousProcess;
var
  RawPid: String;
  Pid, Attempt: Integer;
  ProcessHandle: THandle;
  WaitResult: LongWord;
begin
  RawPid := ExpandConstant('{param:WAITPID|}');
  if RawPid = '' then Exit;
  Pid := StrToIntDef(RawPid, -1);
  if Pid <= 0 then
    RaiseException('Invalid /WAITPID parameter.');
  ProcessHandle := OpenProcess(SynchronizeAccess, False, Pid);
  if ProcessHandle = 0 then
  begin
    if DLLGetLastError = 87 then Exit;  { The process already exited. }
    RaiseException('Could not wait for the previous application process. Close it and try again.');
  end;
  try
    Log(Format('Waiting for application process %d to exit.', [Pid]));
    for Attempt := 1 to 120 do
    begin
      WaitResult := WaitForSingleObject(ProcessHandle, 1000);
      if WaitResult = WaitSignaled then Exit;
      if WaitResult <> WaitTimeout then
        RaiseException('Could not confirm that the previous application exited.');
    end;
    RaiseException('The application is still running. Close it and run the installer again.');
  finally
    CloseHandle(ProcessHandle);
  end;
end;

function InitializeSetup: Boolean;
#ifdef DeltaUpdate
var
  AppDirectory, ManifestPath: String;
#endif
begin
  Result := False;
  WaitForPreviousProcess;
#ifdef DeltaUpdate
  { The helper supplies /DIR explicitly before the app constant is available. }
  AppDirectory := ExpandConstant('{param:DIR|}');
  if (Length(AppDirectory) < 3) or (Copy(AppDirectory, 2, 2) <> ':\') then
    RaiseException('An absolute /DIR is required for the incremental update.');
  ManifestPath := AddBackslash(AppDirectory) + '{#FileManifestName}';
  if not FileExists(ManifestPath) then
    RaiseException('The installed baseline manifest is missing. Use the full installer.');
  if Lowercase(GetSHA256OfFile(ManifestPath)) <> '{#BaseManifestSHA}' then
    RaiseException('The installed baseline differs from this incremental update. Use the full installer.');
#endif
  Result := True;
end;

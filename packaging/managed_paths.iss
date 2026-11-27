// SPDX-License-Identifier: GPL-3.0-or-later
// Only the canonical manifest emitted by delta.encode_manifest establishes ownership.

var
  PreviousManaged, NextManaged: TStringList;

function ManagedAttributes(const Name: String): LongWord;
  external 'GetFileAttributesW@kernel32.dll stdcall';

function TakeLiteral(const Text, Expected: String; var Offset: Integer): Boolean;
begin
  Result := Copy(Text, Offset, Length(Expected)) = Expected;
  if Result then Offset := Offset + Length(Expected);
end;

function ValidManagedPath(const Path: String): Boolean;
var
  I, Start, Dot: Integer;
  Part, Base: String;
begin
  Result := False;
  if (Length(Path) = 0) or (Length(Path) > 240) then Exit;
  if Pos('{' + '#', Path) > 0 then Exit;
  Start := 1;
  for I := 1 to Length(Path) + 1 do
  begin
    if I <= Length(Path) then
      if (Ord(Path[I]) < 32) or (Pos(Path[I], '\<>:"|?*') > 0) then Exit;
    if (I > Length(Path)) or (Path[I] = '/') then
    begin
      Part := Copy(Path, Start, I - Start);
      if (Part = '') or (Part = '.') or (Part = '..') then Exit;
      if (Part[Length(Part)] = '.') or (Part[Length(Part)] = ' ') then Exit;
      Dot := Pos('.', Part);
      if Dot = 0 then Base := Part else Base := Copy(Part, 1, Dot - 1);
      Base := Uppercase(Trim(Base));
      if (Base = 'CON') or (Base = 'PRN') or (Base = 'AUX') or (Base = 'NUL') or
         (Base = 'CONIN$') or (Base = 'CONOUT$') then Exit;
      if ((Copy(Base, 1, 3) = 'COM') or (Copy(Base, 1, 3) = 'LPT')) and
         (Length(Base) = 4) and (Pos(Base[4], '123456789' + #$00B9 + #$00B2 + #$00B3) > 0) then Exit;
      Start := I + 1;
    end;
  end;
  Result := True;
end;

function GeneratedUninstaller(const Path: String): Boolean;
var
  Name, Extension: String;
  I, Dot: Integer;
begin
  Result := False;
  if Pos('/', Path) > 0 then Exit;
  Name := Lowercase(Path);
  if Copy(Name, 1, 5) <> 'unins' then Exit;
  Dot := Pos('.', Name);
  if Dot < 7 then Exit;
  for I := 6 to Dot - 1 do if (Name[I] < '0') or (Name[I] > '9') then Exit;
  Extension := Copy(Name, Dot + 1, Length(Name));
  Result := (Extension = 'exe') or (Extension = 'dat') or (Extension = 'msg');
end;

function ReadCanonicalString(const Text: String; var Offset: Integer; var Value: String): Boolean;
var
  Start: Integer;
begin
  Result := False;
  if not TakeLiteral(Text, '"', Offset) then Exit;
  Start := Offset;
  while (Offset <= Length(Text)) and (Text[Offset] <> '"') do
  begin
    if (Ord(Text[Offset]) < 32) or (Text[Offset] = '\') then Exit;
    Offset := Offset + 1;
  end;
  if Offset > Length(Text) then Exit;
  Value := Copy(Text, Start, Offset - Start);
  Offset := Offset + 1;
  Result := True;
end;

function ReadCanonicalNumber(const Text: String; var Offset: Integer; var Value: Int64): Boolean;
var
  Start: Integer;
  Number: String;
begin
  Result := False;
  Start := Offset;
  while (Offset <= Length(Text)) and (Text[Offset] >= '0') and (Text[Offset] <= '9') do Offset := Offset + 1;
  Number := Copy(Text, Start, Offset - Start);
  if (Number = '') or (Length(Number) > 10) then Exit;
  if (Length(Number) > 1) and (Number[1] = '0') then Exit;
  Value := StrToInt64Def(Number, -1);
  Result := (Value >= 0) and (Value <= 2147483648);
end;

function ValidManagedVersion(const Version: String): Boolean;
var
  I, Start, Count, Number: Integer;
  Part: String;
begin
  Result := False;
  Start := 1;
  Count := 0;
  for I := 1 to Length(Version) + 1 do
  begin
    if I <= Length(Version) then
      if (Version[I] <> '.') and ((Version[I] < '0') or (Version[I] > '9')) then Exit;
    if (I > Length(Version)) or (Version[I] = '.') then
    begin
      Part := Copy(Version, Start, I - Start);
      if (Part = '') or ((Length(Part) > 1) and (Part[1] = '0')) then Exit;
      Number := StrToIntDef(Part, -1);
      if (Number < 0) or (Number > 65535) then Exit;
      Count := Count + 1;
      Start := I + 1;
    end;
  end;
  Result := Count = 3;
end;

function LoadPreviousManaged(Paths: TStringList): Boolean;
var
  Raw: AnsiString;
  Text, Name, Version, Hash, Path, Parent: String;
  Offset, I, Size, Slash: Integer;
  FileBytes, Total: Int64;
begin
  Result := False;
  Paths.Clear;
  Name := AddBackslash(ExpandConstant('{app}')) + '{#FileManifestName}';
  if not FileExists(Name) then
  begin
    Result := not DirExists(Name);
    Exit;
  end;
  if not FileSize(Name, Size) then Exit;
  if (Size < 1) or (Size > 1048576) then Exit;
  if not LoadStringFromFile(Name, Raw) then Exit;
  Text := UTF8Decode(Raw);
  if UTF8Encode(Text) <> Raw then Exit;
  Offset := 1;
  if not TakeLiteral(Text, '{"schema":1,"version":', Offset) then Exit;
  if not ReadCanonicalString(Text, Offset, Version) then Exit;
  if not ValidManagedVersion(Version) then Exit;
  if not TakeLiteral(Text, ',"files":[', Offset) then Exit;
  Total := 0;
  repeat
    if not TakeLiteral(Text, '{"path":', Offset) then Exit;
    if not ReadCanonicalString(Text, Offset, Path) then Exit;
    if not ValidManagedPath(Path) or (Paths.IndexOf(Path) >= 0) then Exit;
    if GeneratedUninstaller(Path) then Exit;
    if CompareText(Path, '{#FileManifestName}') = 0 then Exit;
    Parent := Path;
    repeat
      Slash := Length(Parent);
      while (Slash > 0) and (Parent[Slash] <> '/') do Slash := Slash - 1;
      if Slash = 0 then Break;
      Parent := Copy(Parent, 1, Slash - 1);
      if Paths.IndexOf(Parent) >= 0 then Exit;
    until False;
    for I := 0 to Paths.Count - 1 do
      if CompareText(Copy(Paths[I], 1, Length(Path) + 1), Path + '/') = 0 then Exit;
    if not TakeLiteral(Text, ',"size":', Offset) then Exit;
    if not ReadCanonicalNumber(Text, Offset, FileBytes) then Exit;
    Total := Total + FileBytes;
    if Total > 4294967296 then Exit;
    if not TakeLiteral(Text, ',"sha256":', Offset) then Exit;
    if not ReadCanonicalString(Text, Offset, Hash) then Exit;
    if Length(Hash) <> 64 then Exit;
    for I := 1 to Length(Hash) do if Pos(Hash[I], '0123456789abcdef') = 0 then Exit;
    if not TakeLiteral(Text, '}', Offset) then Exit;
    Paths.Add(Path);
    if Paths.Count > 10000 then Exit;
    if not TakeLiteral(Text, ',', Offset) then Break;
  until False;
  if not TakeLiteral(Text, ']}' + #10, Offset) then Exit;
  if Offset <> Length(Text) + 1 then Exit;
  Paths.Add('{#FileManifestName}');
  Result := True;
end;

function ManagedNativePath(const Relative: String): String;
var
  Local: String;
begin
  Local := Relative;
  StringChangeEx(Local, '/', '\', True);
  Result := AddBackslash(ExpandConstant('{app}')) + Local;
end;

function ManagedCollision(const Relative: String; Owned: TStringList; IsTarget: Boolean): Boolean;
var
  Name, Parent: String;
  Attributes: LongWord;
begin
  Result := True;
  Name := ManagedNativePath(Relative);
  Parent := Name;
  while Length(Parent) >= Length(ExpandConstant('{app}')) do
  begin
    Attributes := ManagedAttributes(Parent);
    if Attributes = $FFFFFFFF then
      if (DLLGetLastError <> 2) and (DLLGetLastError <> 3) then Exit;
    if (Attributes <> $FFFFFFFF) and ((Attributes and $400) <> 0) then Exit;
    if CompareText(Parent, Name) <> 0 then
      if FileExists(Parent) then Exit;
    if CompareText(Parent, ExpandConstant('{app}')) = 0 then Break;
    Parent := ExtractFileDir(Parent);
  end;
  if DirExists(Name) then Exit;
  if IsTarget and FileExists(Name) and (Owned.IndexOf(Relative) < 0) then Exit;
  Result := False;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  I: Integer;
begin
  Result := '';
  if PreviousManaged = nil then PreviousManaged := TStringList.Create;
  if NextManaged = nil then NextManaged := TStringList.Create;
  PreviousManaged.Clear;
  NextManaged.Clear;
  FillTargetManaged(NextManaged);
  if not LoadPreviousManaged(PreviousManaged) then
  begin
    Result := 'The existing file manifest is invalid. Choose a new installation folder to preserve its files.';
    Exit;
  end;
  for I := 0 to NextManaged.Count - 1 do
    if ManagedCollision(NextManaged[I], PreviousManaged, True) then
    begin
      Result := 'Installation would replace an unowned file or directory. Preserve or move it first: ' + NextManaged[I];
      Exit;
    end;
  for I := 0 to PreviousManaged.Count - 1 do
    if ManagedCollision(PreviousManaged[I], PreviousManaged, False) then
    begin
      Result := 'An existing managed path became a directory or reparse point. Preserve or move it first: ' + PreviousManaged[I];
      Exit;
    end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  Name: String;
begin
  if CurStep <> ssPostInstall then Exit;
  if PreviousManaged = nil then Exit;
  for I := 0 to PreviousManaged.Count - 1 do
    if NextManaged.IndexOf(PreviousManaged[I]) < 0 then
    begin
      Name := ManagedNativePath(PreviousManaged[I]);
      if FileExists(Name) then
      begin
        if ManagedCollision(PreviousManaged[I], PreviousManaged, False) then
          RaiseException('An obsolete managed path changed during installation: ' + PreviousManaged[I]);
        Log('Removing obsolete managed file: ' + Name);
        if not DeleteFile(Name) then RaiseException('Could not remove obsolete managed file: ' + PreviousManaged[I]);
      end;
    end;
end;

procedure DeinitializeSetup;
begin
  if PreviousManaged <> nil then PreviousManaged.Free;
  if NextManaged <> nil then NextManaged.Free;
end;

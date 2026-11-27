// SPDX-License-Identifier: GPL-3.0-or-later
// A copied, independent .NET Framework worker. It never terminates application processes.

using System;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.IO.Compression;
using System.Linq;
using System.Net;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Win32;
using Microsoft.Win32.SafeHandles;

internal static class Program
{
    const string AppMutex = "QtPackageTool.Application";
    const string WorkerMutex = "QtPackageTool.UpdateWorker";
    const string ManifestName = "QtPackageTool-files.json";
    const string InstallerName = "QtPackageTool-Setup-x64.exe";
    const string UninstallKey = @"Software\Microsoft\Windows\CurrentVersion\Uninstall\{6A810F1B-57CE-45D3-9679-1DD9951F3069}_is1";
    const long MaxFile = 2L * 1024 * 1024 * 1024;
    const long MaxTotal = 4L * 1024 * 1024 * 1024;
    const int MaxJson = 1024 * 1024;
    static readonly UTF8Encoding Utf8 = new UTF8Encoding(false, true);
    static readonly Regex VersionPattern = new Regex(@"\A(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\z", RegexOptions.CultureInvariant);
    static readonly Regex HashPattern = new Regex(@"\A[a-f0-9]{64}\z", RegexOptions.CultureInvariant);
    static readonly Regex UninstallerPattern = new Regex(@"\Aunins[0-9]{3}\.(exe|dat|msg)\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant);
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = MaxJson, RecursionLimit = 32 };
    static readonly Dictionary<string, string> Messages = new Dictionary<string, string> {
        { "title", "Qt Package Tool Update" },
        { "failure", "The update could not be completed. Download the full installer and try again." },
        { "conflict", "The update was stopped to protect existing files in the installation folder. Move the conflicting files or folders elsewhere, then check for updates again." },
        { "restart_failure", "The update was installed, but the application could not be started. Start Qt Package Tool manually." },
        { "diagnostics", "Diagnostics" }
    };
    static string WorkDir;
    static string AppDir;
    static string BaseVersion;
    static string TargetVersion;
    static string DeltaPath;
    static string FullUrl;
    static long DeltaSize;
    static long FullSize;
    static string DeltaHash;
    static string FullHash;
    static string FileManifestUrl;
    static long FileManifestSize;
    static string FileManifestHash;
    static bool FallbackUsed;
    static string Mode = "delta";
    static Backup BackupState;
    static Manifest TargetManifest;
    static readonly HashSet<string> TargetPaths = new HashSet<string>(StringComparer.OrdinalIgnoreCase);

    sealed class UserFileConflictException : IOException
    {
        internal UserFileConflictException(string path) : base("An existing file or folder conflicts with the update: " + path) { }
    }

    sealed class FileEntry
    {
        internal string Path;
        internal long Size;
        internal string Hash;
    }

    sealed class Manifest
    {
        internal string Version;
        internal string Hash;
        internal readonly Dictionary<string, FileEntry> Files = new Dictionary<string, FileEntry>(StringComparer.OrdinalIgnoreCase);
    }

    sealed class Backup
    {
        internal string Directory;
        internal Manifest Baseline;
        internal readonly List<string> Paths = new List<string>();
        internal object Registration;
        internal bool Complete;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct AttributeTagInfo
    {
        internal uint Attributes;
        internal uint Tag;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafeFileHandle CreateFile(string name, uint access, uint share, IntPtr security, uint creation, uint flags, IntPtr template);

    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetFileInformationByHandleEx(SafeFileHandle handle, int infoClass, out AttributeTagInfo info, uint size);

    [STAThread]
    static int Main(string[] args)
    {
        Mutex worker = null;
        bool owned = false;
        bool success = false;
        bool conflict = false;
        string failure = null;
        try
        {
            if (args.Length != 2 || args[0] != "--plan")
                throw new InvalidDataException("Expected --plan and an absolute update plan path.");
            LoadPlan(args[1]);
            bool created;
            worker = new Mutex(true, WorkerMutex, out created);
            if (!created)
            {
                worker.Dispose();
                worker = null;
                return 2;
            }
            owned = true;
            if (File.Exists(System.IO.Path.Combine(WorkDir, "helper-state.json")))
                throw new InvalidOperationException("This update attempt has already run. Check for updates again or use the full installer.");
            Stage("waiting");
            WaitForApplications(IntValue(ReadObject(args[1]), "wait_pid", 1, Int32.MaxValue));
            try
            {
                ApplyDelta();
                success = true;
            }
            catch (UserFileConflictException) { throw; }
            catch (Exception deltaFailure)
            {
                Log("delta_failed", deltaFailure.Message);
                if (BackupState != null && BackupState.Complete)
                {
                    try { RestoreBackup(); }
                    catch (Exception rollbackFailure) { Log("rollback_failed_before_full", rollbackFailure.Message); }
                }
                else if (BackupState == null)
                {
                    // A structurally invalid ZIP can still fall back with a verified local checkpoint.
                    try
                    {
                        Manifest baseline = ReadManifest(System.IO.Path.Combine(AppDir, ManifestName));
                        if (baseline.Version != BaseVersion)
                            throw new InvalidDataException("The installed baseline version does not match.");
                        VerifyFiles(baseline);
                        CreateBackup(baseline);
                    }
                    catch (Exception backupFailure)
                    {
                        Log("fallback_checkpoint_unavailable", backupFailure.Message);
                    }
                }
                FallbackUsed = true;
                Mode = "full";
                AtomicJson(System.IO.Path.Combine(System.IO.Path.GetDirectoryName(WorkDir), "full-only.json"), new Dictionary<string, object> {
                    { "schema", 1 }, { "base_version", BaseVersion }, { "target_version", TargetVersion }, { "full_sha256", FullHash }
                });
                Stage("full_fallback");
                try
                {
                    Manifest expectedTarget = ObtainTargetManifest();
                    PreflightTarget(expectedTarget);
                    string full = ObtainFullInstaller();
                    PreflightTarget(expectedTarget);
                    RunInstaller(full, "full");
                    Manifest installed = ReadManifest(System.IO.Path.Combine(AppDir, ManifestName));
                    if (installed.Version != TargetVersion)
                        throw new InvalidDataException("The full installer has the wrong target version.");
                    if (BackupState != null && BackupState.Complete)
                        foreach (string previous in BackupState.Baseline.Files.Keys)
                            if (!installed.Files.ContainsKey(previous))
                                DeleteKnownFile(AppPath(previous));
                    VerifyTarget(expectedTarget);
                    success = true;
                }
                catch (UserFileConflictException) { throw; }
                catch (Exception fullFailure)
                {
                    Log("full_failed", fullFailure.Message);
                    if (BackupState != null && BackupState.Complete)
                    {
                        try { RestoreBackup(); }
                        catch (Exception rollbackFailure) { Log("rollback_failed_after_full", rollbackFailure.Message); }
                    }
                    throw new InvalidOperationException("The full update also failed. " + fullFailure.Message, fullFailure);
                }
            }
            Stage("completed");
            Result(true, null);
            CleanupSuccessfulBackup();
        }
        catch (Exception error)
        {
            conflict = error is UserFileConflictException;
            failure = error.Message;
            Log("failed", failure);
            if (WorkDir != null)
            {
                try { Stage("failed"); Result(false, failure); }
                catch (Exception logFailure) { Log("result_write_failed", logFailure.Message); }
            }
        }
        finally
        {
            if (owned)
                worker.ReleaseMutex();
            if (worker != null)
                worker.Dispose();
        }
        if (success)
        {
            try
            {
                Process.Start(new ProcessStartInfo(System.IO.Path.Combine(AppDir, "QtPackageTool.exe")) {
                    UseShellExecute = false, WorkingDirectory = AppDir
                });
                Log("application_started", TargetVersion);
                return 0;
            }
            catch (Exception restartFailure)
            {
                Log("application_start_failed", restartFailure.Message);
                MessageBox.Show(Messages["restart_failure"] + "\n\n" + restartFailure.Message,
                    Messages["title"], MessageBoxButtons.OK, MessageBoxIcon.Error);
                return 1;
            }
        }
        MessageBox.Show(Messages[conflict ? "conflict" : "failure"] + "\n\n" + failure +
            (conflict ? "" : "\n\n" + (FullUrl ?? "https://github.com/NCFXZ/QtPackageTool/releases/latest")) +
            (WorkDir == null ? "" : "\n\n" + Messages["diagnostics"] + ": " + WorkDir),
            Messages["title"], MessageBoxButtons.OK, MessageBoxIcon.Error);
        return 1;
    }

    static void LoadPlan(string path)
    {
        path = Absolute(path);
        WorkDir = System.IO.Path.GetDirectoryName(path);
        AssertNoLinks(path);
        Dictionary<string, object> plan = ReadObject(path);
        RequireSchema(plan);
        object messages;
        if (plan.TryGetValue("messages", out messages))
        {
            Dictionary<string, object> translated = AsObject(messages);
            foreach (string key in Messages.Keys.ToArray())
                if (translated.ContainsKey(key))
                {
                    string text = StringValue(translated, key);
                    if (text.Length > 2000)
                        throw new InvalidDataException("An update message is too long.");
                    Messages[key] = text;
                }
        }
        AppDir = Absolute(StringValue(plan, "app_dir"));
        if (AppDir == System.IO.Path.GetPathRoot(AppDir) || IsWithin(WorkDir, AppDir) || IsWithin(AppDir, WorkDir))
            throw new InvalidDataException("The worker and installed application require separate directories.");
        string self = System.Reflection.Assembly.GetExecutingAssembly().Location;
        if (!IsWithin(self, WorkDir) || IsWithin(self, AppDir))
            throw new InvalidDataException("The updater must run from its download work directory.");
        if (!Directory.Exists(AppDir))
            throw new DirectoryNotFoundException("The installed application directory does not exist.");
        AssertNoLinks(AppDir);
        BaseVersion = StringValue(plan, "base_version");
        TargetVersion = StringValue(plan, "target_version");
        if (VersionParts(BaseVersion).CompareTo(VersionParts(TargetVersion)) >= 0)
            throw new InvalidDataException("The update must target a newer stable version.");
        int pid = IntValue(plan, "wait_pid", 1, Int32.MaxValue);
        if (pid == Process.GetCurrentProcess().Id)
            throw new InvalidDataException("The updater cannot wait for itself.");
        Dictionary<string, object> delta = ObjectValue(plan, "delta");
        DeltaPath = Absolute(StringValue(delta, "path"));
        if (!String.Equals(System.IO.Path.GetDirectoryName(DeltaPath), WorkDir, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("The update ZIP must be in the worker directory.");
        DeltaSize = LongValue(delta, "size", 1, MaxFile);
        DeltaHash = HashValue(delta, "sha256");
        Dictionary<string, object> full = ObjectValue(plan, "full");
        FullUrl = StringValue(full, "url");
        string expected = "https://github.com/NCFXZ/QtPackageTool/releases/download/v" + TargetVersion + "/" + InstallerName;
        if (FullUrl != expected || !AllowedDownload(new Uri(FullUrl), FullUrl))
            throw new InvalidDataException("The full installer URL is not the official release asset.");
        FullSize = LongValue(full, "size", 1, MaxFile);
        FullHash = HashValue(full, "sha256");
        Dictionary<string, object> manifest = ObjectValue(plan, "manifest");
        FileManifestUrl = StringValue(manifest, "url");
        string expectedManifest = "https://github.com/NCFXZ/QtPackageTool/releases/download/v" + TargetVersion + "/" + ManifestName;
        if (FileManifestUrl != expectedManifest || !AllowedDownload(new Uri(FileManifestUrl), FileManifestUrl))
            throw new InvalidDataException("The target manifest URL is not the official release asset.");
        FileManifestSize = LongValue(manifest, "size", 1, MaxJson);
        FileManifestHash = HashValue(manifest, "sha256");
    }

    static void WaitForApplications(int pid)
    {
        Stopwatch watch = Stopwatch.StartNew();
        try
        {
            using (Process previous = Process.GetProcessById(pid))
                if (!previous.WaitForExit(120000))
                    throw new TimeoutException("The application is still running. No files were changed.");
        }
        catch (ArgumentException) { }
        while (watch.ElapsedMilliseconds < 120000)
        {
            try
            {
                using (Mutex running = Mutex.OpenExisting(AppMutex)) { }
            }
            catch (WaitHandleCannotBeOpenedException) { Stage("applications_exited"); return; }
            Thread.Sleep(200);
        }
        throw new TimeoutException("Other Qt Package Tool instances are still running. No files were changed.");
    }

    static void ApplyDelta()
    {
        VerifyAsset(DeltaPath, DeltaSize, DeltaHash);
        string extracted = System.IO.Path.Combine(WorkDir, "delta-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(extracted);
        HashSet<string> names = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        using (FileStream stream = new FileStream(DeltaPath, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (ZipArchive archive = new ZipArchive(stream, ZipArchiveMode.Read))
        {
            if (archive.Entries.Count != 3)
                throw new InvalidDataException("The update ZIP must contain exactly three fixed files.");
            foreach (ZipArchiveEntry entry in archive.Entries)
            {
                if ((entry.FullName != "delta.json" && entry.FullName != "files.json" && entry.FullName != "installer.exe") || !names.Add(entry.FullName))
                    throw new InvalidDataException("The update ZIP contains an unexpected or duplicate path.");
                int type = (entry.ExternalAttributes >> 16) & 0xF000;
                if ((type != 0 && type != 0x8000) || (entry.ExternalAttributes & (int)FileAttributes.ReparsePoint) != 0)
                    throw new InvalidDataException("The update ZIP contains a linked or non-regular entry.");
                long limit = entry.FullName == "installer.exe" ? MaxFile : MaxJson;
                if (entry.Length <= 0 || entry.Length > limit)
                    throw new InvalidDataException("The update ZIP contains an invalid entry size.");
                string destination = System.IO.Path.Combine(extracted, entry.FullName);
                using (Stream input = entry.Open())
                using (FileStream output = new FileStream(destination, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    CopyBounded(input, output, entry.Length);
            }
        }
        Dictionary<string, object> metadata = ReadObject(System.IO.Path.Combine(extracted, "delta.json"));
        RequireSchema(metadata);
        if (StringValue(metadata, "base_version") != BaseVersion || StringValue(metadata, "target_version") != TargetVersion)
            throw new InvalidDataException("The delta versions do not match the selected update.");
        TargetManifest = ReadManifest(System.IO.Path.Combine(extracted, "files.json"));
        if (TargetManifest.Version != TargetVersion || TargetManifest.Hash != HashValue(metadata, "target_manifest_sha256") ||
            TargetManifest.Hash != FileManifestHash || new FileInfo(System.IO.Path.Combine(extracted, "files.json")).Length != FileManifestSize)
            throw new InvalidDataException("The target file manifest does not match the delta.");
        Manifest baseline = ReadManifest(System.IO.Path.Combine(AppDir, ManifestName));
        if (baseline.Version != BaseVersion || baseline.Hash != HashValue(metadata, "base_manifest_sha256"))
            throw new InvalidDataException("The installed baseline does not match this delta.");
        VerifyFiles(baseline);
        Stage("baseline_verified");
        PreflightTarget(TargetManifest);
        CreateBackup(baseline);
        PreflightTarget(TargetManifest);
        RunInstaller(System.IO.Path.Combine(extracted, "installer.exe"), "delta");
        VerifyTarget(TargetManifest);
        Stage("delta_verified");
    }

    static Manifest ReadManifest(string path)
    {
        byte[] bytes = ReadLimited(path, MaxJson);
        Dictionary<string, object> data = ParseObject(bytes);
        RequireSchema(data);
        Manifest result = new Manifest { Version = StringValue(data, "version"), Hash = Digest(bytes) };
        VersionParts(result.Version);
        object[] entries = ArrayValue(data, "files");
        if (entries.Length == 0 || entries.Length > 10000)
            throw new InvalidDataException("The file manifest has an invalid file count.");
        long total = 0;
        foreach (object item in entries)
        {
            Dictionary<string, object> entry = AsObject(item);
            string relative = Relative(StringValue(entry, "path"));
            if (relative.Equals(ManifestName, StringComparison.OrdinalIgnoreCase) || (!relative.Contains("/") && UninstallerPattern.IsMatch(relative)))
                throw new InvalidDataException("The manifest cannot own itself or generated uninstall files.");
            if (result.Files.ContainsKey(relative))
                throw new InvalidDataException("The manifest contains duplicate file paths.");
            FileEntry file = new FileEntry { Path = relative, Size = LongValue(entry, "size", 0, MaxFile), Hash = HashValue(entry, "sha256") };
            total += file.Size;
            if (total > MaxTotal)
                throw new InvalidDataException("The managed file data exceeds its size limit.");
            result.Files.Add(relative, file);
        }
        foreach (string relative in result.Files.Keys)
        {
            string parent = relative;
            while (parent.LastIndexOf('/') >= 0)
            {
                parent = parent.Substring(0, parent.LastIndexOf('/'));
                if (result.Files.ContainsKey(parent))
                    throw new InvalidDataException("The manifest contains conflicting file and directory paths.");
            }
        }
        if (!result.Files.ContainsKey("QtPackageTool.exe"))
            throw new InvalidDataException("The manifest does not contain the application executable.");
        return result;
    }

    static void VerifyFiles(Manifest manifest)
    {
        foreach (FileEntry file in manifest.Files.Values)
            VerifyAsset(AppPath(file.Path), file.Size, file.Hash);
    }

    static void PreflightTarget(Manifest target)
    {
        // Ownership comes from the installed manifest, even when a payload hash needs a full repair.
        Manifest baseline = BackupState == null ? ReadManifest(System.IO.Path.Combine(AppDir, ManifestName)) : BackupState.Baseline;
        if (baseline.Version != BaseVersion)
            throw new UserFileConflictException(ManifestName);
        foreach (string relative in target.Files.Keys)
        {
            string destination = AppPath(relative);
            if (Directory.Exists(destination) || (!baseline.Files.ContainsKey(relative) && File.Exists(destination)))
                throw new UserFileConflictException(relative);
            string parent = System.IO.Path.GetDirectoryName(destination);
            while (!parent.Equals(AppDir, StringComparison.OrdinalIgnoreCase))
            {
                if (File.Exists(parent))
                    throw new UserFileConflictException(parent);
                parent = System.IO.Path.GetDirectoryName(parent);
            }
        }
        // A failed preflight must never make unknown user paths eligible for rollback deletion.
        TargetPaths.UnionWith(target.Files.Keys);
        Stage("target_paths_verified");
    }

    static void VerifyTarget(Manifest expected)
    {
        Manifest installed = ReadManifest(System.IO.Path.Combine(AppDir, ManifestName));
        if (installed.Version != TargetVersion || (expected != null && installed.Hash != expected.Hash))
            throw new InvalidDataException("The installed target manifest does not match the requested update.");
        VerifyFiles(installed);
        if (BackupState != null && BackupState.Complete)
            foreach (string old in BackupState.Baseline.Files.Keys)
                if (!installed.Files.ContainsKey(old) && File.Exists(AppPath(old)))
                    throw new InvalidDataException("The installer left an obsolete managed file behind.");
        using (RegistryKey user = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        using (RegistryKey registration = user.OpenSubKey(UninstallKey))
            if (registration == null || Convert.ToString(registration.GetValue("DisplayVersion")) != TargetVersion)
                throw new InvalidDataException("The installed application registration has the wrong version.");
        Stage("target_verified");
    }

    static void CreateBackup(Manifest baseline)
    {
        BackupState = new Backup {
            Directory = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(AppDir), ".QtPackageTool-backup-" + Guid.NewGuid().ToString("N")),
            Baseline = baseline
        };
        Directory.CreateDirectory(BackupState.Directory);
        BackupState.Paths.AddRange(baseline.Files.Keys);
        BackupState.Paths.Add(ManifestName);
        foreach (string path in Directory.GetFiles(AppDir, "unins*"))
            if (UninstallerPattern.IsMatch(System.IO.Path.GetFileName(path)))
                BackupState.Paths.Add(System.IO.Path.GetFileName(path));
        foreach (string relative in BackupState.Paths)
        {
            string source = AppPath(relative);
            string destination = SafePath(BackupState.Directory, relative);
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(destination));
            File.Copy(source, destination, false);
            FileEntry expected;
            if (baseline.Files.TryGetValue(relative, out expected))
                VerifyAsset(destination, expected.Size, expected.Hash);
            else if (relative.Equals(ManifestName, StringComparison.OrdinalIgnoreCase) && Digest(ReadLimited(destination, MaxJson)) != baseline.Hash)
                throw new InvalidDataException("The baseline manifest changed while making its backup.");
        }
        using (RegistryKey user = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        using (RegistryKey key = user.OpenSubKey(UninstallKey))
            BackupState.Registration = SnapshotRegistry(key);
        AtomicJson(System.IO.Path.Combine(BackupState.Directory, "registration.json"), BackupState.Registration);
        BackupState.Complete = true;
        Stage("backup_created");
    }

    static void RestoreBackup()
    {
        Stage("restoring");
        foreach (string relative in TargetPaths)
            if (!BackupState.Baseline.Files.ContainsKey(relative))
                DeleteKnownFile(AppPath(relative));
        // Only empty directories implied by known target paths may be removed.
        HashSet<string> parents = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (string relative in TargetPaths)
        {
            string parent = System.IO.Path.GetDirectoryName(AppPath(relative));
            while (!parent.Equals(AppDir, StringComparison.OrdinalIgnoreCase))
            {
                parents.Add(parent);
                parent = System.IO.Path.GetDirectoryName(parent);
            }
        }
        foreach (string parent in parents.OrderByDescending(p => p.Length))
            if (Directory.Exists(parent) && !Directory.EnumerateFileSystemEntries(parent).Any())
            {
                AssertNoLinks(parent);
                Directory.Delete(parent);
            }
        foreach (string path in Directory.GetFiles(AppDir, "unins*"))
            if (UninstallerPattern.IsMatch(System.IO.Path.GetFileName(path)))
                DeleteKnownFile(path);
        foreach (string relative in BackupState.Paths)
        {
            string source = SafePath(BackupState.Directory, relative);
            string destination = AppPath(relative);
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(destination));
            if (File.Exists(destination))
                File.SetAttributes(destination, File.GetAttributes(destination) & ~FileAttributes.ReadOnly);
            File.Copy(source, destination, true);
        }
        using (RegistryKey user = RegistryKey.OpenBaseKey(RegistryHive.CurrentUser, RegistryView.Registry64))
        {
            user.DeleteSubKeyTree(UninstallKey, false);
            if (BackupState.Registration != null)
                using (RegistryKey key = user.CreateSubKey(UninstallKey))
                    RestoreRegistry(key, AsObject(BackupState.Registration));
        }
        Manifest restored = ReadManifest(System.IO.Path.Combine(AppDir, ManifestName));
        if (restored.Hash != BackupState.Baseline.Hash)
            throw new InvalidDataException("The baseline manifest could not be restored.");
        VerifyFiles(restored);
        Stage("rollback_complete");
    }

    static object SnapshotRegistry(RegistryKey key)
    {
        if (key == null)
            return null;
        List<object> values = new List<object>();
        foreach (string name in key.GetValueNames())
        {
            RegistryValueKind kind = key.GetValueKind(name);
            object value = key.GetValue(name, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
            if (value is byte[])
                value = Convert.ToBase64String((byte[])value);
            values.Add(new Dictionary<string, object> { { "name", name }, { "kind", (int)kind }, { "data", value } });
        }
        Dictionary<string, object> children = new Dictionary<string, object>();
        foreach (string name in key.GetSubKeyNames())
            using (RegistryKey child = key.OpenSubKey(name))
                children.Add(name, SnapshotRegistry(child));
        return new Dictionary<string, object> { { "values", values }, { "children", children } };
    }

    static void RestoreRegistry(RegistryKey key, Dictionary<string, object> snapshot)
    {
        IEnumerable values = (IEnumerable)snapshot["values"];
        foreach (object item in values)
        {
            Dictionary<string, object> value = AsObject(item);
            RegistryValueKind kind = (RegistryValueKind)Convert.ToInt32(value["kind"]);
            object data = value["data"];
            if (kind == RegistryValueKind.Binary || kind == RegistryValueKind.None)
                data = Convert.FromBase64String((string)data);
            else if (kind == RegistryValueKind.DWord)
                data = Convert.ToInt32(data);
            else if (kind == RegistryValueKind.QWord)
                data = Convert.ToInt64(data);
            else if (kind == RegistryValueKind.MultiString)
                data = ((IEnumerable)data).Cast<object>().Select(Convert.ToString).ToArray();
            key.SetValue((string)value["name"], data, kind);
        }
        foreach (KeyValuePair<string, object> child in AsObject(snapshot["children"]))
            using (RegistryKey subkey = key.CreateSubKey(child.Key))
                RestoreRegistry(subkey, AsObject(child.Value));
    }

    static void RunInstaller(string installer, string kind)
    {
        AssertNoLinks(installer);
        string[] arguments = { "/SP-", "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/RESTARTAPP=0", "/DIR=" + AppDir,
            "/LOG=" + System.IO.Path.Combine(WorkDir, kind + "-installer.log") };
        Stage(kind + "_installer_starting");
        using (Process process = Process.Start(new ProcessStartInfo(installer, String.Join(" ", arguments.Select(Quote))) {
            UseShellExecute = false, WorkingDirectory = System.IO.Path.GetDirectoryName(installer), CreateNoWindow = true
        }))
        {
            if (process == null)
                throw new IOException("The update installer could not be started.");
            Log(kind + "_installer_pid", process.Id.ToString());
            // Do not restore or start another installer while this one may still be modifying files.
            process.WaitForExit();
            if (process.ExitCode != 0)
                throw new IOException("The " + kind + " installer exited with code " + process.ExitCode + ".");
        }
        Stage(kind + "_installer_exited");
    }

    static string ObtainFullInstaller()
    {
        string installer = System.IO.Path.Combine(WorkDir, InstallerName);
        if (File.Exists(installer))
        {
            try { VerifyAsset(installer, FullSize, FullHash); Stage("full_cache_verified"); return installer; }
            catch (Exception invalidCache) { Log("full_cache_rejected", invalidCache.Message); DeleteKnownFile(installer); }
        }
        DownloadVerifiedAsset(FullUrl, installer, FullSize, FullHash);
        Stage("full_download_verified");
        return installer;
    }

    static Manifest ObtainTargetManifest()
    {
        if (TargetManifest != null && TargetManifest.Hash == FileManifestHash && TargetManifest.Version == TargetVersion)
        {
            return TargetManifest;
        }
        string cached = System.IO.Path.Combine(WorkDir, ManifestName);
        bool verified = false;
        if (File.Exists(cached))
        {
            try { VerifyAsset(cached, FileManifestSize, FileManifestHash); verified = true; }
            catch (Exception invalidCache) { Log("manifest_cache_rejected", invalidCache.Message); DeleteKnownFile(cached); }
        }
        if (!verified)
            DownloadVerifiedAsset(FileManifestUrl, cached, FileManifestSize, FileManifestHash);
        Manifest target = ReadManifest(cached);
        if (target.Version != TargetVersion || target.Hash != FileManifestHash)
            throw new InvalidDataException("The published file manifest has the wrong target version.");
        TargetManifest = target;
        Stage("full_manifest_verified");
        return target;
    }

    // Also used by a separately compiled transport probe; no additional product CLI is exposed.
    internal static string DownloadVerifiedAsset(string originalUrl, string destination, long expectedSize, string expectedHash)
    {
        Uri initial = new Uri(originalUrl);
        const string prefix = "/NCFXZ/QtPackageTool/releases/download/";
        string[] tail = initial.AbsolutePath.StartsWith(prefix, StringComparison.Ordinal) ? initial.AbsolutePath.Substring(prefix.Length).Split('/') : new string[0];
        if (initial.Host != "github.com" || !AllowedDownload(initial, originalUrl) || initial.Query != "" || tail.Length != 2 || tail.Any(String.IsNullOrEmpty) ||
            expectedSize <= 0 || expectedSize > MaxFile || !HashPattern.IsMatch(expectedHash))
            throw new InvalidDataException("The transport probe requires an official repository release asset.");
        destination = Absolute(destination);
        AssertNoLinks(destination);
        if (File.Exists(destination))
            throw new IOException("The download destination already exists.");
        string partial = destination + ".part";
        DeleteKnownFile(partial);
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        Uri url = initial;
        Stopwatch total = Stopwatch.StartNew();
        try
        {
            for (int redirects = 0; redirects <= 5; redirects++)
            {
                if (!AllowedDownload(url, originalUrl))
                    throw new InvalidDataException("The full installer redirected outside the allowed GitHub hosts.");
                HttpWebRequest request = (HttpWebRequest)WebRequest.Create(url);
                request.AllowAutoRedirect = false;
                request.Timeout = 30000;
                request.ReadWriteTimeout = 30000;
                request.UserAgent = "QtPackageTool-Updater";
                using (HttpWebResponse response = (HttpWebResponse)request.GetResponse())
                {
                    int code = (int)response.StatusCode;
                    if (code == 301 || code == 302 || code == 303 || code == 307 || code == 308)
                    {
                        string location = response.Headers["Location"];
                        if (String.IsNullOrEmpty(location) || redirects == 5)
                            throw new InvalidDataException("The full installer has too many redirects.");
                        url = new Uri(url, location);
                        continue;
                    }
                    if (code != 200 || (response.ContentLength >= 0 && response.ContentLength != expectedSize))
                        throw new InvalidDataException("The full installer response has an invalid size or status.");
                    using (Stream input = response.GetResponseStream())
                    using (FileStream output = new FileStream(partial, FileMode.CreateNew, FileAccess.Write, FileShare.None))
                    {
                        byte[] buffer = new byte[65536];
                        long received = 0;
                        int count;
                        while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
                        {
                            if (total.ElapsedMilliseconds > 15 * 60 * 1000 || received + count > expectedSize)
                                throw new InvalidDataException("The full installer download exceeded its time or size limit.");
                            output.Write(buffer, 0, count);
                            received += count;
                        }
                        if (received != expectedSize)
                            throw new InvalidDataException("The full installer download was incomplete.");
                    }
                    VerifyAsset(partial, expectedSize, expectedHash);
                    File.Move(partial, destination);
                    return destination;
                }
            }
        }
        catch
        {
            try { DeleteKnownFile(partial); } catch { }
            throw;
        }
        throw new InvalidDataException("The full installer could not be downloaded.");
    }

    static bool AllowedDownload(Uri url, string original)
    {
        if (!url.IsAbsoluteUri || url.Scheme != "https" || url.Port != 443 || url.UserInfo != "" || url.Fragment != "")
            return false;
        if (url.Host == "github.com")
            return url.AbsoluteUri == original;
        return url.Host == "release-assets.githubusercontent.com" || url.Host == "objects.githubusercontent.com";
    }

    static void VerifyAsset(string path, long size, string hash)
    {
        AssertNoLinks(path);
        if (!File.Exists(path) || new FileInfo(path).Length != size)
            throw new InvalidDataException("File size mismatch: " + System.IO.Path.GetFileName(path));
        using (FileStream stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (SHA256 algorithm = SHA256.Create())
            if (Hex(algorithm.ComputeHash(stream)) != hash)
                throw new InvalidDataException("File checksum mismatch: " + System.IO.Path.GetFileName(path));
    }

    static void CopyBounded(Stream input, Stream output, long expected)
    {
        byte[] buffer = new byte[65536];
        long copied = 0;
        int count;
        while ((count = input.Read(buffer, 0, buffer.Length)) != 0)
        {
            if (copied + count > expected)
                throw new InvalidDataException("An extracted ZIP entry exceeds its declared size.");
            output.Write(buffer, 0, count);
            copied += count;
        }
        if (copied != expected)
            throw new InvalidDataException("An extracted ZIP entry is incomplete.");
    }

    static string Relative(string path)
    {
        if (String.IsNullOrEmpty(path) || path.StartsWith("/") || Regex.IsMatch(path, "[\\\\\\x00-\\x1f<>:\"|?*]"))
            throw new InvalidDataException("The manifest contains an invalid relative path.");
        foreach (string part in path.Split('/'))
        {
            if (part == "" || part == "." || part == ".." || part.EndsWith(".") || part.EndsWith(" ") ||
                Regex.IsMatch(part.Split('.')[0].TrimEnd(' '), @"\A(CON|PRN|AUX|NUL|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³])\z", RegexOptions.IgnoreCase | RegexOptions.CultureInvariant))
                throw new InvalidDataException("The manifest contains an unsafe Windows path.");
        }
        return path;
    }

    static string Absolute(string path)
    {
        if (String.IsNullOrWhiteSpace(path) || !Regex.IsMatch(path, @"\A[A-Za-z]:[\\/]") || path.IndexOf('"') >= 0)
            throw new InvalidDataException("Update paths must be absolute local paths.");
        string full = System.IO.Path.GetFullPath(path);
        if (full.TrimEnd('\\', '/').Equals(System.IO.Path.GetPathRoot(full).TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("An update path cannot be a drive root.");
        return full.TrimEnd('\\', '/');
    }

    static bool IsWithin(string path, string root)
    {
        return path.Equals(root, StringComparison.OrdinalIgnoreCase) || path.StartsWith(root.TrimEnd('\\') + "\\", StringComparison.OrdinalIgnoreCase);
    }

    static string SafePath(string root, string relative)
    {
        Relative(relative);
        string path = System.IO.Path.GetFullPath(System.IO.Path.Combine(root, relative.Replace('/', '\\')));
        if (!IsWithin(path, root) || path.Equals(root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidDataException("A managed path escaped its application directory.");
        AssertNoLinks(path);
        return path;
    }

    static string AppPath(string relative) { return SafePath(AppDir, relative); }

    static void AssertNoLinks(string path)
    {
        string current = System.IO.Path.GetPathRoot(path);
        string tail = path.Substring(current.Length);
        foreach (string part in tail.Split(new[] { '\\', '/' }, StringSplitOptions.RemoveEmptyEntries))
        {
            current = System.IO.Path.Combine(current, part);
            FileAttributes attributes;
            try { attributes = File.GetAttributes(current); }
            catch (FileNotFoundException) { return; }
            catch (DirectoryNotFoundException) { return; }
            if ((attributes & FileAttributes.ReparsePoint) != 0)
            {
                using (SafeFileHandle handle = CreateFile(current, 0, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero))
                {
                    AttributeTagInfo info;
                    if (handle.IsInvalid || !GetFileInformationByHandleEx(handle, 9, out info, 8) || (info.Tag & 0x20000000) != 0)
                        throw new InvalidDataException("Linked update paths are not allowed: " + current);
                }
            }
        }
    }

    static void DeleteKnownFile(string path)
    {
        AssertNoLinks(path);
        if (File.Exists(path))
        {
            File.SetAttributes(path, File.GetAttributes(path) & ~FileAttributes.ReadOnly);
            File.Delete(path);
        }
    }

    static void CleanupSuccessfulBackup()
    {
        if (BackupState == null || !BackupState.Complete)
            return;
        try
        {
            foreach (string relative in BackupState.Paths)
                DeleteKnownFile(SafePath(BackupState.Directory, relative));
            DeleteKnownFile(System.IO.Path.Combine(BackupState.Directory, "registration.json"));
            RemoveEmptyBackupDirectories(BackupState.Directory);
            Log("backup_cleaned", "Completed update checkpoint removed.");
        }
        catch (Exception error) { Log("backup_cleanup_deferred", error.Message); }
    }

    static void RemoveEmptyBackupDirectories(string directory)
    {
        AssertNoLinks(directory);
        foreach (string child in Directory.GetDirectories(directory))
            RemoveEmptyBackupDirectories(child);
        if (!Directory.EnumerateFileSystemEntries(directory).Any())
            Directory.Delete(directory);
    }

    static Dictionary<string, object> ReadObject(string path) { return ParseObject(ReadLimited(path, MaxJson)); }
    static Dictionary<string, object> ParseObject(byte[] bytes) { return AsObject(Json.DeserializeObject(Utf8.GetString(bytes))); }
    static Dictionary<string, object> AsObject(object value)
    {
        Dictionary<string, object> result = value as Dictionary<string, object>;
        if (result == null) throw new InvalidDataException("Expected a JSON object.");
        return result;
    }
    static Dictionary<string, object> ObjectValue(Dictionary<string, object> data, string key)
    {
        object value;
        if (!data.TryGetValue(key, out value)) throw new InvalidDataException("Missing update field: " + key);
        return AsObject(value);
    }
    static string StringValue(Dictionary<string, object> data, string key)
    {
        object value;
        if (!data.TryGetValue(key, out value) || !(value is string)) throw new InvalidDataException("Invalid update text field: " + key);
        return (string)value;
    }
    static long LongValue(Dictionary<string, object> data, string key, long minimum, long maximum)
    {
        object value;
        if (!data.TryGetValue(key, out value) || !(value is int || value is long)) throw new InvalidDataException("Invalid update number: " + key);
        long number = Convert.ToInt64(value);
        if (number < minimum || number > maximum) throw new InvalidDataException("Update number out of range: " + key);
        return number;
    }
    static int IntValue(Dictionary<string, object> data, string key, int minimum, int maximum) { return (int)LongValue(data, key, minimum, maximum); }
    static string HashValue(Dictionary<string, object> data, string key)
    {
        string value = StringValue(data, key);
        if (!HashPattern.IsMatch(value)) throw new InvalidDataException("Invalid SHA-256 field: " + key);
        return value;
    }
    static object[] ArrayValue(Dictionary<string, object> data, string key)
    {
        object value;
        if (!data.TryGetValue(key, out value) || !(value is object[])) throw new InvalidDataException("Invalid update array: " + key);
        return (object[])value;
    }
    static void RequireSchema(Dictionary<string, object> data) { IntValue(data, "schema", 1, 1); }
    static Version VersionParts(string text)
    {
        if (!VersionPattern.IsMatch(text)) throw new InvalidDataException("Only stable X.Y.Z versions are supported.");
        return new Version(text);
    }
    static byte[] ReadLimited(string path, int limit)
    {
        AssertNoLinks(path);
        if (!File.Exists(path) || new FileInfo(path).Length > limit) throw new InvalidDataException("Missing or oversized update JSON.");
        return File.ReadAllBytes(path);
    }
    static string Hex(byte[] bytes) { return BitConverter.ToString(bytes).Replace("-", "").ToLowerInvariant(); }
    static string Digest(byte[] bytes) { using (SHA256 hash = SHA256.Create()) return Hex(hash.ComputeHash(bytes)); }
    static string Quote(string value)
    {
        StringBuilder result = new StringBuilder("\"");
        int slashes = 0;
        foreach (char character in value)
        {
            if (character == '\\') { slashes++; continue; }
            if (character == '"') { result.Append('\\', slashes * 2 + 1); result.Append('"'); }
            else { result.Append('\\', slashes); result.Append(character); }
            slashes = 0;
        }
        result.Append('\\', slashes * 2);
        result.Append('"');
        return result.ToString();
    }

    static void Stage(string phase)
    {
        Log(phase, "");
        AtomicJson(System.IO.Path.Combine(WorkDir, "helper-state.json"), new Dictionary<string, object> {
            { "phase", phase }, { "mode", Mode }, { "fallback_used", FallbackUsed }, { "app_dir", AppDir },
            { "base_version", BaseVersion }, { "target_version", TargetVersion },
            { "backup_dir", BackupState == null ? null : BackupState.Directory }, { "backup_complete", BackupState != null && BackupState.Complete }
        });
    }
    static void Result(bool success, string error)
    {
        AtomicJson(System.IO.Path.Combine(WorkDir, "helper-result.json"), new Dictionary<string, object> {
            { "success", success }, { "mode", Mode }, { "fallback_used", FallbackUsed }, { "error", error },
            { "app_dir", AppDir }, { "target_version", TargetVersion }, { "backup_dir", BackupState == null ? null : BackupState.Directory }
        });
    }
    static void AtomicJson(string path, object value)
    {
        string temporary = path + ".tmp";
        AssertNoLinks(path);
        AssertNoLinks(temporary);
        byte[] bytes = Utf8.GetBytes(Json.Serialize(value));
        using (FileStream stream = new FileStream(temporary, FileMode.Create, FileAccess.Write, FileShare.None))
        {
            stream.Write(bytes, 0, bytes.Length);
            stream.Flush(true);
        }
        if (File.Exists(path)) File.Replace(temporary, path, null);
        else File.Move(temporary, path);
    }
    static void Log(string phase, string message)
    {
        if (WorkDir == null) return;
        try
        {
            string path = System.IO.Path.Combine(WorkDir, "helper.log");
            AssertNoLinks(path);
            File.AppendAllText(path, DateTime.UtcNow.ToString("o") + " | " + phase + " | " + message + Environment.NewLine, Utf8);
        }
        catch { }
    }
}

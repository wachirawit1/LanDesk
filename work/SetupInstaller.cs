using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("LanDesk Setup v0.3.1")]
[assembly: AssemblyProduct("LanDesk")]
[assembly: AssemblyVersion("0.3.1.0")]
[assembly: AssemblyFileVersion("0.3.1.0")]

namespace LanDeskSetup
{
    public class SetupForm : Form
    {
        private TextBox txtDir;
        private CheckBox chkDesktop;
        private CheckBox chkStartMenu;
        private CheckBox chkAutoService;
        private CheckBox chkLaunch;
        private ProgressBar progressBar;
        private Label lblStatus;
        private Button btnInstall;
        private Button btnCancel;

        public SetupForm()
        {
            this.Text = "ติดตั้ง LanDesk v0.3.1 · Setup Wizard";
            this.ClientSize = new Size(560, 395);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(237, 242, 246);
            this.Font = new Font("Leelawadee UI", 10f, FontStyle.Regular);
            try { this.Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); } catch { }

            Label lblTitle = new Label();
            lblTitle.Text = "ติดตั้ง LanDesk v0.3.1 (รีโมทภายในองค์กร)";
            lblTitle.Font = new Font("Leelawadee UI", 15f, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(0, 105, 92);
            lblTitle.Location = new Point(22, 18);
            lblTitle.AutoSize = true;
            this.Controls.Add(lblTitle);

            Label lblSub = new Label();
            lblSub.Text = "ติดตั้งโปรแกรมรีโมทเดสก์ท็อปภายในองค์กร พร้อมสร้างทางลัดและตัวถอนการติดตั้ง (Uninstaller)";
            lblSub.Location = new Point(22, 50);
            lblSub.Size = new Size(496, 24);
            this.Controls.Add(lblSub);

            Label lblDir = new Label();
            lblDir.Text = "โฟลเดอร์สำหรับติดตั้ง:";
            lblDir.Font = new Font("Leelawadee UI", 10f, FontStyle.Bold);
            lblDir.Location = new Point(22, 84);
            lblDir.AutoSize = true;
            this.Controls.Add(lblDir);

            string defaultDir = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "LanDesk"
            );

            txtDir = new TextBox();
            txtDir.Text = defaultDir;
            txtDir.Location = new Point(22, 108);
            txtDir.Size = new Size(390, 28);
            this.Controls.Add(txtDir);

            Button btnBrowse = new Button();
            btnBrowse.Text = "เลือกโฟลเดอร์...";
            btnBrowse.Location = new Point(420, 106);
            btnBrowse.Size = new Size(98, 29);
            btnBrowse.Click += OnBrowse;
            this.Controls.Add(btnBrowse);

            chkDesktop = new CheckBox();
            chkDesktop.Text = "สร้างไอคอนทางลัดบนหน้าจอ Desktop (Desktop Shortcut)";
            chkDesktop.Checked = true;
            chkDesktop.Location = new Point(24, 150);
            chkDesktop.Size = new Size(480, 24);
            this.Controls.Add(chkDesktop);

            chkStartMenu = new CheckBox();
            chkStartMenu.Text = "สร้างทางลัดใน Start Menu และลงทะเบียนตัวถอนการติดตั้ง (Uninstall)";
            chkStartMenu.Checked = true;
            chkStartMenu.Location = new Point(24, 178);
            chkStartMenu.Size = new Size(510, 24);
            this.Controls.Add(chkStartMenu);

            chkAutoService = new CheckBox();
            chkAutoService.Text = "รันเซอร์วิสพร้อมรับการเชื่อมต่ออัตโนมัติเมื่อเปิดเครื่อง (เหมือน AnyDesk / VNC)";
            chkAutoService.Checked = true;
            chkAutoService.Location = new Point(24, 206);
            chkAutoService.Size = new Size(510, 24);
            this.Controls.Add(chkAutoService);

            chkLaunch = new CheckBox();
            chkLaunch.Text = "เปิดโปรแกรมและเริ่มเซอร์วิสพร้อมรับการเชื่อมต่อทันทีหลังติดตั้งเสร็จ";
            chkLaunch.Checked = true;
            chkLaunch.Location = new Point(24, 234);
            chkLaunch.Size = new Size(510, 24);
            this.Controls.Add(chkLaunch);

            progressBar = new ProgressBar();
            progressBar.Location = new Point(22, 272);
            progressBar.Size = new Size(516, 22);
            progressBar.Minimum = 0;
            progressBar.Maximum = 100;
            this.Controls.Add(progressBar);

            lblStatus = new Label();
            lblStatus.Text = "พร้อมติดตั้งลงเครื่อง";
            lblStatus.Location = new Point(22, 298);
            lblStatus.Size = new Size(516, 22);
            this.Controls.Add(lblStatus);

            btnInstall = new Button();
            btnInstall.Text = "ติดตั้งทันที (Install)";
            btnInstall.Font = new Font("Leelawadee UI", 10f, FontStyle.Bold);
            btnInstall.BackColor = Color.FromArgb(0, 105, 92);
            btnInstall.ForeColor = Color.White;
            btnInstall.FlatStyle = FlatStyle.Flat;
            btnInstall.Location = new Point(22, 332);
            btnInstall.Size = new Size(180, 38);
            btnInstall.Click += OnInstallClick;
            this.Controls.Add(btnInstall);

            btnCancel = new Button();
            btnCancel.Text = "ยกเลิก";
            btnCancel.Location = new Point(438, 332);
            btnCancel.Size = new Size(100, 38);
            btnCancel.Click += (s, e) => this.Close();
            this.Controls.Add(btnCancel);
        }

        private void OnBrowse(object sender, EventArgs e)
        {
            using (FolderBrowserDialog fbd = new FolderBrowserDialog())
            {
                fbd.SelectedPath = txtDir.Text;
                if (fbd.ShowDialog(this) == DialogResult.OK)
                {
                    string p = fbd.SelectedPath;
                    if (!p.EndsWith("LanDesk", StringComparison.OrdinalIgnoreCase))
                        p = Path.Combine(p, "LanDesk");
                    txtDir.Text = p;
                }
            }
        }

        private void OnInstallClick(object sender, EventArgs e)
        {
            string targetDir = txtDir.Text.Trim();
            if (string.IsNullOrEmpty(targetDir))
            {
                MessageBox.Show(this, "กรุณาระบุโฟลเดอร์สำหรับติดตั้ง", "ติดตั้ง LanDesk", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            btnInstall.Enabled = false;
            btnCancel.Enabled = false;
            bool makeDesktop = chkDesktop.Checked;
            bool makeStart = chkStartMenu.Checked;
            bool autoService = chkAutoService.Checked;
            bool launchNow = chkLaunch.Checked;

            Thread worker = new Thread(() =>
            {
                try
                {
                    InstallerEngine.PerformInstall(targetDir, makeDesktop, makeStart, autoService, (pct, msg) =>
                    {
                        this.BeginInvoke((Action)(() =>
                        {
                            progressBar.Value = Math.Min(100, Math.Max(0, pct));
                            lblStatus.Text = msg;
                        }));
                    });

                    this.BeginInvoke((Action)(() =>
                    {
                        progressBar.Value = 100;
                        lblStatus.Text = "ติดตั้งเสร็จสมบูรณ์!";
                        MessageBox.Show(
                            this,
                            "ติดตั้ง LanDesk v0.3.0 เรียบร้อยแล้วที่:\n" + targetDir +
                            "\n\n• สามารถเปิดโปรแกรมได้จากไอคอน LanDesk บนหน้าจอ Desktop หรือ Start Menu\n• สามารถถอนการติดตั้ง (Uninstall) ได้จาก Uninstall-LanDesk.exe หรือใน Windows Settings -> Apps",
                            "ติดตั้งสำเร็จ",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information
                        );
                        if (launchNow)
                        {
                            string exePath = Path.Combine(targetDir, "LanDesk.exe");
                            if (File.Exists(exePath))
                            {
                                Process.Start(new ProcessStartInfo(exePath) { WorkingDirectory = targetDir });
                            }
                        }
                        this.Close();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke((Action)(() =>
                    {
                        btnInstall.Enabled = true;
                        btnCancel.Enabled = true;
                        lblStatus.Text = "เกิดข้อผิดพลาด: " + ex.Message;
                        MessageBox.Show(this, "ไม่สามารถติดตั้งได้:\n" + ex.Message, "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }));
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }
    }

    public static class InstallerEngine
    {
        public static void PerformInstall(string targetDir, bool desktopShortcut, bool startMenuShortcut, bool autoService, Action<int, string> onProgress)
        {
            if (onProgress != null) onProgress(5, "กำลังเตรียมโฟลเดอร์ติดตั้ง...");
            try
            {
                foreach (Process proc in Process.GetProcessesByName("LanDesk"))
                {
                    try { proc.Kill(); proc.WaitForExit(2000); } catch { }
                }
            }
            catch { }

            Directory.CreateDirectory(targetDir);

            Assembly asm = Assembly.GetExecutingAssembly();
            using (Stream resStream = asm.GetManifestResourceStream("payload.zip"))
            {
                if (resStream == null)
                    throw new InvalidOperationException("ไม่พบแพ็กเกจติดตั้งภายในไฟล์ Setup");

                using (ZipArchive archive = new ZipArchive(resStream, ZipArchiveMode.Read))
                {
                    int total = archive.Entries.Count;
                    int index = 0;
                    foreach (ZipArchiveEntry entry in archive.Entries)
                    {
                        index++;
                        string relPath = entry.FullName.Replace('/', Path.DirectorySeparatorChar);
                        if (relPath.StartsWith("LanDesk" + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase))
                        {
                            relPath = relPath.Substring(("LanDesk" + Path.DirectorySeparatorChar).Length);
                        }
                        if (string.IsNullOrEmpty(relPath))
                            continue;

                        string destPath = Path.Combine(targetDir, relPath);
                        if (string.IsNullOrEmpty(entry.Name))
                        {
                            Directory.CreateDirectory(destPath);
                            continue;
                        }

                        Directory.CreateDirectory(Path.GetDirectoryName(destPath));
                        entry.ExtractToFile(destPath, true);

                        if (onProgress != null && (index % 15 == 0 || index == total))
                        {
                            int pct = 10 + (int)((index * 75.0) / Math.Max(1, total));
                            onProgress(pct, "กำลังแตกไฟล์ระบบ (" + index + "/" + total + ")...");
                        }
                    }
                }
            }

            string mainExe = Path.Combine(targetDir, "LanDesk.exe");
            string uninstExe = Path.Combine(targetDir, "Uninstall-LanDesk.exe");
            string icoFile = Path.Combine(targetDir, "landesk.ico");
            string iconPath = File.Exists(icoFile) ? icoFile : mainExe;
            if (File.Exists(mainExe) && !File.Exists(uninstExe))
            {
                File.Copy(mainExe, uninstExe, true);
            }

            if (onProgress != null) onProgress(88, "กำลังตั้งค่าเซอร์วิสทำงานอัตโนมัติเมื่อเปิดเครื่อง...");
            try
            {
                using (RegistryKey runKey = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run"))
                {
                    if (runKey != null)
                    {
                        if (autoService && File.Exists(mainExe))
                            runKey.SetValue("LanDesk", "\"" + mainExe + "\" --background");
                        else
                            runKey.DeleteValue("LanDesk", false);
                    }
                }
            }
            catch { }

            if (onProgress != null) onProgress(92, "กำลังลงทะเบียนระบบถอนการติดตั้ง (Uninstaller)...");
            try
            {
                using (RegistryKey key = Registry.CurrentUser.CreateSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\LanDesk"))
                {
                    if (key != null)
                    {
                        key.SetValue("DisplayName", "LanDesk 0.3.0 (รีโมทภายในองค์กร)");
                        key.SetValue("DisplayVersion", "0.3.0");
                        key.SetValue("Publisher", "LanDesk Intranet Remote");
                        key.SetValue("DisplayIcon", iconPath);
                        key.SetValue("InstallLocation", targetDir);
                        key.SetValue("UninstallString", "\"" + uninstExe + "\"");
                        key.SetValue("QuietUninstallString", "\"" + uninstExe + "\" --silent");
                        key.SetValue("NoModify", 1, RegistryValueKind.DWord);
                        key.SetValue("NoRepair", 1, RegistryValueKind.DWord);
                    }
                }
            }
            catch { }

            if (onProgress != null) onProgress(96, "กำลังสร้างไอคอนทางลัด (Shortcuts)...");
            if (desktopShortcut && File.Exists(mainExe))
            {
                try
                {
                    string desktopDir = Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);
                    CreateShortcut(Path.Combine(desktopDir, "LanDesk.lnk"), mainExe, "", targetDir, "LanDesk 0.3.0 รีโมทภายในองค์กร", iconPath);
                }
                catch { }
            }

            if (startMenuShortcut && File.Exists(mainExe))
            {
                try
                {
                    string programsDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "LanDesk");
                    Directory.CreateDirectory(programsDir);
                    CreateShortcut(Path.Combine(programsDir, "LanDesk.lnk"), mainExe, "", targetDir, "LanDesk 0.3.0 รีโมทภายในองค์กร", iconPath);
                    if (File.Exists(uninstExe))
                    {
                        CreateShortcut(Path.Combine(programsDir, "Uninstall LanDesk (ถอนการติดตั้ง).lnk"), uninstExe, "", targetDir, "ถอนการติดตั้ง LanDesk", uninstExe);
                    }
                }
                catch { }
            }
        }

        private static void CreateShortcut(string shortcutPath, string targetPath, string arguments, string workingDir, string description, string iconPath)
        {
            try
            {
                Type shellType = Type.GetTypeFromProgID("WScript.Shell");
                if (shellType == null) return;
                object shell = Activator.CreateInstance(shellType);
                object shortcut = shellType.InvokeMember("CreateShortcut", BindingFlags.InvokeMethod, null, shell, new object[] { shortcutPath });
                Type scType = shortcut.GetType();
                scType.InvokeMember("TargetPath", BindingFlags.SetProperty, null, shortcut, new object[] { targetPath });
                scType.InvokeMember("Arguments", BindingFlags.SetProperty, null, shortcut, new object[] { arguments });
                scType.InvokeMember("WorkingDirectory", BindingFlags.SetProperty, null, shortcut, new object[] { workingDir });
                scType.InvokeMember("Description", BindingFlags.SetProperty, null, shortcut, new object[] { description });
                scType.InvokeMember("IconLocation", BindingFlags.SetProperty, null, shortcut, new object[] { iconPath + ",0" });
                scType.InvokeMember("Save", BindingFlags.InvokeMethod, null, shortcut, null);
            }
            catch { }
        }
    }

    public static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            bool silent = false;
            bool launch = false;
            string customDir = null;

            foreach (string arg in args)
            {
                string lower = arg.ToLowerInvariant();
                if (lower == "--silent" || lower == "/s" || lower == "/silent")
                    silent = true;
                else if (lower == "--launch")
                    launch = true;
                else if (lower.StartsWith("--dir="))
                    customDir = arg.Substring(6).Trim('"');
            }

            if (silent)
            {
                try
                {
                    string targetDir = customDir;
                    if (string.IsNullOrEmpty(targetDir))
                    {
                        targetDir = Path.Combine(
                            Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                            "Programs",
                            "LanDesk"
                        );
                    }
                    InstallerEngine.PerformInstall(targetDir, true, true, true, null);
                    if (launch)
                    {
                        string exePath = Path.Combine(targetDir, "LanDesk.exe");
                        if (File.Exists(exePath))
                            Process.Start(new ProcessStartInfo(exePath) { WorkingDirectory = targetDir });
                    }
                    return 0;
                }
                catch
                {
                    return 1;
                }
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new SetupForm());
            return 0;
        }
    }
}

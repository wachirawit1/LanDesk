using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Windows.Forms;
using Microsoft.Win32;

[assembly: AssemblyTitle("LanDesk Uninstaller")]
[assembly: AssemblyProduct("LanDesk")]
[assembly: AssemblyVersion("0.3.0.0")]
[assembly: AssemblyFileVersion("0.3.0.0")]

namespace LanDeskUninstaller
{
    public class UninstallForm : Form
    {
        private string targetDir;
        private CheckBox chkRemoveData;
        private ProgressBar progressBar;
        private Label lblStatus;
        private Button btnUninstall;
        private Button btnCancel;

        public UninstallForm(string installDir)
        {
            this.targetDir = installDir;
            this.Text = "ถอนการติดตั้ง LanDesk (Uninstall)";
            this.ClientSize = new Size(520, 300);
            this.FormBorderStyle = FormBorderStyle.FixedDialog;
            this.MaximizeBox = false;
            this.StartPosition = FormStartPosition.CenterScreen;
            this.BackColor = Color.FromArgb(237, 242, 246);
            this.Font = new Font("Leelawadee UI", 10f, FontStyle.Regular);
            this.TopMost = true;
            try { this.Icon = Icon.ExtractAssociatedIcon(Assembly.GetExecutingAssembly().Location); } catch { }

            Label lblTitle = new Label();
            lblTitle.Text = "ถอนการติดตั้ง LanDesk (Uninstall)";
            lblTitle.Font = new Font("Leelawadee UI", 15f, FontStyle.Bold);
            lblTitle.ForeColor = Color.FromArgb(183, 28, 28);
            lblTitle.Location = new Point(22, 18);
            lblTitle.AutoSize = true;
            this.Controls.Add(lblTitle);

            Label lblDesc = new Label();
            lblDesc.Text = "คุณต้องการถอนการติดตั้งโปรแกรม LanDesk ออกจากเครื่องคอมพิวเตอร์นี้หรือไม่?\nโฟลเดอร์ที่ติดตั้ง: " + installDir;
            lblDesc.Location = new Point(22, 54);
            lblDesc.Size = new Size(476, 48);
            this.Controls.Add(lblDesc);

            chkRemoveData = new CheckBox();
            chkRemoveData.Text = "ลบข้อมูลการตั้งค่า สมุดรายชื่อ และไฟล์ที่เคยรับทั้งหมด (%LOCALAPPDATA%\\LanDesk)";
            chkRemoveData.Checked = false;
            chkRemoveData.Location = new Point(24, 114);
            chkRemoveData.Size = new Size(476, 26);
            this.Controls.Add(chkRemoveData);

            progressBar = new ProgressBar();
            progressBar.Location = new Point(22, 158);
            progressBar.Size = new Size(476, 22);
            progressBar.Minimum = 0;
            progressBar.Maximum = 100;
            this.Controls.Add(progressBar);

            lblStatus = new Label();
            lblStatus.Text = "กดปุ่ม \"ยืนยันถอนการติดตั้ง\" เพื่อเริ่มลบโปรแกรม";
            lblStatus.Location = new Point(22, 186);
            lblStatus.Size = new Size(476, 24);
            this.Controls.Add(lblStatus);

            btnUninstall = new Button();
            btnUninstall.Text = "ยืนยันถอนการติดตั้ง (Uninstall)";
            btnUninstall.Font = new Font("Leelawadee UI", 10f, FontStyle.Bold);
            btnUninstall.BackColor = Color.FromArgb(183, 28, 28);
            btnUninstall.ForeColor = Color.White;
            btnUninstall.FlatStyle = FlatStyle.Flat;
            btnUninstall.Location = new Point(22, 232);
            btnUninstall.Size = new Size(220, 38);
            btnUninstall.Click += OnUninstallClick;
            this.Controls.Add(btnUninstall);

            btnCancel = new Button();
            btnCancel.Text = "ยกเลิก";
            btnCancel.Location = new Point(398, 232);
            btnCancel.Size = new Size(100, 38);
            btnCancel.Click += (s, e) => this.Close();
            this.Controls.Add(btnCancel);
        }

        private void OnUninstallClick(object sender, EventArgs e)
        {
            btnUninstall.Enabled = false;
            btnCancel.Enabled = false;
            bool purgeData = chkRemoveData.Checked;

            Thread worker = new Thread(() =>
            {
                try
                {
                    UninstallEngine.PerformUninstall(targetDir, purgeData, (pct, msg) =>
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
                        lblStatus.Text = "ถอนการติดตั้งเสร็จสมบูรณ์!";
                        MessageBox.Show(
                            this,
                            "ถอนการติดตั้ง LanDesk ออกจากเครื่องเรียบร้อยแล้ว",
                            "ถอนการติดตั้งสำเร็จ",
                            MessageBoxButtons.OK,
                            MessageBoxIcon.Information
                        );
                        this.Close();
                    }));
                }
                catch (Exception ex)
                {
                    this.BeginInvoke((Action)(() =>
                    {
                        btnUninstall.Enabled = true;
                        btnCancel.Enabled = true;
                        lblStatus.Text = "เกิดข้อผิดพลาด: " + ex.Message;
                        MessageBox.Show(this, "เกิดข้อผิดพลาดระหว่างถอนการติดตั้ง:\n" + ex.Message, "ข้อผิดพลาด", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    }));
                }
            });
            worker.IsBackground = true;
            worker.Start();
        }
    }

    public static class UninstallEngine
    {
        public static string ResolveInstallDir(string explicitDir, string exeDir)
        {
            if (!string.IsNullOrEmpty(explicitDir) && Directory.Exists(explicitDir))
                return Path.GetFullPath(explicitDir);

            // If Uninstall-LanDesk.exe is run inside a folder alongside LanDesk.exe, target that exact folder!
            if (!string.IsNullOrEmpty(exeDir) && File.Exists(Path.Combine(exeDir, "LanDesk.exe")))
                return Path.GetFullPath(exeDir);

            try
            {
                using (RegistryKey key = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\LanDesk"))
                {
                    if (key != null)
                    {
                        object val = key.GetValue("InstallLocation");
                        if (val != null && !string.IsNullOrEmpty(val.ToString()) && Directory.Exists(val.ToString()))
                            return Path.GetFullPath(val.ToString());
                    }
                }
            }
            catch { }

            return Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Programs",
                "LanDesk"
            );
        }

        public static void PerformUninstall(string targetDir, bool removeUserData, Action<int, string> onProgress)
        {
            if (onProgress != null) onProgress(10, "กำลังปิดโปรเซส LanDesk ที่กำลังทำงานอยู่...");
            int myPid = Process.GetCurrentProcess().Id;
            foreach (string procName in new string[] { "LanDesk", "LanDesk.old" })
            {
                try
                {
                    foreach (Process proc in Process.GetProcessesByName(procName))
                    {
                        if (proc.Id != myPid)
                        {
                            try { proc.Kill(); proc.WaitForExit(3000); } catch { }
                        }
                    }
                }
                catch { }
            }

            Thread.Sleep(400);

            if (onProgress != null) onProgress(30, "กำลังลบไอคอนทางลัด (Desktop & Start Menu)...");
            try
            {
                string desktopLnk = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "LanDesk.lnk");
                if (File.Exists(desktopLnk)) File.Delete(desktopLnk);
            }
            catch { }

            try
            {
                string startMenuDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Programs), "LanDesk");
                if (Directory.Exists(startMenuDir)) Directory.Delete(startMenuDir, true);
            }
            catch { }

            if (onProgress != null) onProgress(50, "กำลังลบข้อมูลการลงทะเบียนในระบบ Windows...");
            try
            {
                using (RegistryKey runKey = Registry.CurrentUser.OpenSubKey(@"Software\Microsoft\Windows\CurrentVersion\Run", true))
                {
                    if (runKey != null) runKey.DeleteValue("LanDesk", false);
                }
            }
            catch { }

            try
            {
                Registry.CurrentUser.DeleteSubKeyTree(@"Software\Microsoft\Windows\CurrentVersion\Uninstall\LanDesk", false);
            }
            catch { }

            if (removeUserData)
            {
                if (onProgress != null) onProgress(65, "กำลังลบข้อมูลการตั้งค่าและสมุดรายชื่อ...");
                try
                {
                    string dataDir = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "LanDesk");
                    if (Directory.Exists(dataDir)) Directory.Delete(dataDir, true);
                }
                catch { }
            }

            if (onProgress != null) onProgress(80, "กำลังลบไฟล์โปรแกรม LanDesk...");
            if (!string.IsNullOrEmpty(targetDir) && Directory.Exists(targetDir))
            {
                for (int attempt = 0; attempt < 6; attempt++)
                {
                    try
                    {
                        Directory.Delete(targetDir, true);
                        break;
                    }
                    catch
                    {
                        Thread.Sleep(500);
                    }
                }
                if (Directory.Exists(targetDir))
                {
                    // Fallback cmd cleanup if any file was still releasing
                    string cmdPath = Path.Combine(Path.GetTempPath(), "landesk_cleanup_" + Environment.TickCount + ".cmd");
                    File.WriteAllText(cmdPath, "@echo off\r\ncd /d \"%TEMP%\"\r\nping 127.0.0.1 -n 3 > nul\r\nrmdir /s /q \"" + targetDir + "\" 2>nul\r\n(goto) 2>nul & del \"%~f0\"\r\n");
                    ProcessStartInfo psi = new ProcessStartInfo("cmd.exe", "/c \"" + cmdPath + "\"")
                    {
                        WorkingDirectory = Path.GetTempPath(),
                        CreateNoWindow = true,
                        UseShellExecute = false
                    };
                    Process.Start(psi);
                }
            }
        }
    }

    public static class Program
    {
        [STAThread]
        public static int Main(string[] args)
        {
            string exePath = Assembly.GetExecutingAssembly().Location;
            string exeDir = Path.GetDirectoryName(exePath);
            string tempDir = Path.GetTempPath().TrimEnd(Path.DirectorySeparatorChar);

            string explicitDir = null;
            bool silent = false;
            bool purge = false;
            bool relaunched = false;

            foreach (string arg in args)
            {
                string lower = arg.ToLowerInvariant();
                if (lower == "--silent" || lower == "/s") silent = true;
                else if (lower == "--purge") purge = true;
                else if (lower == "--relaunched") relaunched = true;
                else if (lower.StartsWith("--dir=")) explicitDir = arg.Substring(6).Trim('"');
            }

            string targetDir = UninstallEngine.ResolveInstallDir(explicitDir, exeDir);

            // If running from inside the installation directory, copy self to TEMP and relaunch so the directory is not locked!
            if (!relaunched && !string.IsNullOrEmpty(exeDir) && !string.IsNullOrEmpty(targetDir) &&
                string.Equals(Path.GetFullPath(exeDir).TrimEnd('\\'), Path.GetFullPath(targetDir).TrimEnd('\\'), StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    string tempExe = Path.Combine(Path.GetTempPath(), "LanDesk-Uninstaller-" + Environment.TickCount + ".exe");
                    File.Copy(exePath, tempExe, true);
                    string newArgs = "--relaunched --dir=\"" + targetDir + "\"" + (silent ? " --silent" : "") + (purge ? " --purge" : "");
                    ProcessStartInfo psi = new ProcessStartInfo(tempExe, newArgs)
                    {
                        WorkingDirectory = Path.GetTempPath(),
                        UseShellExecute = false
                    };
                    Process.Start(psi);
                    return 0;
                }
                catch { }
            }

            Environment.CurrentDirectory = Path.GetTempPath();

            if (silent)
            {
                UninstallEngine.PerformUninstall(targetDir, purge, null);
                return 0;
            }

            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new UninstallForm(targetDir));
            return 0;
        }
    }
}

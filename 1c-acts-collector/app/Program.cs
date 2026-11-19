using System;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.IO.Compression;
using System.Net;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading.Tasks;
using System.Windows.Forms;

namespace SborAktov
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm());
        }
    }

    internal sealed class MainForm : Form
    {
        private readonly TextBox _log;
        private readonly Label _counter;
        private readonly Button _button;
        private readonly object _gate = new object();
        private volatile bool _running;
        private volatile bool _stop;
        private Process _current;

        public MainForm()
        {
            Text = "Сбор актов 1С";
            StartPosition = FormStartPosition.CenterScreen;
            ClientSize = new Size(720, 520);
            MinimumSize = new Size(640, 420);
            Font = new Font("Segoe UI", 11F);

            var help = new Label
            {
                Dock = DockStyle.Top,
                Height = 96,
                Padding = new Padding(12, 10, 12, 4),
                Text = "Откройте в 1С отчёт «Обращения клиентов. Акты оценки».\r\nНажмите «Старт». После «Стоп» или выключения компьютера нажмите «Старт» снова: программа продолжит с непроверенных актов.\r\nОкно 1С не закрывайте."
            };

            _counter = new Label
            {
                Dock = DockStyle.Top,
                Height = 42,
                Padding = new Padding(12, 8, 12, 4),
                Font = new Font("Segoe UI", 14F, FontStyle.Bold),
                Text = SavedCounterText()
            };

            _button = new Button
            {
                Dock = DockStyle.Bottom,
                Height = 56,
                Text = "Старт",
                Font = new Font("Segoe UI", 16F, FontStyle.Bold)
            };
            _button.Click += OnButton;

            _log = new TextBox
            {
                Dock = DockStyle.Fill,
                Multiline = true,
                ReadOnly = true,
                ScrollBars = ScrollBars.Vertical,
                Font = new Font("Consolas", 10F),
                BackColor = Color.White
            };

            Controls.Add(_log);
            Controls.Add(_button);
            Controls.Add(_counter);
            Controls.Add(help);
        }

        private void OnButton(object sender, EventArgs e)
        {
            if (_running)
            {
                _stop = true;
                Log("Останавливаю...");
                KillCurrent();
                return;
            }

            _stop = false;
            _running = true;
            _button.Text = "Стоп";
            _log.Clear();
            Task.Run(delegate
            {
                try
                {
                    Work();
                }
                catch (Exception ex)
                {
                    Log("Не вышло: " + ex.Message);
                }
                finally
                {
                    BeginInvoke((Action)delegate
                    {
                        _running = false;
                        _button.Text = "Старт";
                    });
                }
            });
        }

        private void Work()
        {
            string root = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Sbor-aktov-1C");
            Directory.CreateDirectory(root);
            Log("Папка программы: " + root);

            if (_stop) return;
            Log("Раскладываю файлы сбора...");
            ExtractPayload(root);

            if (_stop) return;
            string python = EnsurePython();
            Log("Python: " + python);

            if (_stop) return;
            string tesseract = EnsureTesseract(root);
            Log("Чтение текста: " + tesseract);
            WriteTesseractPath(Path.Combine(root, "config.json"), tesseract);

            if (_stop) return;
            string venvPython = Path.Combine(root, ".venv", "Scripts", "python.exe");
            if (!File.Exists(venvPython))
            {
                Log("Готовлю окружение Python...");
                int code = Run(python, "-m venv \"" + Path.Combine(root, ".venv") + "\"", root, null);
                if (code != 0 || !File.Exists(venvPython))
                    throw new InvalidOperationException("Не удалось подготовить Python.");
            }

            string req = File.ReadAllText(Path.Combine(root, "requirements.txt"));
            string hash = Hash(req);
            string marker = Path.Combine(root, ".venv", ".deps-ok");
            if (!File.Exists(marker) || File.ReadAllText(marker).Trim() != hash)
            {
                Log("Скачиваю библиотеки. Первый раз это несколько минут...");
                int code = Run(venvPython, "-m pip install --disable-pip-version-check -r requirements.txt", root, null);
                if (code != 0)
                    throw new InvalidOperationException("Не удалось скачать библиотеки. Проверьте интернет и нажмите «Старт» ещё раз.");
                File.WriteAllText(marker, hash);
            }

            if (_stop) return;
            Log("");
            Log("Начинаю сбор. 1С должна оставаться открытой.");
            Log("");
            int runCode = Run(venvPython, "-u run_collector.py", root, tesseract);
            string table = Path.Combine(root, "output", "акты.xlsx");
            if (File.Exists(table))
            {
                Log("Таблица: " + table);
                try { Process.Start(table); } catch { }
            }
            if (_stop)
            {
                Log("Остановлено.");
                return;
            }
            if (runCode == 0)
            {
                Log("Готово.");
                return;
            }
            Log("Сбор остановился. Причина — в строке «Ошибка» выше.");
        }

        private static void ExtractPayload(string root)
        {
            string tempZip = Path.Combine(Path.GetTempPath(), "sbor-aktov-payload.zip");
            string tempDir = Path.Combine(Path.GetTempPath(), "sbor-aktov-payload");
            using (Stream stream = typeof(Program).Assembly.GetManifestResourceStream("payload.zip"))
            {
                if (stream == null)
                    throw new InvalidOperationException("В программе нет файлов сбора.");
                using (FileStream file = File.Create(tempZip))
                    stream.CopyTo(file);
            }
            if (Directory.Exists(tempDir))
                Directory.Delete(tempDir, true);
            ZipFile.ExtractToDirectory(tempZip, tempDir);
            CopyTree(tempDir, root);
        }

        private static void CopyTree(string from, string to)
        {
            Directory.CreateDirectory(to);
            foreach (string file in Directory.GetFiles(from))
                File.Copy(file, Path.Combine(to, Path.GetFileName(file)), true);
            foreach (string dir in Directory.GetDirectories(from))
                CopyTree(dir, Path.Combine(to, Path.GetFileName(dir)));
        }

        private string EnsurePython()
        {
            string found = FindPython();
            if (found != null)
                return found;
            Log("Ставлю Python. Если Windows спросит разрешение — нажмите Да.");
            RunWinget("install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements");
            found = FindPython();
            if (found == null)
                throw new InvalidOperationException("Python не установился. Нажмите «Старт» ещё раз и согласитесь, если Windows спросит разрешение.");
            return found;
        }

        private string EnsureTesseract(string root)
        {
            string exe = FindTesseract();
            if (exe == null)
            {
                Log("Ставлю чтение текста с экрана. Если Windows спросит разрешение — нажмите Да.");
                RunWinget("install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements");
                exe = FindTesseract();
            }
            if (exe == null)
                throw new InvalidOperationException("Чтение текста с экрана не установилось. Нажмите «Старт» ещё раз и согласитесь, если Windows спросит разрешение.");

            string install = Path.Combine(Path.GetDirectoryName(exe), "tessdata");
            string local = Path.Combine(root, "tessdata");
            Directory.CreateDirectory(local);
            foreach (string lang in new[] { "eng", "rus" })
            {
                string name = lang + ".traineddata";
                string dest = Path.Combine(local, name);
                if (TraineddataReady(dest))
                    continue;
                string src = Path.Combine(install, name);
                if (TraineddataReady(src))
                {
                    try
                    {
                        File.Copy(src, dest, true);
                    }
                    catch
                    {
                        // папка установки закрыта для чтения — скачаем ниже
                    }
                }
                if (TraineddataReady(dest))
                    continue;
                Log("Скачиваю язык " + lang + "...");
                try
                {
                    Download("https://github.com/tesseract-ocr/tessdata_fast/raw/main/" + name, dest);
                }
                catch (Exception ex)
                {
                    throw new InvalidOperationException(
                        "Не скачался файл языка " + lang + ". Проверьте интернет и нажмите «Старт» ещё раз. " + ex.Message);
                }
                if (!TraineddataReady(dest))
                    throw new InvalidOperationException("Файл языка " + lang + " скачался пустым. Нажмите «Старт» ещё раз.");
            }
            return exe;
        }

        private static bool TraineddataReady(string path)
        {
            try
            {
                return File.Exists(path) && new FileInfo(path).Length > 500000;
            }
            catch
            {
                return false;
            }
        }

        private static string FindPython()
        {
            string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
            string[] candidates = new[]
            {
                Path.Combine(local, "Programs", "Python", "Python312", "python.exe"),
                Path.Combine(local, "Programs", "Python", "Python313", "python.exe"),
                @"C:\Program Files\Python312\python.exe",
                @"C:\Program Files\Python313\python.exe"
            };
            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate))
                    return candidate;
            }
            string py = Which("py.exe");
            if (py != null)
            {
                string printed = RunCapture(py, "-3 -c \"import sys; print(sys.executable)\"");
                if (!string.IsNullOrWhiteSpace(printed) && File.Exists(printed.Trim()))
                    return printed.Trim();
            }
            string python = Which("python.exe");
            if (python != null && python.IndexOf("WindowsApps", StringComparison.OrdinalIgnoreCase) < 0)
                return python;
            return null;
        }

        private static string FindTesseract()
        {
            string[] candidates = new[]
            {
                @"C:\Program Files\Tesseract-OCR\tesseract.exe",
                @"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
            };
            foreach (string candidate in candidates)
            {
                if (File.Exists(candidate))
                    return candidate;
            }
            return Which("tesseract.exe");
        }

        private static string Which(string name)
        {
            string path = Environment.GetEnvironmentVariable("PATH") ?? "";
            foreach (string dir in path.Split(new[] { ';' }, StringSplitOptions.RemoveEmptyEntries))
            {
                try
                {
                    string full = Path.Combine(dir.Trim(), name);
                    if (File.Exists(full))
                        return full;
                }
                catch
                {
                    // skip broken PATH entries
                }
            }
            return null;
        }

        private void RunWinget(string arguments)
        {
            string winget = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Microsoft", "WindowsApps", "winget.exe");
            if (!File.Exists(winget))
                winget = Which("winget.exe");
            if (winget == null)
                throw new InvalidOperationException("На этом Windows нет установки программ (winget). Напишите в чат: нет winget.");
            var psi = new ProcessStartInfo
            {
                FileName = winget,
                Arguments = arguments,
                UseShellExecute = true
            };
            using (Process process = Process.Start(psi))
            {
                if (process == null)
                    throw new InvalidOperationException("Не удалось запустить установку.");
                Remember(process);
                process.WaitForExit();
                Forget(process);
            }
        }

        private int Run(string file, string arguments, string workdir, string tesseract)
        {
            var psi = new ProcessStartInfo
            {
                FileName = file,
                Arguments = arguments,
                WorkingDirectory = workdir,
                UseShellExecute = false,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                CreateNoWindow = true,
                StandardOutputEncoding = Encoding.UTF8,
                StandardErrorEncoding = Encoding.UTF8
            };
            psi.EnvironmentVariables["PYTHONIOENCODING"] = "utf-8";
            psi.EnvironmentVariables["PYTHONUTF8"] = "1";
            if (!string.IsNullOrEmpty(tesseract))
            {
                // Сборка Tesseract для Windows ищет eng.traineddata прямо в папке
                // из TESSDATA_PREFIX, если путь заканчивается на «\».
                string prefix = Path.Combine(workdir, "tessdata");
                if (!prefix.EndsWith("\\"))
                    prefix += "\\";
                psi.EnvironmentVariables["TESSDATA_PREFIX"] = prefix;
            }
            using (Process process = new Process())
            {
                process.StartInfo = psi;
                process.OutputDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        Log(e.Data);
                };
                process.ErrorDataReceived += delegate(object sender, DataReceivedEventArgs e)
                {
                    if (!string.IsNullOrEmpty(e.Data))
                        Log(e.Data);
                };
                if (!process.Start())
                    throw new InvalidOperationException("Не удалось запустить " + file);
                Remember(process);
                process.BeginOutputReadLine();
                process.BeginErrorReadLine();
                while (!process.WaitForExit(400))
                {
                    if (_stop && !process.HasExited)
                    {
                        try { process.Kill(); } catch { }
                        break;
                    }
                }
                process.WaitForExit();
                Forget(process);
                return process.ExitCode;
            }
        }

        private static string RunCapture(string file, string arguments)
        {
            try
            {
                var psi = new ProcessStartInfo
                {
                    FileName = file,
                    Arguments = arguments,
                    UseShellExecute = false,
                    RedirectStandardOutput = true,
                    CreateNoWindow = true
                };
                using (Process process = Process.Start(psi))
                {
                    if (process == null)
                        return null;
                    string text = process.StandardOutput.ReadToEnd();
                    process.WaitForExit(15000);
                    if (process.ExitCode != 0)
                        return null;
                    return text;
                }
            }
            catch
            {
                return null;
            }
        }

        private static void WriteTesseractPath(string configPath, string tesseract)
        {
            string json = File.ReadAllText(configPath, Encoding.UTF8);
            string escaped = tesseract.Replace("\\", "\\\\");
            json = Regex.Replace(json, "\"tesseract_cmd\"\\s*:\\s*\"[^\"]*\"", "\"tesseract_cmd\": \"" + escaped + "\"");
            File.WriteAllText(configPath, json, new UTF8Encoding(false));
        }

        private static void Download(string url, string destination)
        {
            using (var client = new WebClient())
            {
                client.Headers["User-Agent"] = "SborAktov";
                client.DownloadFile(url, destination);
            }
        }

        private static string Hash(string text)
        {
            using (SHA256 sha = SHA256.Create())
            {
                byte[] bytes = sha.ComputeHash(Encoding.UTF8.GetBytes(text));
                var builder = new StringBuilder(bytes.Length * 2);
                foreach (byte b in bytes)
                    builder.Append(b.ToString("x2"));
                return builder.ToString();
            }
        }

        private void Remember(Process process)
        {
            lock (_gate)
                _current = process;
        }

        private void Forget(Process process)
        {
            lock (_gate)
            {
                if (ReferenceEquals(_current, process))
                    _current = null;
            }
        }

        private void KillCurrent()
        {
            Process process;
            lock (_gate)
                process = _current;
            if (process == null)
                return;
            try
            {
                if (!process.HasExited)
                    process.Kill();
            }
            catch
            {
                // already gone
            }
        }

        private void Log(string line)
        {
            if (IsDisposed)
                return;
            if (InvokeRequired)
            {
                try { BeginInvoke((Action)delegate { Log(line); }); } catch { }
                return;
            }
            if (line != null && line.StartsWith("СЧЁТЧИК "))
            {
                _counter.Text = FormatCounter(line);
                return;
            }
            _log.AppendText((line ?? "") + Environment.NewLine);
        }

        private static string SavedCounterText()
        {
            string csv = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "Sbor-aktov-1C",
                "output",
                "акты.csv");
            int got = 0;
            int lost = 0;
            if (!File.Exists(csv))
                return "Собрано: 0    Не собрано: 0";
            foreach (string raw in File.ReadAllLines(csv))
            {
                if (string.IsNullOrWhiteSpace(raw) || raw.StartsWith("Акт;"))
                    continue;
                string[] parts = raw.Split(';');
                if (parts.Length == 0 || string.IsNullOrWhiteSpace(parts[0]))
                    continue;
                string phone = parts.Length > 4 ? parts[4] : "";
                if (PhoneCollected(phone))
                    got++;
                else
                    lost++;
            }
            return "Собрано: " + got + "    Не собрано: " + lost;
        }

        private static string FormatCounter(string line)
        {
            string got = Piece(line, "собрано=");
            string lost = Piece(line, "не собрано=");
            string left = Piece(line, "осталось=");
            return "Собрано: " + got + "    Не собрано: " + lost + "    Осталось: " + left;
        }

        private static string Piece(string line, string key)
        {
            int start = line.IndexOf(key, StringComparison.Ordinal);
            if (start < 0)
                return "0";
            start += key.Length;
            int end = line.IndexOf(' ', start);
            if (end < 0)
                end = line.Length;
            return line.Substring(start, end - start);
        }

        private static bool PhoneCollected(string raw)
        {
            var digits = new System.Text.StringBuilder();
            foreach (char ch in raw ?? "")
            {
                if (ch >= '0' && ch <= '9')
                    digits.Append(ch);
            }
            string text = digits.ToString();
            if (text.Length == 11 && (text[0] == '7' || text[0] == '8'))
                text = text.Substring(1);
            return text.Length == 10 && text[0] == '9';
        }
    }
}

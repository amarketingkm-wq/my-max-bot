@echo off
setlocal
set "START_BAT=%~f0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$p=Get-Content -LiteralPath $env:START_BAT -Raw -Encoding UTF8; $k='#__POWERSHELL__'; $i=$p.LastIndexOf($k); if ($i -lt 0) { throw 'bad launcher' }; Invoke-Expression $p.Substring($i + $k.Length)"
set "ERR=%ERRORLEVEL%"
if not "%ERR%"=="0" pause
exit /b %ERR%
#__POWERSHELL__
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
Add-Type -AssemblyName System.Windows.Forms | Out-Null

function Refresh-Path {
  $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $user = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machine;$user"
}

function Say([string]$text, [string]$buttons = "OK") {
  return [System.Windows.Forms.MessageBox]::Show($text, "Сбор актов 1С", $buttons, "Information")
}

function Find-PythonExe {
  Refresh-Path
  $candidates = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "C:\Program Files\Python312\python.exe",
    "C:\Program Files\Python313\python.exe"
  )
  foreach ($c in $candidates) {
    if (Test-Path -LiteralPath $c) { return $c }
  }
  $py = Get-Command py -ErrorAction SilentlyContinue
  if ($py) {
    $out = & py -3 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return ($out | Select-Object -Last 1).ToString().Trim() }
  }
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($python -and $python.Source -notmatch "WindowsApps") { return $python.Source }
  return $null
}

function Find-Tesseract {
  Refresh-Path
  foreach ($c in @(
    "C:\Program Files\Tesseract-OCR\tesseract.exe",
    "C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"
  )) {
    if (Test-Path -LiteralPath $c) { return $c }
  }
  $cmd = Get-Command tesseract -ErrorAction SilentlyContinue
  if ($cmd) { return $cmd.Source }
  return $null
}

function Find-Collector([string]$dir) {
  if (-not $dir -or -not (Test-Path -LiteralPath $dir)) { return $null }
  $direct = Join-Path $dir "run_collector.py"
  if (Test-Path -LiteralPath $direct) { return $dir }
  $sub = Join-Path $dir "1c-acts-collector\run_collector.py"
  if (Test-Path -LiteralPath $sub) { return (Split-Path -Parent $sub) }
  foreach ($d in @(Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue)) {
    $nested = Join-Path $d.FullName "1c-acts-collector\run_collector.py"
    if (Test-Path -LiteralPath $nested) { return (Split-Path -Parent $nested) }
  }
  return $null
}

function Ensure-Winget {
  if (Get-Command winget -ErrorAction SilentlyContinue) { return }
  throw "Не получилось поставить программы автоматически. Напишите в чат: нет winget."
}

try {
  $bat = $env:START_BAT
  if (-not $bat) { throw "Запускайте двойным щелчком по файлу Сбор-актов.bat" }
  $batDir = Split-Path -Parent $bat
  $installRoot = Join-Path $env:USERPROFILE "Desktop\Sbor-aktov-1C"

  $answer = Say "Отчёт 1С должен быть открыт на экране.`nКурсор в списке поставьте на первый акт.`n`nНажмите Да — дальше всё сделается само.`nОстановить потом: клавиша F9." "YesNo"
  if ($answer -ne [System.Windows.Forms.DialogResult]::Yes) { exit 0 }

  Write-Host "Подготовка. Первый раз может занять несколько минут."
  Write-Host "Окно 1С не закрывайте."

  $collector = Find-Collector $batDir
  if (-not $collector) { $collector = Find-Collector $installRoot }
  if (-not $collector) {
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    $zip = Join-Path $installRoot "src.zip"
    Write-Host "Скачиваю программу сбора..."
    Invoke-WebRequest -Uri "https://github.com/amarketingkm-wq/my-max-bot/archive/refs/heads/cursor/one-click-1c-start-0bcf.zip" -OutFile $zip
    Expand-Archive -LiteralPath $zip -DestinationPath $installRoot -Force
    $collector = Find-Collector $installRoot
  }
  if (-not $collector) { throw "Программа сбора не скачалась. Проверьте интернет и запустите файл ещё раз." }

  $shell = New-Object -ComObject WScript.Shell
  $lnkPath = Join-Path ([Environment]::GetFolderPath("Desktop")) "Сбор актов 1С.lnk"
  $lnk = $shell.CreateShortcut($lnkPath)
  $lnk.TargetPath = Join-Path $collector "Сбор-актов.bat"
  $lnk.WorkingDirectory = $collector
  $lnk.WindowStyle = 1
  $lnk.Description = "Сбор сумм и телефонов из актов 1С"
  $lnk.Save()

  $python = Find-PythonExe
  if (-not $python) {
    Ensure-Winget
    Write-Host "Ставлю Python..."
    Say "Если Windows спросит разрешение — нажмите Да. Это один раз." | Out-Null
    & winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
    $python = Find-PythonExe
  }
  if (-not $python) { throw "Python не установился. Запустите файл ещё раз и нажмите Да, если Windows спросит разрешение." }

  $tess = Find-Tesseract
  if (-not $tess) {
    Ensure-Winget
    Write-Host "Ставлю чтение текста с экрана..."
    & winget install -e --id UB-Mannheim.TesseractOCR --silent --accept-package-agreements --accept-source-agreements
    $tess = Find-Tesseract
  }
  if (-not $tess) { throw "Чтение текста с экрана не установилось. Запустите файл ещё раз и нажмите Да, если Windows спросит разрешение." }

  $tessdata = Join-Path (Split-Path -Parent $tess) "tessdata"
  $rus = Join-Path $tessdata "rus.traineddata"
  if (-not (Test-Path -LiteralPath $rus)) {
    Write-Host "Скачиваю русский язык для чтения экрана..."
    New-Item -ItemType Directory -Force -Path $tessdata | Out-Null
    Invoke-WebRequest -Uri "https://github.com/tesseract-ocr/tessdata_fast/raw/main/rus.traineddata" -OutFile $rus
  }

  $cfgPath = Join-Path $collector "config.json"
  $json = [IO.File]::ReadAllText($cfgPath)
  $esc = $tess.Replace("\", "\\")
  $json = [regex]::Replace($json, '"tesseract_cmd"\s*:\s*"[^"]*"', ('"tesseract_cmd": "' + $esc + '"'))
  $utf8 = New-Object System.Text.UTF8Encoding $false
  [IO.File]::WriteAllText($cfgPath, $json, $utf8)

  $venvPy = Join-Path $collector ".venv\Scripts\python.exe"
  if (-not (Test-Path -LiteralPath $venvPy)) {
    Write-Host "Ставлю библиотеки. Подождите..."
    & $python -m venv (Join-Path $collector ".venv")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось подготовить Python." }
  }
  & $venvPy -m pip install --disable-pip-version-check -r (Join-Path $collector "requirements.txt")
  if ($LASTEXITCODE -ne 0) { throw "Не удалось скачать библиотеки. Проверьте интернет и запустите файл ещё раз." }

  Write-Host ""
  Write-Host "Начинаю сбор. 1С не сворачивайте."
  Write-Host "F9 — остановить, F8 — пауза."
  Write-Host ""
  Set-Location -LiteralPath $collector
  & $venvPy -u "run_collector.py"
  $code = $LASTEXITCODE
  $csv = Join-Path $collector "output\acts_phones.csv"
  if ($code -eq 0 -and (Test-Path -LiteralPath $csv)) {
    Start-Process -FilePath $csv
    Say "Готово.`nТаблица с суммами и телефонами открыта." | Out-Null
    exit 0
  }
  Say "Сбор не завершился.`nЧерное окно не закрывайте: там написана причина.`nЧаще всего отчет 1С был закрыт или курсор стоял не на акте." | Out-Null
  Write-Host "Нажмите Enter, чтобы закрыть окно."
  [void](Read-Host)
  exit 1
}
catch {
  Say ("Не вышло.`n`n" + $_.Exception.Message) | Out-Null
  Write-Host $_.Exception.Message
  Write-Host "Нажмите Enter, чтобы закрыть окно."
  [void](Read-Host)
  exit 1
}

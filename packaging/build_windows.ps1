# Fabrique Pirouette.exe (PyInstaller) puis l'installateur Pirouette-<version>-Windows-Setup.exe (Inno Setup).
# À lancer sur un PC Windows (c'est ce que fait GitHub Actions), après : pip install -r requirements-desktop.txt
$ErrorActionPreference = 'Stop'
Set-Location (Join-Path $PSScriptRoot '..')

$Version = (python -c "import app; print(app.__version__)").Trim()

Write-Host "-> Icone"
python -c "from PIL import Image; Image.open('packaging/icon-1024.png').save('packaging/Pirouette.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"

Write-Host "-> Application"
python -m PyInstaller --noconfirm --clean packaging/Pirouette.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller a echoue" }

Write-Host "-> Installateur"
$Iscc = @("${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe", "$env:ProgramFiles\Inno Setup 6\ISCC.exe") | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $Iscc) {
  choco install innosetup -y --no-progress | Out-Null
  $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
}
& $Iscc "/DVersion=$Version" packaging\pirouette.iss
if ($LASTEXITCODE -ne 0) { throw "Inno Setup a echoue" }
Write-Host "OK dist\Pirouette-$Version-Windows-Setup.exe"

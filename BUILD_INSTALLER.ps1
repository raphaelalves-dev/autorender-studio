$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $project

$innoCandidates = @(
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)
$inno = $innoCandidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
if (-not $inno) { throw 'Inno Setup 6 ou 7 nao encontrado.' }

foreach ($required in @('bin\ffmpeg.exe', 'bin\ffprobe.exe', 'installer\settings.default.json', 'installer\LEIA-ME_INSTALACAO.md')) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Arquivo necessario ausente: $required" }
}

& py -m PyInstaller --noconfirm --clean AutoRenderPreset.spec
if ($LASTEXITCODE -ne 0) { throw 'Falha ao compilar o programa.' }
if (-not (Test-Path -LiteralPath 'dist\AutoRenderPreset\_internal\base_library.zip' -PathType Leaf)) {
    throw 'Programa compilado sem a pasta _internal completa.'
}

& $inno AutoRenderStudio_Setup.iss
if ($LASTEXITCODE -ne 0) { throw 'Falha ao gerar o instalador.' }

$installer = Join-Path $project 'output\AutoRenderStudio_Setup_1.2.8.exe'
if (-not (Test-Path -LiteralPath $installer -PathType Leaf)) { throw 'Instalador nao encontrado apos a compilacao.' }
$parts = @(Get-ChildItem -LiteralPath (Join-Path $project 'output') -Filter 'AutoRenderStudio_Setup_1.2.8*.bin' -File)
if ($parts.Count) { throw 'O instalador foi dividido em partes; revise o tamanho do preset e DiskSpanning.' }
Get-Item -LiteralPath $installer | Select-Object FullName, Length
Get-FileHash -LiteralPath $installer -Algorithm SHA256 | Select-Object Hash

param([switch]$SkipInstaller, [switch]$SkipDependencies, [switch]$OpenInstaller, [string]$OutputRoot)

$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$workspacePaths = $null
if ($SkipInstaller -and $OpenInstaller) { throw 'Opening the installer requires building it.' }
if (-not $OutputRoot) {
    $pathSettings = Join-Path $projectRoot '.paperplane-paths.json'
    if (Test-Path -LiteralPath $pathSettings) {
        $workspacePaths = Get-Content -LiteralPath $pathSettings -Raw | ConvertFrom-Json
        $OutputRoot = $workspacePaths.artifacts_dir
    }
    if (-not $OutputRoot) { $OutputRoot = $projectRoot }
}
if (-not [IO.Path]::IsPathRooted($OutputRoot)) { throw 'OutputRoot must be an absolute path.' }
$OutputRoot = [IO.Path]::GetFullPath($OutputRoot)
$distRoot = Join-Path $OutputRoot 'dist'
$buildRoot = Join-Path $OutputRoot 'build'
if ($workspacePaths -and $workspacePaths.releases_dir) { $distRoot = $workspacePaths.releases_dir }
if ($workspacePaths -and $workspacePaths.build_dir) { $buildRoot = $workspacePaths.build_dir }
foreach ($directory in @($distRoot,$buildRoot)) {
    if (-not [IO.Path]::IsPathRooted($directory)) { throw 'Build and release paths must be absolute.' }
}
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    py -3 -m venv (Join-Path $projectRoot '.venv')
}
if (-not $SkipDependencies) {
    & $python -c "import importlib.metadata,pathlib; lines=pathlib.Path(r'$projectRoot\requirements-desktop.txt').read_text().splitlines(); assert all(importlib.metadata.version(name.strip()) == number.strip() for name,number in (line.split('==') for line in lines if line.strip() and not line.lstrip().startswith('#')))" 2>$null
    if ($LASTEXITCODE -ne 0) {
        & $python -m pip install -r (Join-Path $projectRoot 'requirements-desktop.txt')
        if ($LASTEXITCODE -ne 0) { throw 'Desktop dependencies could not be installed.' }
    }
}
$desktopVersion = & $python -c "import runpy; print(runpy.run_path(r'$projectRoot\desktop_version.py')['VERSION'])"
if ($LASTEXITCODE -ne 0 -or $desktopVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Invalid desktop version.' }
& $python (Join-Path $PSScriptRoot 'make_desktop_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Desktop icon could not be generated.' }
Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean --windowed --onedir --name PaperPlaneStudio --distpath $distRoot --workpath $buildRoot --specpath $buildRoot --icon "$projectRoot\web\icon.ico" --add-data "$projectRoot\docs\visual-styles\aiclaw-warm-engineering-v1;visual-styles/aiclaw-warm-engineering-v1" --add-data "$projectRoot\web;web" --add-data "$projectRoot\library\web;library/web" --add-data "$projectRoot\skills;skills" --add-data "$projectRoot\agents;agents" --hidden-import studio.skills --hidden-import studio.skill_executors --hidden-import yaml "$projectRoot\desktop.py"
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller build failed.' }
    if (-not $SkipInstaller) {
        $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
        if (-not $iscc) {
            foreach ($candidate in @(
                (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
                (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
            )) {
                if (Test-Path $candidate) { $iscc = @{ Source = $candidate }; break }
            }
        }
        if (-not $iscc) { throw 'Inno Setup 6 (ISCC.exe) is required for the installer. The runnable folder is in dist\PaperPlaneStudio.' }
        $packageSource = Join-Path $distRoot 'PaperPlaneStudio'
        $packageOutput = Join-Path $distRoot 'installer'
        $helperDirectory = Join-Path $buildRoot 'upgrade-helper'
        New-Item -ItemType Directory -Path $helperDirectory -Force | Out-Null
        $helperLibrary = Join-Path $helperDirectory 'upgrade_paths.dll'
        $csharpCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
        & $csharpCompiler /nologo /target:library "/out:$helperLibrary" "$projectRoot\tools\upgrade_paths.cs"
        if ($LASTEXITCODE -ne 0) { throw 'Upgrade helper compilation failed.' }
        & $iscc.Source "/DAppVersion=$desktopVersion" "/DSourcePath=$packageSource" "/DOutputPath=$packageOutput" "/DUpgradeHelperPath=$helperLibrary" desktop-installer.iss
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
        $package = Join-Path $packageOutput "PaperPlaneStudio-Setup-$desktopVersion.exe"
        $stream = [IO.File]::OpenRead($package)
        $algorithm = [Security.Cryptography.SHA256]::Create()
        try { $checksum = [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-','').ToLowerInvariant() }
        finally { $stream.Dispose(); $algorithm.Dispose() }
        [IO.File]::WriteAllText("$package.sha256", "$checksum  $([IO.Path]::GetFileName($package))`n", [Text.Encoding]::ASCII)
    }
} finally {
    Pop-Location
}
if ($OpenInstaller) {
    # Interactive wizard: the owner confirms installation. Never use silent flags.
    Start-Process -FilePath $package
}

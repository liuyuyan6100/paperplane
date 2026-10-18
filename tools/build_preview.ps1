param([switch]$SkipDependencies, [switch]$OpenInstaller, [string]$OutputRoot)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $OutputRoot) { $OutputRoot = $projectRoot }
if (-not [IO.Path]::IsPathRooted($OutputRoot)) { throw 'OutputRoot must be absolute.' }
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    & python -m venv (Join-Path $projectRoot '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'Python virtual environment creation failed.' }
}
if (-not $SkipDependencies) {
    & $python -m pip install -r (Join-Path $projectRoot 'requirements-preview.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Preview dependencies could not be installed.' }
}
Push-Location (Join-Path $projectRoot 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependencies failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
& $python (Join-Path $PSScriptRoot 'make_desktop_icon.py')
if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
$distRoot = Join-Path $OutputRoot 'dist'
$buildRoot = Join-Path $OutputRoot 'build'
Push-Location $projectRoot
try {
    & $python -m PyInstaller --noconfirm --clean --windowed --onedir --name PaperPlaneStudioPreview --distpath $distRoot --workpath $buildRoot --specpath $buildRoot --icon "$projectRoot\web\icon.ico" --add-data "$projectRoot\frontend\dist;frontend/dist" --add-data "$projectRoot\docs\visual-styles\aiclaw-warm-engineering-v1;visual-styles/aiclaw-warm-engineering-v1" --add-data "$projectRoot\web;web" --add-data "$projectRoot\library\web;library/web" --add-data "$projectRoot\skills;skills" --add-data "$projectRoot\agents;agents" --hidden-import studio.skills --hidden-import studio.skill_executors --hidden-import yaml --hidden-import uvicorn.logging --hidden-import uvicorn.loops.asyncio --hidden-import uvicorn.protocols.http.h11_impl --hidden-import uvicorn.lifespan.on "$projectRoot\preview.py"
    if ($LASTEXITCODE -ne 0) { throw 'Preview packaging failed.' }
    $iscc = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if (-not $iscc) {
        foreach ($candidate in @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),(Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe'))) {
            if (Test-Path -LiteralPath $candidate) { $iscc = @{ Source = $candidate }; break }
        }
    }
    if (-not $iscc) { throw 'Inno Setup 6 is required.' }
    $packageSource = Join-Path $distRoot 'PaperPlaneStudioPreview'
    $packageOutput = Join-Path $distRoot 'preview-installer'
    $helperDirectory = Join-Path $buildRoot 'upgrade-helper'
    New-Item -ItemType Directory -Path $helperDirectory -Force | Out-Null
    $helperLibrary = Join-Path $helperDirectory 'upgrade_paths.dll'
    $csharpCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
    & $csharpCompiler /nologo /target:library "/out:$helperLibrary" "$projectRoot\tools\upgrade_paths.cs"
    if ($LASTEXITCODE -ne 0) { throw 'Upgrade helper compilation failed.' }
    & $iscc.Source "/DSourcePath=$packageSource" "/DOutputPath=$packageOutput" "/DUpgradeHelperPath=$helperLibrary" preview-installer.iss
    if ($LASTEXITCODE -ne 0) { throw 'Preview installer build failed.' }
    $package = Join-Path $packageOutput 'PaperPlaneStudioPreview-Setup-0.2.0.2.exe'
    $checksum = (Get-FileHash -LiteralPath $package -Algorithm SHA256).Hash.ToLowerInvariant()
    [IO.File]::WriteAllText("$package.sha256", "$checksum  $([IO.Path]::GetFileName($package))`n", [Text.Encoding]::ASCII)
    Write-Output $package
} finally { Pop-Location }
if ($OpenInstaller) { Start-Process -FilePath $package }

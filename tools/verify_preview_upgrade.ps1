param([Parameter(Mandatory=$true)][string]$Package, [Parameter(Mandatory=$true)][string]$HelperLibrary)
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$scratch = Join-Path $projectRoot ('test-results\m2-upgrade-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $scratch | Out-Null
$helper = Join-Path $scratch 'helper'
New-Item -ItemType Directory -Path $helper | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'prepare_desktop_upgrade.ps1') -Destination $helper
Copy-Item -LiteralPath $HelperLibrary -Destination (Join-Path $helper 'upgrade_paths.dll')
$launched = @()
$markers = @()
$untouched = @(Get-Process -Name PaperPlaneStudio,PaperPlaneStudioPreview -ErrorAction SilentlyContinue)
try {
    foreach ($name in @('target','other')) {
        $install = Join-Path $scratch $name
        Copy-Item -LiteralPath $Package -Destination $install -Recurse
        $data = Join-Path $scratch ($name + '-data')
        New-Item -ItemType Directory -Path $data | Out-Null
        Set-Content -LiteralPath (Join-Path $data 'preserved.txt') -Value 'synthetic data remains'
        $process = Start-Process -FilePath (Join-Path $install 'PaperPlaneStudioPreview.exe') -ArgumentList @('--serve','--data-dir', ('"' + $data + '"')) -WindowStyle Hidden -PassThru
        $launched += $process
        $marker = Join-Path $data 'preview-runtime.json'
        $deadline = (Get-Date).AddSeconds(40)
        while (-not (Test-Path -LiteralPath $marker)) {
            if ($process.HasExited -or (Get-Date) -gt $deadline) { throw 'Synthetic preview did not start.' }
            Start-Sleep -Milliseconds 100
        }
        $markers += (Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json)
    }
    & powershell.exe -NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File (Join-Path $helper 'prepare_desktop_upgrade.ps1') -InstallDir (Join-Path $scratch 'target') -ExecutableName PaperPlaneStudioPreview
    if ($LASTEXITCODE -ne 0) { throw 'Upgrade helper failed.' }
    if (Get-Process -Id $markers[0].pid -ErrorAction SilentlyContinue) { throw 'Target did not exit.' }
    if (-not (Get-Process -Id $markers[1].pid -ErrorAction SilentlyContinue)) { throw 'Other preview was incorrectly stopped.' }
    foreach ($process in $untouched) { if ($process.HasExited) { throw 'An existing app unexpectedly exited.' } }
    foreach ($name in @('target','other')) {
        if ((Get-Content -LiteralPath (Join-Path $scratch "$name-data\preserved.txt") -Raw).Trim() -ne 'synthetic data remains') { throw 'Synthetic data changed.' }
    }
    # A data directory must be refused before closing any process or cleaning files.
    $refusal = Start-Process powershell.exe -ArgumentList @('-NoLogo','-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',('"' + (Join-Path $helper 'prepare_desktop_upgrade.ps1') + '"'),'-InstallDir',('"' + (Join-Path $scratch 'other-data') + '"'),'-ExecutableName','PaperPlaneStudioPreview') -WindowStyle Hidden -Wait -PassThru -RedirectStandardError (Join-Path $scratch 'expected-refusal.txt')
    if ($refusal.ExitCode -eq 0) { throw 'A data directory was accepted.' }
    $refusal.Dispose()
    if (-not (Get-Process -Id $markers[1].pid -ErrorAction SilentlyContinue)) { throw 'Refusal stopped another process.' }
    @{passed=$true;target_exited=$true;other_preview_alive=$true;existing_apps_untouched=$true;data_preserved=$true;data_target_rejected=$true;scratch=$scratch} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $projectRoot 'test-results\m2-upgrade-result.json') -Encoding utf8
} finally {
    # Only the synthetic processes launched by this check are cleaned up.
    foreach ($marker in $markers) { Stop-Process -Id $marker.pid -Force -ErrorAction SilentlyContinue }
    foreach ($process in $launched) { if (-not $process.HasExited) { $process.Kill(); $null = $process.WaitForExit(10000) }; $process.Dispose() }
    foreach ($process in $untouched) { $process.Dispose() }
}

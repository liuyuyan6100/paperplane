param([Parameter(Mandatory=$true)][string]$InstallDir, [ValidateSet('PaperPlaneStudio','PaperPlaneStudioPreview')][string]$ExecutableName = 'PaperPlaneStudio')

# Bundled installer helper. Never terminate by image name alone or touch user data.
$ErrorActionPreference = 'Stop'
try {
    $targetDir = [IO.Path]::GetFullPath($InstallDir).TrimEnd('\')
    if (-not [IO.Path]::IsPathRooted($InstallDir) -or $targetDir -eq [IO.Path]::GetPathRoot($targetDir).TrimEnd('\')) {
        throw 'The installation directory must be an absolute non-root path.'
    }
    if (-not (Test-Path -LiteralPath $targetDir)) { exit 0 }
    $targetItem = Get-Item -LiteralPath $targetDir -Force
    if (-not $targetItem.PSIsContainer -or ($targetItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'The installation directory cannot be a reparse point.'
    }
    # Data directories cannot be selected as an application installation target.
    foreach ($marker in @('studio.sqlite3','private.json','desktop.json','local','browser')) {
        if (Test-Path -LiteralPath (Join-Path $targetDir $marker)) { throw 'The selected directory contains user data.' }
    }
    $ownedExe = Join-Path $targetDir "$ExecutableName.exe"
    $ownedRuntime = Join-Path $targetDir '_internal'
    if ((Test-Path -LiteralPath $ownedRuntime) -and -not (Test-Path -LiteralPath $ownedExe)) {
        throw 'Runtime exists without the application identity marker.'
    }
    foreach ($ownedPath in @($ownedExe,$ownedRuntime)) {
        if ([IO.Path]::GetDirectoryName([IO.Path]::GetFullPath($ownedPath)) -ne $targetDir) { throw 'Unsafe cleanup target.' }
        if (Test-Path -LiteralPath $ownedPath) {
            if ((Get-Item -LiteralPath $ownedPath -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw 'An application file or runtime is a reparse point.'
            }
        }
    }
    # Walk without following directory links. Reject before closing anything.
    $ownedFiles = [Collections.Generic.List[string]]::new()
    if (Test-Path -LiteralPath $ownedExe) { $ownedFiles.Add($ownedExe) }
    if (Test-Path -LiteralPath $ownedRuntime) {
        $pendingDirs = [Collections.Generic.Stack[string]]::new()
        $pendingDirs.Push($ownedRuntime)
        while ($pendingDirs.Count -gt 0) {
            foreach ($entry in Get-ChildItem -LiteralPath $pendingDirs.Pop() -Force) {
                if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Runtime contains a reparse point.' }
                if ($entry.PSIsContainer) { $pendingDirs.Push($entry.FullName) } else { $ownedFiles.Add($entry.FullName) }
            }
        }
    }
    # Resolve aliases in ancestor paths (for example C:\Users and D:\Users).
    Add-Type -Path (Join-Path $PSScriptRoot 'upgrade_paths.dll')
    if (Test-Path -LiteralPath $ownedExe) {
        $expectedExe = [UpgradePaths]::Final($ownedExe)
        for ($attempt=0; $attempt -lt 3; $attempt++) {
            $roots = @()
            foreach ($candidate in @(Get-Process -Name $ExecutableName -ErrorAction SilentlyContinue)) {
                try {
                    # Hold the process handle before reading identity or terminating,
                    # so PID reuse cannot redirect Kill() to an unrelated process.
                    $null = $candidate.Handle
                    if ([UpgradePaths]::Final([UpgradePaths]::Image($candidate.Handle)) -eq $expectedExe) { $roots += $candidate }
                    else { $candidate.Dispose() }
                } catch {
                    $gone = $false
                    try { $gone = $candidate.HasExited } catch { }
                    $candidate.Dispose()
                    if (-not $gone) { throw 'Cannot verify a running PaperPlane process.' }
                }
            }
            if ($roots.Count -eq 0) { break }
            $snapshot = @(Get-CimInstance Win32_Process)
            $children = @()
            $parentBirth = @{}
            $seen = [Collections.Generic.HashSet[int]]::new()
            foreach ($process in $roots) { $parentBirth[$process.Id]=$process.StartTime; $null=$seen.Add($process.Id) }
            while ($parentBirth.Count -gt 0) {
                $next = @($snapshot | Where-Object {
                    $parentBirth.ContainsKey([int]$_.ParentProcessId) -and
                    $_.CreationDate -ge $parentBirth[[int]$_.ParentProcessId] -and
                    -not $seen.Contains([int]$_.ProcessId)
                })
                $parentBirth = @{}
                foreach ($row in $next) {
                    $null=$seen.Add([int]$row.ProcessId)
                    $parentBirth[[int]$row.ProcessId]=$row.CreationDate
                    try {
                        $child = [Diagnostics.Process]::GetProcessById($row.ProcessId)
                        $null = $child.Handle
                        if ([Math]::Abs(($child.StartTime.ToUniversalTime() - $row.CreationDate.ToUniversalTime()).TotalSeconds) -lt 1) { $children += $child }
                        else { $child.Dispose() }
                    } catch [ArgumentException] { } # Already exited.
                }
            }
            # Stop roots first to prevent spawning more children, then WebView/helper descendants.
            foreach ($process in @($roots) + @($children)) {
                try {
                    if (-not $process.HasExited) { $process.Kill() }
                    if (-not $process.WaitForExit(10000)) { throw 'An old process did not exit.' }
                } finally { $process.Dispose() }
            }
        }
    }
    # Detect remaining file locks before Inno removes any old runtime files.
    foreach ($file in $ownedFiles) {
        $stream = [IO.File]::Open($file, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::None)
        $stream.Dispose()
    }
    Write-Output 'Old application stopped; exact cleanup targets validated; user data untouched.'
    exit 0
} catch {
    Write-Error -ErrorAction Continue ('Upgrade preparation stopped: ' + $_.Exception.Message)
    exit 1
}

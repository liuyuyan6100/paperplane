param([Parameter(Mandatory = $true)][string]$Installer)
$ErrorActionPreference = 'Stop'
$resolvedInstaller = (Resolve-Path -LiteralPath $Installer).Path
if ([IO.Path]::GetFileName($resolvedInstaller) -notlike 'PaperPlaneStudioPreview-Setup-*.exe') {
    throw 'Choose the verified preview installer.'
}
# A packaged development host can redirect AppData for inherited child processes.
# Launch the ordinary, interactive installer through the Windows process provider,
# so its post-install application sees the same data as the desktop shortcut.
$launch = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
    CommandLine = ('"' + $resolvedInstaller + '"')
}
if ($launch.ReturnValue -ne 0) {
    throw ('Could not open the normal preview installer: ' + $launch.ReturnValue)
}
[pscustomobject]@{ Installer = $resolvedInstaller; ProcessId = $launch.ProcessId; ReturnValue = $launch.ReturnValue }

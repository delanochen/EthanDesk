# 把 Windows 端打成免安装的单文件 exe：EthanDesk.exe（主控端）/ DeskAgent.exe（被控端）。
#
# 用法（在仓库根目录的 PowerShell 里）：
#   .\packaging\build_windows.ps1              # 两个都打
#   .\packaging\build_windows.ps1 -Target viewer
#
# 产物在 dist\ 下，拷走即可运行，不需要装 Python，也不需要安装程序。

param(
    [ValidateSet('both', 'viewer', 'agent')]
    [string]$Target = 'both'
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) {
    throw "找不到 .venv\Scripts\python.exe，先在仓库根目录建虚拟环境"
}

if (-not (Test-Path -LiteralPath (Join-Path $root 'assets\ethandesk.ico'))) {
    Write-Host '生成图标…'
    & $python (Join-Path $root 'packaging\make_icon.py')
}

$common = @(
    '--noconfirm',
    '--clean',
    '--onefile',
    '--noconsole',
    '--icon', 'assets\ethandesk.ico',
    '--add-data', 'assets;assets',
    '--add-data', 'VERSION;.',
    '--collect-all', 'pystray',
    '--hidden-import', 'pystray._win32',
    '--distpath', 'dist'
)

if ($Target -in @('both', 'viewer')) {
    Write-Host '打包主控端 EthanDesk.exe…'
    & $python -m PyInstaller @common --name EthanDesk launcher.py
}

if ($Target -in @('both', 'agent')) {
    Write-Host '打包被控端 DeskAgent.exe…'
    & $python -m PyInstaller @common --name DeskAgent agent_launcher.py
}

Get-ChildItem -LiteralPath (Join-Path $root 'dist') -Filter *.exe |
    ForEach-Object { '{0}  {1:N1} MB' -f $_.FullName, ($_.Length / 1MB) }

# 一键把这个仓库推送到你自己的 Git 仓库
#
# 用法：
#   1. 先在 GitHub/Gitee 建一个空仓库（不要勾选 README）
#   2. 在本目录打开 PowerShell，执行：
#        .\scripts\push.ps1 -Remote https://github.com/你的名字/miku-bot.git
#   3. 首次会弹窗让你登录
#
# 如果要推到 Gitee，把地址换成 gitee 的即可。

param(
    [Parameter(Mandatory = $true)]
    [string]$Remote,
    [string]$Branch = "main",
    [string]$Message = "feat: 初音机器人套件（插件 + 桌宠 + 音色脚本）"
)

$ErrorActionPreference = "Stop"

Write-Host "== 检查 git ==" -ForegroundColor Cyan
git --version

if (-not (Test-Path ".git")) {
    Write-Host "== 初始化仓库 ==" -ForegroundColor Cyan
    git init
    git checkout -b $Branch 2>$null
} else {
    Write-Host "== 已有仓库，跳过 init ==" -ForegroundColor Cyan
}

Write-Host "== 检查敏感信息 ==" -ForegroundColor Cyan
$pats = @(
    'sk-[a-zA-Z0-9]{20,}'
)
# 额外的检测值（自己的 QQ 号、token 片段等）不要写进仓库：
# 用环境变量 LEAK_MARKERS 传入，逗号分隔，运行时自动并入检测列表
if ($env:LEAK_MARKERS) {
    $pats += ($env:LEAK_MARKERS -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ })
}
$hits = 0
Get-ChildItem -Recurse -File | Where-Object {
    $_.FullName -notmatch '\\\.git\\' -and
    $_.Extension -in @('.py', '.md', '.json', '.yaml', '.yml', '.cmd', '.ps1', '.txt', '.example')
} | ForEach-Object {
    foreach ($p in $pats) {
        $m = Select-String -Path $_.FullName -Pattern $p -ErrorAction SilentlyContinue
        if ($m) {
            Write-Host ("  命中 " + $p + " -> " + $_.Name) -ForegroundColor Yellow
            $script:hits++
        }
    }
}
if ($hits -gt 0) {
    Write-Host "== 发现 $hits 处敏感信息，已中止 ==" -ForegroundColor Red
    Write-Host "请先清理，或确认无误后注释掉这段检查" -ForegroundColor Red
    exit 1
}
Write-Host "   干净" -ForegroundColor Green

Write-Host "== 提交 ==" -ForegroundColor Cyan
git add -A
git commit -m $Message

Write-Host "== 推送 ==" -ForegroundColor Cyan
$existing = git remote 2>$null
if ($existing -contains "origin") {
    git remote set-url origin $Remote
} else {
    git remote add origin $Remote
}
git push -u origin $Branch

Write-Host "== 完成 ==" -ForegroundColor Green
Write-Host "打开你的仓库看看：$Remote"

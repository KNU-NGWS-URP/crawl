param(
    [ValidateSet('Analyze','Probe','Tests')][string]$Mode = 'Analyze',
    [string]$Settings = "$PSScriptRoot/settings.local.json",
    [switch]$AllowNetwork,
    [int]$Limit = 10,
    [ValidateRange(0,2147483647)][int]$Offset = 0,
    [ValidateRange(1,3)][int]$Repeat = 2,
    [string]$Members,
    [switch]$PreflightOnly,
    [ValidateSet('normal','strict')][string]$LoadPolicy = 'normal',
    [string]$ProbeResults,
    [string]$CompareWith
)
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath $Settings)) { throw 'settings.example.json을 settings.local.json으로 복사하고 경로를 지정하세요.' }
$taskConfig = Get-Content -LiteralPath $Settings -Raw | ConvertFrom-Json
$taskPython = $taskConfig.python
$taskNode = $taskConfig.node
if (-not $taskPython) { $taskPython = (Get-Command python -ErrorAction Stop).Source }
if (-not $taskNode) { $taskNode = (Get-Command node -ErrorAction Stop).Source }
if ($taskConfig.playwright_root) { $env:PILOT_PLAYWRIGHT_ROOT = $taskConfig.playwright_root }
Push-Location -LiteralPath $PSScriptRoot
try {
    if ($Mode -eq 'Analyze') {
        $taskArgs = @('-m','pilotcheck','--data',$taskConfig.data,'--out',$taskConfig.output)
        if ($taskConfig.baseline) { $taskArgs += @('--baseline',$taskConfig.baseline) }
        if ($taskConfig.review_ledger) { $taskArgs += @('--review-ledger',$taskConfig.review_ledger) }
        if ($taskConfig.observations) { $taskArgs += @('--observations',$taskConfig.observations) }
        if ($taskConfig.no_html) { $taskArgs += '--no-html' }
        if ($ProbeResults) { $taskArgs += @('--probe-results',$ProbeResults) }
        if ($CompareWith) { $taskArgs += @('--compare-with',$CompareWith) }
        & $taskPython @taskArgs
        if ($LASTEXITCODE -ne 0) { throw "분류기 종료 코드: $LASTEXITCODE" }
    } elseif ($Mode -eq 'Probe') {
        $taskArgs = @('probe.mjs','--plan',(Join-Path $taskConfig.output 'probe_plan.json'),'--out',(Join-Path $taskConfig.output 'probes'),'--limit',"$Limit",'--offset',"$Offset",'--repeat',"$Repeat")
        if ($AllowNetwork) { $taskArgs += '--allow-network' }
        if ($Members) { $taskArgs += @('--members',$Members) }
        if ($PreflightOnly) { $taskArgs += '--preflight-only' }
        $taskArgs += @('--load-policy',$LoadPolicy)
        if ($taskConfig.channel) { $taskArgs += @('--channel',$taskConfig.channel) }
        & $taskNode @taskArgs
        if ($LASTEXITCODE -ne 0) { throw "검사기 종료 코드: $LASTEXITCODE" }
    } else {
        & $taskPython -m unittest discover -s tests -p 'test_*.py' -v
        if ($LASTEXITCODE -ne 0) { throw 'Python 회귀 검사 실패' }
        $env:PILOT_BROWSER_CHANNEL = $taskConfig.channel
        & $taskNode --test tests/probe.test.mjs
        if ($LASTEXITCODE -ne 0) { throw '로컬 브라우저 회귀 검사 실패' }
    }
} finally { Pop-Location }

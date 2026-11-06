<#
.SYNOPSIS
    E2E-S4.0A validation runner for VS Code / PowerShell (Windows PowerShell 5.1 or PowerShell 7).

.DESCRIPTION
    Runs, in order, the requested validation levels:

      Unit    Full offline regression (pytest, JUnit XML).
      Replay  CACHE_ONLY resume of a persisted root S4.0A run inside an isolated
              sandbox copy of the state database, with outbound sockets blocked.
      Live    Real bounded candidate replenishment (Yahoo + local Ollama) inside
              an isolated sandbox copy of the state database.

    The production database data\state\portfolio_cio.db is only opened read-only
    and its SHA-256 is verified unchanged. No broker order, portfolio mutation or
    automatic execution is possible: the frozen Stage 4 contracts reject them.

    Results are written to data\cache\e2e\stage4\validation\<UTC stamp>\
    (git-ignored). Share summary.md, and the logs if a step fails.

.EXAMPLE
    .\scripts\Invoke-Stage4Validation.ps1
    # Unit + Replay (offline, about one minute)

.EXAMPLE
    .\scripts\Invoke-Stage4Validation.ps1 -Level All -MaxWaves 1
    # Unit + Replay + one LIVE replenishment wave (needs Ollama and internet)
#>
[CmdletBinding()]
param(
    [ValidateSet('Unit', 'Replay', 'Live', 'All')]
    [string[]] $Level = @('Unit', 'Replay'),
    [string] $Python,
    [string] $RunId,
    [ValidateRange(1, 5)] [int] $MaxWaves = 1,
    [ValidateRange(2, 8)] [int] $MaxHypotheses = 4,
    [string] $Model = 'qwen3:8b',
    [ValidateRange(5, 600)] [int] $LiveTimeoutMinutes = 90,
    [string] $WorkRoot,
    [switch] $ForceLive,
    [switch] $NoShadowLedger
)


$ErrorActionPreference = 'Stop'

function Join-Parts {
    param([Parameter(Mandatory)] [string[]] $Parts)
    return [System.IO.Path]::Combine([string[]] $Parts)
}

function Invoke-Logged {
    param(
        [Parameter(Mandatory)] [string] $Exe,
        [Parameter(Mandatory)] [string[]] $Arguments,
        [Parameter(Mandatory)] [string] $LogPath
    )
    "$ $Exe $($Arguments -join ' ')" | Out-File -FilePath $LogPath -Encoding utf8
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Exe @Arguments 2>&1 | ForEach-Object {
            $line = "$_"
            Add-Content -Path $LogPath -Value $line -Encoding utf8
            Write-Host $line
        }
        return $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previous
    }
}

function Read-JsonFile {
    param([string] $Path)
    if (-not (Test-Path -LiteralPath $Path)) { return $null }
    return (Get-Content -LiteralPath $Path -Raw -Encoding utf8 | ConvertFrom-Json)
}

function Get-FailedChecks {
    param($Result)
    if ($null -eq $Result -or $null -eq $Result.checks) { return @() }
    return @($Result.checks | Where-Object { -not $_.passed })
}

$RepoRoot = Split-Path -Parent $PSScriptRoot
if ($Level -contains 'All') { $Level = @('Unit', 'Replay', 'Live') }

if (-not $Python) {
    foreach ($candidate in @(
            (Join-Parts @($RepoRoot, '.venv', 'Scripts', 'python.exe')),
            (Join-Parts @($RepoRoot, 'venv', 'Scripts', 'python.exe')),
            (Join-Parts @($RepoRoot, '.venv', 'bin', 'python')))) {
        if (Test-Path -LiteralPath $candidate) { $Python = $candidate; break }
    }
    if (-not $Python) { $Python = 'python' }
}

$Stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$ResultDir = Join-Parts @($RepoRoot, 'data', 'cache', 'e2e', 'stage4', 'validation', $Stamp)
if (-not $WorkRoot) {
    # Sandboxes live outside the repository so that OneDrive never syncs SQLite writes.
    $WorkRoot = Join-Parts @([System.IO.Path]::GetTempPath(), 'agentic_portfolio_validation', $Stamp)
}
New-Item -ItemType Directory -Force -Path $ResultDir, $WorkRoot | Out-Null

$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUTF8 = '1'
Push-Location $RepoRoot
Start-Transcript -Path (Join-Parts @($ResultDir, 'transcript.txt')) | Out-Null

$steps = [ordered]@{}
try {
    Write-Host "== E2E-S4.0A validation $Stamp ==" -ForegroundColor Cyan
    Write-Host "Repository : $RepoRoot"
    Write-Host "Python     : $Python"
    Write-Host "Levels     : $($Level -join ', ')"
    Write-Host "Sandboxes  : $WorkRoot"
    Write-Host "Results    : $ResultDir"

    # --- Preflight -----------------------------------------------------------
    $preflightJson = Join-Parts @($ResultDir, 'preflight.json')
    $preflightArgs = @('-m', 'tools.stage4_validation', 'preflight', '--model', $Model, '--out', $preflightJson)
    if ($RunId) { $preflightArgs += @('--run-id', $RunId) }
    if (-not ($Level -contains 'Live')) { $preflightArgs += '--skip-yahoo' }
    Write-Host "`n-- Preflight" -ForegroundColor Cyan
    Invoke-Logged -Exe $Python -Arguments $preflightArgs -LogPath (Join-Parts @($ResultDir, 'preflight.log')) | Out-Null
    $preflight = Read-JsonFile $preflightJson
    $steps['preflight'] = $preflight

    # --- Unit ---------------------------------------------------------------
    if ($Level -contains 'Unit') {
        Write-Host "`n-- Unit: full offline regression" -ForegroundColor Cyan
        $junit = Join-Parts @($ResultDir, 'junit.xml')
        $code = Invoke-Logged -Exe $Python -LogPath (Join-Parts @($ResultDir, 'pytest.log')) -Arguments @(
            '-m', 'pytest', '-q', '-p', 'no:cacheprovider', "--junitxml=$junit")
        $unit = [ordered]@{ step = 'unit'; exit_code = $code; verdict = 'FAIL' }
        if (Test-Path -LiteralPath $junit) {
            [xml] $xml = Get-Content -LiteralPath $junit -Raw -Encoding utf8
            $suite = $xml.testsuites.testsuite
            $unit.tests = [int] $suite.tests
            $unit.failures = [int] $suite.failures
            $unit.errors = [int] $suite.errors
            $unit.skipped = [int] $suite.skipped
            $unit.seconds = [double] $suite.time
        }
        $summaryLine = Select-String -Path (Join-Parts @($ResultDir, 'pytest.log')) -Pattern '\d+ passed' | Select-Object -Last 1
        if ($summaryLine) { $unit.pytest_summary = $summaryLine.Line.Trim() }
        if ($code -eq 0) { $unit.verdict = 'PASS' }
        $steps['unit'] = [pscustomobject] $unit
    }

    # --- Replay -------------------------------------------------------------
    if ($Level -contains 'Replay') {
        Write-Host "`n-- Replay: CACHE_ONLY resume in sandbox, network blocked" -ForegroundColor Cyan
        $replayJson = Join-Parts @($ResultDir, 'replay.json')
        $replayArgs = @('-m', 'tools.stage4_validation', 'replay',
            '--workdir', (Join-Parts @($WorkRoot, 'replay')), '--out', $replayJson)
        if ($RunId) { $replayArgs += @('--run-id', $RunId) }
        Invoke-Logged -Exe $Python -Arguments $replayArgs -LogPath (Join-Parts @($ResultDir, 'replay.log')) | Out-Null
        $steps['replay'] = Read-JsonFile $replayJson
    }

    # --- Live ---------------------------------------------------------------
    if ($Level -contains 'Live') {
        Write-Host "`n-- Live: bounded replenishment ($MaxWaves wave(s)) in sandbox" -ForegroundColor Cyan
        if ($preflight -and -not $preflight.live_ready -and -not $ForceLive) {
            Write-Warning 'Live prerequisites not satisfied (see preflight live_checks). Use -ForceLive to run anyway.'
            $steps['live'] = [pscustomobject] @{ step = 'live'; verdict = 'SKIPPED'; reason = 'LIVE_PREREQUISITES_NOT_READY' }
        }
        else {
            $liveJson = Join-Parts @($ResultDir, 'live.json')
            $liveArgs = @('-m', 'tools.stage4_validation', 'live',
                '--workdir', (Join-Parts @($WorkRoot, 'live')),
                '--model', $Model,
                '--max-waves', "$MaxWaves",
                '--max-hypotheses', "$MaxHypotheses",
                '--timeout-seconds', "$($LiveTimeoutMinutes * 60)",
                '--log', (Join-Parts @($ResultDir, 'live_cli.log')),
                '--out', $liveJson)
            if ($RunId) { $liveArgs += @('--run-id', $RunId) }
            if ($NoShadowLedger) { $liveArgs += '--no-shadow-ledger' }
            Invoke-Logged -Exe $Python -Arguments $liveArgs -LogPath (Join-Parts @($ResultDir, 'live.log')) | Out-Null
            $steps['live'] = Read-JsonFile $liveJson
        }
    }
}
finally {
    # --- Summary ------------------------------------------------------------
    $md = New-Object System.Collections.Generic.List[string]
    $md.Add("# E2E-S4.0A validation $Stamp")
    $md.Add('')
    if ($preflight) {
        $md.Add("- Branch: ``$($preflight.git_branch)`` commit ``$($preflight.git_commit)`` dirty=$($preflight.git_dirty)")
        $md.Add("- Platform: $($preflight.platform)")
        foreach ($warning in @($preflight.warnings)) { if ($warning) { $md.Add("- WARNING: $warning") } }
    }
    $md.Add('')
    $md.Add('| Step | Verdict | Detail |')
    $md.Add('| --- | --- | --- |')
    $overall = $true
    foreach ($name in $steps.Keys) {
        $result = $steps[$name]
        if ($null -eq $result) { $md.Add("| $name | FAIL | no result file |"); $overall = $false; continue }
        $verdict = [string] $result.verdict
        if ($verdict -eq 'FAIL') { $overall = $false }
        $detail = ''
        switch ($name) {
            'preflight' {
                $live = @($result.live_checks | ForEach-Object { "$($_.name)=$($_.passed)" }) -join '; '
                $detail = "live_ready=$($result.live_ready); $live"
            }
            'unit' { $detail = "$($result.pytest_summary)" }
            'replay' {
                if ($result.run) {
                    $detail = "run_id=$($result.run.run_id) status=$($result.run.status)/$($result.run.terminal_reason) network=$($result.run.network_calls)"
                }
                elseif ($result.error) { $detail = "$($result.error)" }
            }
            'live' {
                if ($result.summary) {
                    $opportunities = @($result.summary.opportunity_ids) -join ','
                    $detail = "exit=$($result.exit_code) terminal=$($result.summary.terminal_reason) opportunities=[$opportunities] frontier=$($result.summary.frontier_ranking_mode) qualified=$($result.summary.frontier_ranking_qualified_count) r1_acceptance=$($result.r1_acceptance) r2_acceptance=$($result.research_inspection.r2_acceptance) shadow_ledger=$(($result.shadow_ledger_export | ConvertTo-Json -Compress)) elapsed=$($result.elapsed_seconds)s"
                }
                elseif ($result.reason) { $detail = "$($result.reason)" }
                elseif ($result.error) { $detail = "$($result.error)" }
            }
        }
        $md.Add("| $name | $verdict | $($detail -replace '\|', '/') |")
    }
    foreach ($name in $steps.Keys) {
        foreach ($failed in (Get-FailedChecks $steps[$name])) {
            $md.Add("- FAILED CHECK [$name] $($failed.name): $(($failed.detail | ConvertTo-Json -Compress -Depth 4))")
        }
    }
    if ($steps.Contains('live') -and $steps['live'] -and $steps['live'].summary) {
        $md.Add('')
        $md.Add('## Live waves')
        foreach ($wave in @($steps['live'].summary.waves)) {
            $md.Add("- wave $($wave.wave_index) $($wave.status)/$($wave.terminal_reason) listings=$(@($wave.listing_keys) -join ',') opportunities=$(@($wave.opportunity_ids) -join ',') diagnostics=$(@($wave.diagnostics) -join '; ')")
        }
    }
    if ($steps.Contains('live') -and $steps['live'] -and $steps['live'].research_inspection) {
        $inspection = $steps['live'].research_inspection
        $md.Add('')
        $md.Add("## Research inspection (AI-8C.3-R1 acceptance: $($inspection.r1_acceptance))")
        foreach ($check in @($inspection.r1_checks)) {
            $md.Add("- [$(if ($check.passed) { 'x' } else { ' ' })] $($check.name): $(($check.detail | ConvertTo-Json -Compress -Depth 4))")
        }
        $md.Add("- AI-8C.2 research acceptance: $($inspection.c2r1_acceptance); COMPLETE research: $($inspection.complete_research_count); PARTIAL without material gaps: $(@($inspection.partial_without_material_gaps).Count); opportunities: $(@($inspection.opportunity_ids) -join ',')")
        foreach ($check in @($inspection.c2r1_checks)) {
            $md.Add("- [$(if ($check.passed) { 'x' } else { ' ' })] $($check.name): $(($check.detail | ConvertTo-Json -Compress -Depth 4))")
        }
        $md.Add("- research status: $(($inspection.research_status_counts | ConvertTo-Json -Compress))")
        foreach ($failure in @($inspection.processing_failures)) {
            if ($failure) { $md.Add("- PROCESSING FAILURE $($failure.subject) $($failure.kind): $(@($failure.diagnostics) -join ' | ')") }
        }
        $md.Add("- outcomes: $(($inspection.outcome_reason_counts | ConvertTo-Json -Compress))")
        foreach ($row in @($inspection.research)) {
            $md.Add("- research $($row.ticker) $($row.research_status) quality=$($row.evidence_quality) confidence=$($row.research_confidence) volatility_in_evidence=$($row.bundle_states_volatility) volatility_unknown=$($row.volatility_listed_unknown)")
            $md.Add("    - evidence kinds: $(($row.evidence_kinds | ConvertTo-Json -Compress)); bundle warnings: $(@($row.bundle_warnings) -join ' | ')")
            $md.Add("    - fundamental period end: $($row.fundamental_period_end) (age $($row.fundamental_age_days) days)")
            foreach ($unknown in @($row.material_unknowns)) { if ($unknown) { $md.Add("    - MATERIAL: $unknown") } }
            foreach ($unknown in @($row.other_unknowns)) { if ($unknown) { $md.Add("    - other: $unknown") } }
            foreach ($item in @($row.forward_uncertainties)) { if ($item) { $md.Add("    - forward: $item") } }
            foreach ($item in @($row.material_gaps)) { if ($item) { $md.Add("    - GAP (blocks COMPLETE): $item") } }
            foreach ($item in @($row.context_gaps)) { if ($item) { $md.Add("    - context gap (non-blocking): $item") } }
            if ($row.research_confidence_repaired) { $md.Add("    - research_confidence repaired: $($row.initial_research_confidence) -> $($row.research_confidence)") }
        }
        foreach ($row in @($inspection.scores)) {
            $md.Add("- score $($row.ticker) $($row.hypothesis_kind) direction=$($row.direction)/$($row.direction_source) $($row.scoring_status) raw=$($row.raw_score) adjusted=$($row.confidence_adjusted_score) thesis=$($row.thesis_score) catalyst=$($row.catalyst_score) fundamental=$($row.fundamental_score) technical=$($row.technical_score) expectations=$($row.expectations_score) direction_ignored=$($row.possible_direction_ignored)")
        }
        $md.Add('')
        $md.Add("## Directional scoring (AI-8C.3-R2 acceptance: $($inspection.r2_acceptance))")
        foreach ($check in @($inspection.r2_checks)) {
            $md.Add("- [$(if ($check.passed) { 'x' } else { ' ' })] $($check.name): $(($check.detail | ConvertTo-Json -Compress -Depth 4))")
        }
        foreach ($pair in @($inspection.direction_pairs)) {
            $md.Add("- pair $($pair.ticker): LONG raw=$($pair.long_raw) technical=$($pair.long_technical) [thesis,catalyst,fundamental,expectations]=$(@($pair.components_long) -join '/') | SHORT raw=$($pair.short_raw) technical=$($pair.short_technical) [thesis,catalyst,fundamental,expectations]=$(@($pair.components_short) -join '/') | company-frame fundamental,expectations LONG=$(($pair.company_frame_long | ConvertTo-Json -Compress)) SHORT=$(($pair.company_frame_short | ConvertTo-Json -Compress)) shared_assessment=$($pair.shared_assessment) sources=$(($pair.sources_short | ConvertTo-Json -Compress))")
        }
    }
    $md.Add('')
    $overallText = 'FAIL'
    if ($overall) { $overallText = 'PASS' }
    $md.Add("**OVERALL: $overallText**")
    $summaryPath = Join-Parts @($ResultDir, 'summary.md')
    $md | Out-File -FilePath $summaryPath -Encoding utf8
    ($steps | ConvertTo-Json -Depth 12) | Out-File -FilePath (Join-Parts @($ResultDir, 'summary.json')) -Encoding utf8

    Write-Host ''
    $md | ForEach-Object { Write-Host $_ }
    Write-Host "`nSummary: $summaryPath" -ForegroundColor Cyan
    Stop-Transcript | Out-Null
    Pop-Location
}

if ($overall) { exit 0 } else { exit 1 }

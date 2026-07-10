param($scratch = "C:\Users\steph\AppData\Local\Temp\grok-goal-7f03c592cc57\implementer")  # specific per goal verification
New-Item -ItemType Directory -Force -Path $scratch | Out-Null
cd "C:\Users\steph\doge-miner-fullstack"
Write-Host "=== VERIF LAUNCH using documented entry: python -m uvicorn backend.main:app ==="
Write-Host "Ensuring deps (to SCRATCH logs)..."
python -m pip install -q -r backend/requirements.txt 2>$null

function Run-Verif {
  param($runName, $port)
  $serverLog = Join-Path $scratch "$runName-server.log"
  $runLog = Join-Path $scratch "$runName.log"
  "=== FULL SERVER LOG for $runName (documented entry) ===" | Out-File $runLog
  # Start uvicorn as a process with ALL output redirected (stdout + stderr) for full capture of uvicorn banner + miner prints
  $psi = New-Object System.Diagnostics.ProcessStartInfo
  $psi.FileName = "python"
  $psi.Arguments = "-m uvicorn backend.main:app --host 127.0.0.1 --port $port --log-level info"
  $psi.WorkingDirectory = (Get-Location).Path
  $psi.RedirectStandardOutput = $true
  $psi.RedirectStandardError = $true
  $psi.UseShellExecute = $false
  $psi.CreateNoWindow = $true
  $proc = [System.Diagnostics.Process]::Start($psi)
  Start-Sleep -Seconds 6  # allow uvicorn banner + app ready

  # Also capture any initial server output so far
  $initialOut = $proc.StandardOutput.ReadToEndAsync()
  $initialErr = $proc.StandardError.ReadToEndAsync()
  Start-Sleep -Milliseconds 300
  if ($initialOut.IsCompleted) { $initialOut.Result | Out-File -Append $serverLog }
  if ($initialErr.IsCompleted) { $initialErr.Result | Out-File -Append $serverLog }

  $base = "http://127.0.0.1:$port"
  try {
    $body1 = @{wallet="DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"; mode="cpu"} | ConvertTo-Json
    Invoke-RestMethod -Uri "$base/api/start" -Method Post -Body $body1 -ContentType "application/json" -TimeoutSec 15 | Out-Null
    "START-CPU (external HTTP to documented boot)" | Out-File -Append $runLog
    $positive = $false
    for($i=0; $i -lt 8; $i++) {
      Start-Sleep -Seconds 0.9
      $s = Invoke-RestMethod -Uri "$base/api/stats" -TimeoutSec 8
      $line = "POLL-CPU $i hashes=$($s.total_hashes) rate=$($s.hashrate_khs) running=$($s.running) mode=$($s.mode) workers=$($s.worker_count) shares=$($s.shares_accepted) earnings=$($s.earnings)"
      $line | Out-File -Append $runLog
      $line | Out-File -Append $serverLog   # also to server side for full view
      if ($s.total_hashes -gt 0) { $positive = $true }
    }
    $body2 = @{wallet="DTW2M5oEW97WbmYJRM71qD7uE6xfJs1MUK"; mode="gpu"; workers=6} | ConvertTo-Json
    Invoke-RestMethod -Uri "$base/api/start" -Method Post -Body $body2 -ContentType "application/json" -TimeoutSec 15 | Out-Null
    "START-GPU (external, custom workers)" | Out-File -Append $runLog
    Start-Sleep -Seconds 1.0
    $s2 = Invoke-RestMethod -Uri "$base/api/stats" -TimeoutSec 8
    "POLL-GPU hashes=$($s2.total_hashes) rate=$($s2.hashrate_khs) mode=$($s2.mode) workers=$($s2.worker_count)" | Out-File -Append $runLog
    Invoke-RestMethod -Uri "$base/api/stop" -Method Post -TimeoutSec 8 | Out-Null
    "STOP (external)" | Out-File -Append $runLog
  } catch {
    "ERR: $_" | Out-File -Append $runLog
  } finally {
    # Drain remaining server output (this is where "Started XXX mining with N workers" and uvicorn lines will appear)
    Start-Sleep -Seconds 1
    if (-not $proc.HasExited) {
      try { $proc.Kill($true) } catch {}
    }
    $tailOut = $proc.StandardOutput.ReadToEnd()
    $tailErr = $proc.StandardError.ReadToEnd()
    if ($tailOut) { $tailOut | Out-File -Append $serverLog }
    if ($tailErr) { $tailErr | Out-File -Append $serverLog }
    # Merge full server log into the run log for single-file evidence
    "" | Out-File -Append $runLog
    "=== SERVER STDOUT/STDERR (full uvicorn + miner prints) ===" | Out-File -Append $runLog
    if (Test-Path $serverLog) { Get-Content $serverLog | Out-File -Append $runLog }
  }
  Write-Host "Wrote $runLog (with full server output)"
  Get-Content $runLog -Tail 12
}

# Run 1
Run-Verif -runName "launch-run-1" -port 18150
Start-Sleep -Seconds 2
# Run 2 (fresh boot)
Run-Verif -runName "launch-run-2" -port 18151

Write-Host "=== Verification complete. Full logs (including uvicorn banner + 'Started ... mining') in SCRATCH ==="
Write-Host "launch-run-1.log and launch-run-2.log now contain complete boot + external HTTP evidence."

# Additional for plan: units + source-check.txt
Write-Host "Running units..."
python -m unittest backend.tests.test_miner -v 2>&1 | Out-File -FilePath (Join-Path $scratch "unit-tests.log") -Encoding utf8
Write-Host "Units captured."

Write-Host "Source grep for required patterns..."
$srcCheck = Join-Path $scratch "source-check.txt"
@"
data-last-paint after grid: $(if (Select-String -Path frontend/index.html -Pattern 'data-last-paint' -Quiet) { 'present' } else { 'MISSING' })
if (!cfg.ok) return in saveConfig: $(if (Select-String -Path frontend/index.html -Pattern 'if \(!cfg.ok\)' -Quiet) { 'present' } else { 'MISSING' })
feedBuffer: $(if (Select-String -Path frontend/index.html -Pattern 'feedBuffer' -Quiet) { 'present' } else { 'MISSING' })
resetMiningClientState: $(if (Select-String -Path frontend/index.html -Pattern 'resetMiningClientState' -Quiet) { 'present' } else { 'MISSING' })
HTML keys in source: $(if (Select-String -Path frontend/index.html -Pattern 'log-container|cpu-chart|gpu-chart|setup-mode-cpu|config-modal|help-modal|wallet-input' -Quiet) { 'present' } else { 'MISSING' })
"@ | Out-File -FilePath $srcCheck -Encoding utf8
Write-Host "source-check.txt written."

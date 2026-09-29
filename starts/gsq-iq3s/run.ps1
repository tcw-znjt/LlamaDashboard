$exe = "E:\2-TCW\workspace\llama.cpp\b11223\llama-server.exe"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$log = "$PSScriptRoot\server-$stamp.log"

function Test-Port($p) {
  try {
    $l = New-Object System.Net.Sockets.TcpListener([System.Net.IPAddress]::Any, $p)
    $l.Start()
    $l.Stop()
    return $true
  } catch { return $false }
}

$port = 8088
if (-not (Test-Port $port)) {
  Write-Host "WARNING: port $port is unavailable (occupied or Hyper-V excluded range). Trying fallback ports..."
  $port = 0
  foreach ($cand in 9188, 10188, 18888, 28088) {
    if (Test-Port $cand) { $port = $cand; break }
  }
  if ($port -eq 0) { Write-Host "ERROR: no available port found (9188/10188/18888/28088 all busy)."; exit 1 }
  Write-Host ">>> FALLBACK: using port $port. Update your client (e.g. CodeBuddy) to http://localhost:$port <<<"
}

$sw = New-Object System.IO.StreamWriter($log, $false, (New-Object System.Text.UTF8Encoding($false)))
$sw.AutoFlush = $true
Write-Host "Log: $log"
try {
  & $exe `
    -m "D:\models\Qwen3.8-27B-GSQ-RCO-IQ3_S-mtp.gguf" `
    --spec-type draft-mtp `
    -ngl 99 -ngld 99 `
    -fa on `
    -c 65536 --parallel 1 `
    --cache-type-k q8_0 --cache-type-v q8_0 `
    --jinja `
    --alias Qwen3.8 `
    --host 0.0.0.0 `
    --port $port `
    --api-key 123456 2>&1 | ForEach-Object { $l = "$_"; $sw.WriteLine($l); $l }
}
finally { $sw.Close() }
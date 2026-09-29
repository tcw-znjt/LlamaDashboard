# kvmem-swift-iq3xs: 调 KVMem 的 start-iq3.ps1(最终起 bin/llama-kvmem-server.exe)。
# 该脚本端口写死 18200,所以这里只做占用检查;控制台输出照旧留存为 server-<时间戳>.log。
$launcher = "E:\2-TCW\workspace\kvmen_llama\kvmem-v0.16.0-rc3-windows-x86_64-cuda13.2.86\scripts\windows\start-iq3.ps1"
$port = 18200
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

if (-not (Test-Port $port)) {
  Write-Host "ERROR: port $port is occupied (start-iq3.ps1 hardcodes it). Stop the running server first."
  exit 1
}

$sw = New-Object System.IO.StreamWriter($log, $false, (New-Object System.Text.UTF8Encoding($false)))
$sw.AutoFlush = $true
Write-Host "Log: $log"
try {
  & powershell -NoProfile -ExecutionPolicy Bypass -File $launcher `
    -Model "D:\models\Swift-Qwen3.8-27B-IQ3_XS.gguf" `
    -Gpu 0 2>&1 | ForEach-Object { $l = "$_"; $sw.WriteLine($l); $l }
}
finally { $sw.Close() }

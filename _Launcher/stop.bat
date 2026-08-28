@echo off
setlocal
chcp 65001 >nul 2>&1
powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ports = 8090,8091,8092,8093,8094; " ^
  "$conns = Get-NetTCPConnection -LocalPort $ports -State Listen -ErrorAction SilentlyContinue; " ^
  "if (-not $conns) { Write-Host 'No SD SMA launcher services are listening on 8090-8094.'; exit 0 }; " ^
  "$serviceProcessIds = $conns | Select-Object -ExpandProperty OwningProcess -Unique; " ^
  "foreach ($processId in $serviceProcessIds) { " ^
  "  $proc = Get-CimInstance Win32_Process -Filter \"ProcessId=$processId\" -ErrorAction SilentlyContinue; " ^
  "  if (-not $proc) { Write-Host \"PID $processId is already stopped.\"; continue } " ^
  "  if ($proc.CommandLine -match '(sd_sma_launcher[.]py|SD_SMA_Launcher[.]exe)' -or ($proc.CommandLine -match 'uvicorn' -and $proc.CommandLine -match '(web_config[.]main:app|app[.]main:app)')) { " ^
  "    Write-Host \"Stopping PID ${processId}: $($proc.CommandLine)\"; Stop-Process -Id $processId -Force " ^
  "  } else { " ^
  "    Write-Host \"Skip PID $processId because it does not look like an SD SMA uvicorn service.\" " ^
  "  } " ^
  "}"
pause
endlocal

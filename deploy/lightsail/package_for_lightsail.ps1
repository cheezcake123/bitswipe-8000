param(
  [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Dist = Join-Path $Root "dist"

if (-not $OutputPath) {
  $OutputPath = Join-Path $Dist "bitswipe-lightsail.tar.gz"
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $OutputPath) | Out-Null
if (Test-Path -LiteralPath $OutputPath) {
  Remove-Item -LiteralPath $OutputPath -Force
}

$excludes = @(
  "--exclude=.git",
  "--exclude=.venv",
  "--exclude=venv",
  "--exclude=env",
  "--exclude=__pycache__",
  "--exclude=data",
  "--exclude=dist",
  "--exclude=.env",
  "--exclude=.env.local",
  "--exclude=.env.development",
  "--exclude=*.log",
  "--exclude=.pytest_cache",
  "--exclude=.mypy_cache"
)

& tar.exe @excludes -czf $OutputPath -C $Root .
if ($LASTEXITCODE -ne 0) {
  throw "tar.exe failed with exit code $LASTEXITCODE"
}

Write-Host "Created package: $OutputPath"

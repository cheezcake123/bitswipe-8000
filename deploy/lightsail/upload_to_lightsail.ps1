param(
  [Parameter(Mandatory = $true)]
  [string]$ServerIp,

  [Parameter(Mandatory = $true)]
  [string]$KeyPath,

  [string]$User = "ubuntu",
  [string]$RemoteDir = "/opt/bitswipe"
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Archive = Join-Path $Root "dist\bitswipe-lightsail.tar.gz"

& (Join-Path $PSScriptRoot "package_for_lightsail.ps1") -OutputPath $Archive
if ($LASTEXITCODE -ne 0) {
  throw "package_for_lightsail.ps1 failed"
}

$target = "${User}@${ServerIp}"
Write-Host "Uploading package to $target ..."
& scp.exe -i $KeyPath $Archive "${target}:/tmp/bitswipe-lightsail.tar.gz"
if ($LASTEXITCODE -ne 0) {
  throw "scp.exe failed with exit code $LASTEXITCODE"
}

$remoteCommand = "sudo mkdir -p $RemoteDir && sudo tar -xzf /tmp/bitswipe-lightsail.tar.gz -C $RemoteDir && sudo chown -R $User`:$User $RemoteDir"
& ssh.exe -i $KeyPath $target $remoteCommand
if ($LASTEXITCODE -ne 0) {
  throw "ssh.exe failed with exit code $LASTEXITCODE"
}

Write-Host ""
Write-Host "Uploaded to $RemoteDir"
Write-Host "Next:"
Write-Host "  ssh -i `"$KeyPath`" $target"
Write-Host "  cd $RemoteDir"
Write-Host "  cp .env.production.example .env"
Write-Host "  nano .env"
Write-Host "  chmod +x deploy/lightsail/bootstrap_ubuntu.sh"
Write-Host "  APP_DIR=$RemoteDir SERVICE_NAME=bitswipe APP_PORT=8000 ./deploy/lightsail/bootstrap_ubuntu.sh"

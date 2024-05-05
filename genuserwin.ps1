# Run genuser.sh within tpotinit, prepare path and file
# Define the volume paths
$homePath = $Env:USERPROFILE + "\tpotce"
$nginxpasswdPath = $homePath + "\data\nginx\conf\nginxpasswd"

# Ensure nginxpasswd file exists
if (-Not (Test-Path $nginxpasswdPath)) {
    New-Item -ItemType File -Force -Path $nginxpasswdPath
}

# Run the Docker container without specifying UID / GID
docker run -v "${homePath}:/data" --entrypoint bash -it dtagdevsec/tpotinit:24.04 "/opt/tpot/bin/genuser.sh"

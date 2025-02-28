# Format, colorize docker ps output
# Define a fixed width for the STATUS column
$statusWidth = 30

# Capture the Docker output into a variable
$dockerOutput = docker ps -f status=running -f status=exited --format "{{.Names}}`t{{.Status}}`t{{.Ports}}"

# Print header with colors
Write-Host ("NAME".PadRight(20) + "STATUS".PadRight($statusWidth) + "PORTS") -ForegroundColor Cyan -NoNewline
Write-Host ""

# Split the output into lines and loop over them
$dockerOutput -split '\r?\n' | ForEach-Object {
    if ($_ -ne "") {
        $fields = $_ -split "`t"
        Write-Host ($fields[0].PadRight(20)) -NoNewline -ForegroundColor Yellow
        Write-Host ($fields[1].PadRight($statusWidth)) -NoNewline -ForegroundColor Green
        Write-Host ($fields[2]) -ForegroundColor Blue
    }
}

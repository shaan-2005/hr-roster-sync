$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function New-Secret {
    $bytes = New-Object byte[] 32
    $generator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($bytes) } finally { $generator.Dispose() }
    return ([System.BitConverter]::ToString($bytes)).Replace('-', '').ToLowerInvariant()
}

if (-not (Test-Path -LiteralPath '.env')) {
    @(
        "POSTGRES_PASSWORD=$(New-Secret)"
        "DJANGO_SECRET_KEY=$(New-Secret)"
        "OUTBOX_INTERNAL_TOKEN=$(New-Secret)"
    ) | Set-Content -LiteralPath '.env' -Encoding Ascii
    Write-Host 'Created local .env with random credentials.'
}

$docker = Get-Command docker -ErrorAction SilentlyContinue
if ($docker) {
    & $docker.Source compose up --build
} elseif (Test-Path -LiteralPath 'D:\Docker\resources\bin\docker-compose.exe') {
    $env:PATH = 'D:\Docker\resources\bin;' + $env:PATH
    & 'D:\Docker\resources\bin\docker-compose.exe' up --build
} else {
    throw 'Docker Compose was not found. Start Docker Desktop and reopen PowerShell.'
}

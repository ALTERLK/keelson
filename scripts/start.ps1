# One-command start for the Keelson web console (Windows).
#
#   start.bat              live if .env has an API key, otherwise replay
#   start.bat -Replay      recorded traces only; no model is called
#   start.bat -Port 9000
#   start.bat -NoOpen      do not open a browser tab
#
# Finds a Python >= 3.11, creates or repairs .venv, installs the package on
# first run (and again when pyproject.toml changes), then runs `keelson web`.

param(
    [switch]$Replay,
    [switch]$NoOpen,
    [int]$Port = 8765
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $root

function Test-Python([string[]]$cmd) {
    # A launcher entry can outlive the interpreter it points to, so run it.
    $rest = @()
    if ($cmd.Count -gt 1) { $rest = $cmd[1..($cmd.Count - 1)] }
    # Windows PowerShell turns a native command's stderr into an error record;
    # with ErrorActionPreference=Stop that would abort the script, so relax it here.
    $previous = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        $out = & $cmd[0] @rest -c "import sys; print(sys.version_info >= (3, 11))" 2>$null
        return ($LASTEXITCODE -eq 0 -and "$out".Trim() -eq 'True')
    } catch {
        return $false
    } finally {
        $ErrorActionPreference = $previous
    }
}

function Find-Python {
    $candidates = @(
        @('py', '-3.13'), @('py', '-3.12'), @('py', '-3.11'),
        @('python'), @('python3')
    )
    foreach ($cmd in $candidates) {
        if ((Get-Command $cmd[0] -ErrorAction SilentlyContinue) -and (Test-Python $cmd)) { return $cmd }
    }
    # Interpreters installed by uv live in a known folder; look there directly
    # so this works even where the `uv` command itself is unavailable.
    $uvDir = Join-Path $env:APPDATA 'uv\python'
    if (Test-Path -LiteralPath $uvDir) {
        $dirs = Get-ChildItem -LiteralPath $uvDir -Directory -Filter 'cpython-3.1*' | Sort-Object Name -Descending
        foreach ($dir in $dirs) {
            $exe = Join-Path $dir.FullName 'python.exe'
            if ((Test-Path -LiteralPath $exe) -and (Test-Python @($exe))) { return @($exe) }
        }
    }
    return $null
}

$venvPython = Join-Path $root '.venv\Scripts\python.exe'

if (-not ((Test-Path -LiteralPath $venvPython) -and (Test-Python @($venvPython)))) {
    # @(...) keeps a one-element result an array; PowerShell would otherwise
    # unwrap it to a string, and $python[0] would be its first character.
    $python = @(Find-Python)
    if ($python.Count -eq 0) {
        Write-Host 'Python 3.11 or newer was not found. Install it from https://www.python.org/downloads/ and run this again.' -ForegroundColor Red
        exit 1
    }
    if (Test-Path -LiteralPath (Join-Path $root '.venv')) {
        Write-Host 'The existing .venv does not run; rebuilding it.'
        Remove-Item -LiteralPath (Join-Path $root '.venv') -Recurse -Force
    }
    Write-Host "Creating .venv with: $($python -join ' ')"
    $rest = @()
    if ($python.Count -gt 1) { $rest = $python[1..($python.Count - 1)] }
    $ErrorActionPreference = 'Continue'
    & $python[0] @rest -m venv .venv
    $ErrorActionPreference = 'Stop'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Could not create the virtual environment.' -ForegroundColor Red
        exit 1
    }
}

# Reinstall only when pyproject.toml changed since the last successful install.
$stamp = Join-Path $root '.venv\.keelson-installed'
$wanted = (Get-Item -LiteralPath (Join-Path $root 'pyproject.toml')).LastWriteTimeUtc.Ticks.ToString()
$have = ''
if (Test-Path -LiteralPath $stamp) { $have = (Get-Content -LiteralPath $stamp -Raw).Trim() }
if ($have -ne $wanted) {
    Write-Host 'Installing dependencies (first run takes about a minute)...'
    $ErrorActionPreference = 'Continue'   # pip reports build warnings on stderr
    & $venvPython -m pip install --quiet --disable-pip-version-check -e '.[web]'
    $ErrorActionPreference = 'Stop'
    if ($LASTEXITCODE -ne 0) {
        Write-Host 'Install failed. Check your network connection and run this again.' -ForegroundColor Red
        exit 1
    }
    Set-Content -LiteralPath $stamp -Value $wanted -Encoding ascii
}

$webArgs = @('-m', 'agents.main', 'web', '--port', "$Port")
if ($Replay) { $webArgs += '--replay' }
if ($NoOpen) { $webArgs += '--no-open' }
& $venvPython @webArgs
exit $LASTEXITCODE

param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ProjectName,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = 'Continue'

$workspace = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Set-Location $workspace

$dataset = Join-Path 'datasets' $ProjectName
$outputDir = Join-Path 'output' $ProjectName
$rustProjectName = "$ProjectName-rust"
$logDir = Join-Path $workspace 'log'

$tmpParent = $null
if ($env:LOCALAPPDATA) {
    $tmpParent = Join-Path $env:LOCALAPPDATA 'Temp'
}
elseif ($env:USERPROFILE) {
    $tmpParent = Join-Path $env:USERPROFILE 'AppData\Local\Temp'
}
else {
    $tmpParent = Join-Path $logDir 'tmp'
}

$tmpDir = Join-Path $tmpParent 'cgrcode-agent'

New-Item -ItemType Directory -Force -Path $logDir | Out-Null
New-Item -ItemType Directory -Force -Path $tmpDir | Out-Null

$timestamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$logFile = Join-Path $logDir "agent-$ProjectName-$timestamp.log"

$env:TEMP = $tmpDir
$env:TMP = $tmpDir
$env:TMPDIR = $tmpDir
$env:CONDA_NO_PLUGINS = 'true'
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host '========================================'
Write-Host "Project: $ProjectName"
Write-Host "Dataset: $dataset"
Write-Host "Output: $outputDir"
Write-Host "Rust project: $rustProjectName"
Write-Host "Log: $logFile"
Write-Host '========================================'

$pythonExe = $null
$explicitCandidates = @(
    'E:\develop\Anaconda\miniconda3\envs\tcode\python.exe'
)
foreach ($candidate in $explicitCandidates) {
    if (Test-Path $candidate) {
        $pythonExe = $candidate
        break
    }
}

if (-not $pythonExe) {
    $condaCmd = Get-Command conda -ErrorAction SilentlyContinue
    if ($condaCmd) {
        $condaDir = Split-Path $condaCmd.Source -Parent
        $condaBase = Split-Path $condaDir -Parent
        $candidate = Join-Path $condaBase 'envs\tcode\python.exe'
        if (Test-Path $candidate) {
            $pythonExe = $candidate
        }
    }
}

if ($pythonExe) {
    Write-Host "Runner: $pythonExe"
    & $pythonExe -u .\src\agent\main.py `
        --c_project_path $dataset `
        --output_dir $outputDir `
        --rust-project-name $rustProjectName `
        --use-rust-repair-agent `
        --use-contextual-rust-agent `
        --use-rust-test-agent `
        --use-spec-agent `
        --rust-test-agent-max-iterations 20 `
        @ExtraArgs 2>&1 | Tee-Object -FilePath $logFile
}
else {
    Write-Host 'Runner: conda run -n tcode'
    & conda --no-plugins run --no-capture-output -n tcode `
        python -u .\src\agent\main.py `
        --c_project_path $dataset `
        --output_dir $outputDir `
        --rust-project-name $rustProjectName `
        --use-rust-repair-agent `
        --use-contextual-rust-agent `
        --use-rust-test-agent `
        --use-spec-agent `
        --rust-test-agent-max-iterations 20 `
        @ExtraArgs 2>&1 | Tee-Object -FilePath $logFile
}

exit $LASTEXITCODE

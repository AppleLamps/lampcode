# Initialize demo-project as a standalone git repo for agent init / run.
Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot

Set-Location $Root
if (-not (Test-Path ".git")) {
    git init
    git add calc.py test_calc.py AGENTS.md README.md .agent-cli
    git commit -m "demo-project scaffold"
    Write-Host "Initialized git repo in $Root"
} else {
    Write-Host "Git repo already exists in $Root"
}

Write-Host "Next:"
Write-Host "  agent init --yes    # if .agent-cli needs refresh"
Write-Host "  agent run `"fix failing tests`" --model-profile deep"

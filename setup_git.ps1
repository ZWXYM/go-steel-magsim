$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

if (Test-Path ".git") {
    Remove-Item -Recurse -Force ".git"
}

git init
git config user.name "YUCE"
git config user.email "yuce@project.local"

$gitignore = "# Python`n__pycache__/`n*.py[cod]`n*.pyo`n.Python`n`n# Sim output`noutput/`npreinput/pipeline_*/`ngrain_scripts/pipeline_*/`n`n# ML artifacts`ndata/datasets/*.csv`ndata/models/*/`ndata/exports/*.amat`n`n# Misc`ninstance/`n.vscode/`n.idea/`n*.swp`n.DS_Store`nThumbs.db`n*.log`nsetup_git.ps1`n"
[System.IO.File]::WriteAllText("$dir\.gitignore", $gitignore, [System.Text.Encoding]::UTF8)

git add .
git commit -m "snapshot: YUCE v2 with ML pipeline (pre-refactor)"

Write-Host "Done. Use 'git checkout -- .' to restore this snapshot." -ForegroundColor Green

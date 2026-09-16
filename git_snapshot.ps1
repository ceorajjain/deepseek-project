$Root = "C:\Users\HP\Documents\Codex\2026-09-14\hi"
Set-Location $Root

git add pipeline run_pipeline.ps1 .gitignore
$msg = "Snapshot " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
git commit -m $msg
git push origin main

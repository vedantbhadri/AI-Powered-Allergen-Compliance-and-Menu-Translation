# Windows PowerShell script to run the Flask app locally
# Runs the Flask app locally in LOCAL_MODE (no AWS account needed)

Set-Location -Path "app"

$env:LOCAL_MODE = "true"
$env:FLASK_ENV = "development"
$env:PORT = 8000

Write-Host "Starting local server at http://localhost:8000  (LOCAL_MODE=true, no AWS calls)" -ForegroundColor Green

python application.py
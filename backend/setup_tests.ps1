# OracleX Test Setup & Runner
# Run this from C:\Projects\OracleX\backend

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "  OracleX Test Suite Setup" -ForegroundColor Cyan
Write-Host "========================================" -ForegroundColor Cyan

# Install test dependencies
Write-Host "`n[1/3] Installing test dependencies..." -ForegroundColor Yellow
pip install pytest pytest-asyncio httpx playwright --quiet

# Install Playwright browser
Write-Host "`n[2/3] Installing Playwright Chromium browser..." -ForegroundColor Yellow
playwright install chromium

# Copy test file to tests folder
Write-Host "`n[3/3] Setting up test file..." -ForegroundColor Yellow
if (!(Test-Path "tests")) { New-Item -ItemType Directory -Path "tests" }
Copy-Item "..\tests\test_oraclex_full.py" -Destination "tests\" -Force 2>$null

Write-Host "`n========================================" -ForegroundColor Green
Write-Host "  Setup complete! Run tests with:" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "  # All tests (backend + DB + frontend):" -ForegroundColor White
Write-Host "  pytest tests/test_oraclex_full.py -v" -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Backend + DB only (no browser):" -ForegroundColor White
Write-Host "  pytest tests/test_oraclex_full.py -v -k 'not TestFrontendUI'" -ForegroundColor Cyan
Write-Host ""
Write-Host "  # Just the full pipeline test:" -ForegroundColor White
Write-Host "  pytest tests/test_oraclex_full.py -v -k 'test_full_prediction_pipeline'" -ForegroundColor Cyan
Write-Host ""
Write-Host "  # With live output printed:" -ForegroundColor White
Write-Host "  pytest tests/test_oraclex_full.py -v -s" -ForegroundColor Cyan
Write-Host ""

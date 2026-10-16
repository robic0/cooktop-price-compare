@echo off
REM Cooktop Price Comparison - Daily Scraper
REM Schedule this with Windows Task Scheduler
REM Action: Start a program
REM Program: C:\Users\rober\.gemini\antigravity\scratch\cooktop-price-compare\run_daily.bat

cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\Programs\Python\Python312;%LOCALAPPDATA%\Programs\Python\Python312\Scripts;%PATH%"
python scraper.py
echo Scrape completed at %date% %time% >> data\scrape_log.txt
pause

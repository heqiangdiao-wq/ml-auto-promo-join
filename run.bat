@echo off
REM 美客多推广中心自动报活动 —— Windows 快速运行
REM
REM 用法：
REM   run.bat            真实参加
REM   run.bat --dry      只判定
REM   run.bat --store 4店 --dry
REM
REM 日志自动写入 logs\run_<日期>_<时间>.log

setlocal

cd /d "%~dp0"

if not exist logs mkdir logs

REM 生成时间戳（不依赖系统区域设置，用 WMIC 的 ISO 格式再裁剪）
for /f "tokens=2 delims==" %%i in ('wmic os get localdatetime /value 2^>nul') do set DT=%%i
if "%DT%"=="" (
  set STAMP=%RANDOM%
) else (
  set STAMP=%DT:~0,8%_%DT:~8,4%
)

set LOG=logs\run_%STAMP%.log

echo [run] python -m src.main %* > "%LOG%"
python -m src.main %* >> "%LOG%" 2>&1
set RC=%ERRORLEVEL%

echo.
echo [run] exit=%RC%  log=%LOG%
endlocal & exit /b %RC%

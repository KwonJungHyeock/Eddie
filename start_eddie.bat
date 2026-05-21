@echo off
REM ============================================================
REM  EDDIE 통합 런처 (Phase 3-4 풀 통합)
REM  npm start 하나로 HUD + 음성 백엔드 자동 실행
REM  HUD 창에서 스페이스바로 녹음
REM ============================================================

cd /d "%~dp0"

echo ============================================================
echo   EDDIE 시작 중... (HUD + 음성 통합)
echo ============================================================
echo.

if not exist "venv\Scripts\python.exe" (
    echo [에러] venv 가 없습니다.
    pause
    exit /b 1
)

REM HUD 실행 (main.js 가 voice_chat 을 자동으로 백그라운드 실행)
call npm start


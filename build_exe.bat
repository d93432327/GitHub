@echo off
chcp 65001 >nul
cd /d "%~dp0"

set "APP_NAME=GitHub工作台"
set "ENTRY_SCRIPT=github_manager_pro.py"
set "PY_CMD=py -3.12"

echo ========================================================
echo   正在啟動 [%APP_NAME%] 精簡版自動打包流程
echo ========================================================
echo.

:: 1. 檢查主程式是否存在
if not exist "%ENTRY_SCRIPT%" (
    echo [錯誤] 找不到主程式 %ENTRY_SCRIPT%！
    echo 請確認 build_exe.bat 與 %ENTRY_SCRIPT% 放在同一個資料夾內。
    pause
    exit /b 1
)

:: 2. 清理舊的打包暫存資料夾與舊版 .exe
echo [1/4] 正在清理舊的打包暫存檔 (build / dist / .spec)...
if exist "build" rmdir /s /q "build"
if exist "dist" rmdir /s /q "dist"
if exist "%APP_NAME%.spec" del /f /q "%APP_NAME%.spec"
if exist "%APP_NAME%.exe" del /f /q "%APP_NAME%.exe"

:: 3. 確認 PyInstaller 是否已安裝
echo [2/4] 正在檢查 PyInstaller 打包環境...
%PY_CMD% -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo 尚未安裝 PyInstaller，正在為您自動安裝...
    %PY_CMD% -m pip install pyinstaller
)

:: 4. 執行精簡版 PyInstaller 打包（排除未使用的肥大模組）
echo [3/4] 正在執行精簡版打包，請稍候約 30~60 秒...
%PY_CMD% -m PyInstaller --noconsole --onefile --clean --name "%APP_NAME%" ^
  --exclude-module PySide6.QtWebEngine ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.Qt3DCore ^
  --exclude-module PySide6.Qt3DRender ^
  --exclude-module PySide6.Qt3DInput ^
  --exclude-module PySide6.Qt3DLogic ^
  --exclude-module PySide6.Qt3DAnimation ^
  --exclude-module PySide6.Qt3DExtras ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtMultimediaWidgets ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtNfc ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtLocation ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtSvg ^
  --exclude-module PySide6.QtSvgWidgets ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtNetwork ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module tkinter ^
  --exclude-module unittest ^
  --exclude-module pydoc ^
  "%ENTRY_SCRIPT%"

if errorlevel 1 (
    echo.
    echo [打包失敗] 請檢查上方錯誤訊息！
    pause
    exit /b 1
)

:: 5. 將成品移出至專案根目錄並清除所有打包暫存垃圾
echo [4/4] 正在取出成品並自動清理暫存資料夾...
if exist "dist\%APP_NAME%.exe" (
    move /y "dist\%APP_NAME%.exe" "%APP_NAME%.exe" >nul
    rmdir /s /q "build"
    rmdir /s /q "dist"
    del /f /q "%APP_NAME%.spec"
    echo.
    echo ========================================================
    echo   打包成功！
    echo   成品位置: %~dp0%APP_NAME%.exe
    echo ========================================================
) else (
    echo [錯誤] 找不到產出的執行檔！
)

pause
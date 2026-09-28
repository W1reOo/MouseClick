@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo [1/3] 检查打包工具...
py -m pip install -r requirements.txt || goto :fail

echo [2/3] 正在打包（约需 1 分钟）...
py -m PyInstaller --noconsole --onefile --clean --name AutoClicker auto_clicker.py || goto :fail

echo [3/3] 复制使用说明...
copy /Y "使用说明.md" "dist\使用说明.txt" >nul

echo.
echo 打包完成：dist\AutoClicker.exe
explorer "%~dp0dist"
exit /b 0

:fail
echo 打包失败，请检查 Python / PyInstaller 环境。
pause
exit /b 1

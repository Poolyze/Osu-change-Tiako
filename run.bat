@echo off
chcp 65001 >nul
cd /d "%~dp0"
where py >nul 2>nul && (py -3 app.py & goto :done)
where python >nul 2>nul && (python app.py & goto :done)
echo 找不到 Python。
echo 請先到 https://www.python.org/downloads/ 安裝 Python 3，
echo 安裝時記得勾選 "Add python.exe to PATH"，裝好後再雙擊一次這個檔案。
:done
pause

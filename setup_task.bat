@echo off
chcp 65001 >nul
echo ====================================
echo  报奖信息监测 - 定时任务注册
echo ====================================
echo.

:: 获取脚本所在目录
set PROJECT_DIR=%~dp0
set PROJECT_DIR=%PROJECT_DIR:~0,-1%

:: 通过 run.bat 启动，确保工作目录正确（data/ logs/ 等相对路径依赖）
:: pythonw 无控制台窗口，适合后台定时运行

:: 注册上午任务（09:00）
schtasks /create /tn "报奖信息监测-上午" /tr "cmd /c \"%PROJECT_DIR%\run.bat\"" /sc daily /st 09:00 /f
if %errorlevel% equ 0 (
    echo [OK] 上午任务注册成功 (每天 09:00)
) else (
    echo [FAIL] 上午任务注册失败
)

:: 注册晚上任务（21:00）
schtasks /create /tn "报奖信息监测-晚上" /tr "cmd /c \"%PROJECT_DIR%\run.bat\"" /sc daily /st 21:00 /f
if %errorlevel% equ 0 (
    echo [OK] 晚上任务注册成功 (每天 21:00)
) else (
    echo [FAIL] 晚上任务注册失败
)

echo.
echo ====================================
echo  注册完成！
echo  上午: 每天 09:00
echo  晚上: 每天 21:00
echo ====================================
echo.
echo  如需删除定时任务，运行:
echo  schtasks /delete /tn "报奖信息监测-上午" /f
echo  schtasks /delete /tn "报奖信息监测-晚上" /f
echo.
pause

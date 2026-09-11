@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 即将打开浏览器，请在弹出的 Chrome 窗口里登录 B 站（扫码即可）。
echo 登录成功后程序会自动检测并保存登录态，之后就可以下高清晰度了。
echo.
python bili_login.py %*
if errorlevel 1 (
  echo.
  echo 没有检测到登录（或出错了），可重试一次。
  pause
)

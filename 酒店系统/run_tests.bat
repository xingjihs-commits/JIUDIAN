@echo off
echo Running PMS test suite...
call "C:\Users\FF.FC\AppData\Local\Programs\Python\Python311\python.exe" -m pytest tests/
if %ERRORLEVEL% EQU 0 (
    echo All tests passed!
) else (
    echo Some tests failed.
)
pause

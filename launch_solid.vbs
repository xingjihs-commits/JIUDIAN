' Solid PMS 启动器 — 支持中文路径，双击即可
Option Explicit
Dim sh, appDir, pyExe, fso
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
appDir = "d:\AAAGZT-99\JIUDIAN\酒店系统"
pyExe = "C:\Users\FF.FC\AppData\Local\Programs\Python\Python311\pythonw.exe"
If Not fso.FolderExists(appDir) Then
    MsgBox "找不到程序目录：" & vbCrLf & appDir, vbCritical, "Solid 酒店系统"
    WScript.Quit 1
End If
If Not fso.FileExists(appDir & "\app_main.py") Then
    MsgBox "找不到 app_main.py，请确认 D 盘项目完整。", vbCritical, "Solid 酒店系统"
    WScript.Quit 1
End If
If Not fso.FileExists(pyExe) Then
    pyExe = "C:\Users\FF.FC\AppData\Local\Programs\Python\Python311\python.exe"
End If
sh.CurrentDirectory = appDir
sh.Run """" & pyExe & """ app_main.py", 1, False

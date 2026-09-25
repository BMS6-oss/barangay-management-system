' Barangay Management System (BMS) Silent Windows Launcher
' Launches start_bms.bat without showing a command prompt window.

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

strScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
strBatPath = """" & strScriptDir & "\start_bms.bat"""

WshShell.CurrentDirectory = strScriptDir
WshShell.Run strBatPath, 0, False

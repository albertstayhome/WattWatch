Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ScriptPath = WScript.ScriptFullName
AppDir = fso.GetParentFolderName(ScriptPath)

argStr = ""
For Each a In WScript.Arguments
    argStr = argStr & " " & a
Next

isElevated = (WshShell.Run("cmd /c fltmc >nul 2>&1", 0, True) = 0)
If isElevated Then
    WshShell.CurrentDirectory = AppDir
    WshShell.Run "pyw """ & AppDir & "\app.py""" & argStr, 0, False
Else
    Set ShellApp = CreateObject("Shell.Application")
    ShellApp.ShellExecute "wscript.exe", """" & ScriptPath & """" & argStr, AppDir, "runas", 1
End If

Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
gatewayScript = scriptDir & "\gateway.py"

' 后台静默启动 gateway.py（0 表示隐藏窗口）
WshShell.Run "python " & Chr(34) & gatewayScript & Chr(34), 0
Set WshShell = Nothing
Set fso = Nothing
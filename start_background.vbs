Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
gatewayScript = scriptDir & "\gateway.py"

' 设置工作目录为网关目录，保证相对模块加载正常
WshShell.CurrentDirectory = scriptDir

' 后台静默启动 gateway.py（窗口参数0=隐藏窗口，第三参数False=不阻塞）
WshShell.Run "python " & Chr(34) & gatewayScript & Chr(34), 0, False
Set WshShell = Nothing
Set fso = Nothing
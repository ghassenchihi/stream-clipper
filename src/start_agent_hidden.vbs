' Starts the clipper agent with NO window (nothing to close by mistake). Stop it: create GO_quit.txt
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
sh.Run "cmd /c agent.bat", 0, False

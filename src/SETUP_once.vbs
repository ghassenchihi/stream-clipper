' Double-click ONCE: the clipper agent starts now (no window) and every time Windows starts.
Set sh = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
here = fs.GetParentFolderName(WScript.ScriptFullName)
startup = sh.SpecialFolders("Startup")
Set f = fs.CreateTextFile(startup & "\clipper_agent.vbs", True)
f.WriteLine "Set sh = CreateObject(""WScript.Shell"")"
f.WriteLine "sh.CurrentDirectory = """ & here & """"
f.WriteLine "sh.Run ""cmd /c agent.bat"", 0, False"
f.Close
sh.CurrentDirectory = here
sh.Run "cmd /c agent.bat", 0, False
MsgBox "Clipper agent is running in the background and will start with Windows. You never need to click again.", 64, "Clipper"

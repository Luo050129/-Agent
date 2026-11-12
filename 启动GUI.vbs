' 双击启动「墟」图形界面 —— 全程不出现命令行窗口
Option Explicit

Dim fso, wsh, here, gui, list, i, py, rc

Set fso = CreateObject("Scripting.FileSystemObject")
Set wsh = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName)
gui = here & "\gui.py"

If Not fso.FileExists(gui) Then
    MsgBox "找不到 gui.py：" & vbCrLf & gui, 16, "启动失败"
    WScript.Quit 1
End If

' 依次尝试带 tkinter 的 pythonw，谁先通过就用谁
list = Array( _
    "C:\Python314\pythonw.exe", _
    "C:\Python313\pythonw.exe", _
    "C:\Python312\pythonw.exe", _
    "pythonw.exe" _
)

For i = 0 To UBound(list)
    py = list(i)
    On Error Resume Next
    Err.Clear
    rc = wsh.Run("""" & py & """ -c ""import tkinter""", 0, True)
    If Err.Number = 0 Then
        If rc = 0 Then
            On Error GoTo 0
            wsh.CurrentDirectory = here
            wsh.Run """" & py & """ """ & gui & """", 0, False
            WScript.Quit 0
        End If
    End If
    On Error GoTo 0
Next

MsgBox "没找到带 tkinter 的 pythonw，无法启动图形界面。" & vbCrLf & vbCrLf & _
       "可手动运行：" & vbCrLf & "C:\Python314\pythonw.exe gui.py", 48, "启动失败"
WScript.Quit 1

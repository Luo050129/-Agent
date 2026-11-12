"""交互层。

console.py 是命令行，window.py 是图形界面。两者只依赖 core 的事件接口，互不认识。

这里不 import window：图形界面需要 tkinter，不能拖累命令行启动。
"""

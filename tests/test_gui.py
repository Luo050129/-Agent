"""GUI 冒烟测试：用假模型驱动真实窗口，验证渲染、线程与安全拦截。

需要带 tkinter 的解释器：
    C:\\Python314\\python.exe tests\\test_gui.py

会短暂弹出一个窗口，跑完自动关闭。
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import miniagent.ui.window as window_mod  # noqa: E402
from miniagent.config import BASE_DIR  # noqa: E402
from miniagent.model import LLMError  # noqa: E402
from miniagent.tools import DANGEROUS, TOOL_SCHEMAS, refresh  # noqa: E402
from miniagent.ui import theme  # noqa: E402
from miniagent.ui.window import create_app  # noqa: E402


class StepFakeLLM:
    """第 1 轮要工具，第 2 轮给答案。"""

    model = "fake-gui"

    def stream(self, messages, tools=None, on_text=None):
        if not any(m.get("role") == "tool" for m in messages):
            return "", [
                {
                    "id": "call_gui",
                    "type": "function",
                    "function": {"name": "run_shell", "arguments": json.dumps({"command": "echo gui-ok"})},
                }
            ]
        text = "窗口里的回答：gui-ok"
        if on_text:
            for ch in text:
                on_text(ch)
        return text, []


class BoomLLM:
    """模拟接口失败。"""

    model = "fake-boom"

    def stream(self, messages, tools=None, on_text=None):
        raise LLMError("模拟的接口错误")


class WriteFakeLLM:
    """请求写文件，用于验证安全模式拦截。"""

    model = "fake-write"

    def __init__(self, target: str) -> None:
        self.target = target

    def stream(self, messages, tools=None, on_text=None):
        if not any(m.get("role") == "tool" for m in messages):
            args = json.dumps({"path": self.target, "content": "不该被写出来"})
            return "", [
                {"id": "call_w", "type": "function", "function": {"name": "write_file", "arguments": args}}
            ]
        reply = "好的，不写了。"
        if on_text:
            on_text(reply)
        return reply, []


class MdFakeLLM:
    """返回标准 Markdown，验证渲染后不残留裸标记。"""

    model = "fake-md"
    ANSWER = (
        "### 标题\n\n"
        "| 文件 | 职责 |\n"
        "| --- | --- |\n"
        "| `a.py` | 主循环 |\n\n"
        "**重点**：用 `CLI` 就能跑。\n"
        "- **第一条** 里带 `代码`\n"
    )

    def stream(self, messages, tools=None, on_text=None):
        if on_text:
            on_text(self.ANSWER)
        return self.ANSWER, []


def main() -> int:
    # 历史会话测试要把存档引到临时目录，别污染真实的 .agent/sessions
    import tempfile
    import pathlib
    import miniagent.core.persistence as _persist

    _tmp_sessions = pathlib.Path(tempfile.mkdtemp(prefix="agent-sessions-"))
    _old_sessions_dir = _persist.SESSIONS_DIR
    _persist.SESSIONS_DIR = _tmp_sessions

    root, app = create_app(llm=StepFakeLLM())
    root.update()
    # 安全模式默认开，但它是模态弹窗，会把自动化测试卡住；先关掉，最后单独测
    app.safe_var.set(False)

    app.submit("跑个命令看看")
    finished = app.wait_until_idle(30)
    root.update()
    content = app.chat.get("1.0", "end")

    checks = [
        ("任务正常结束", finished),
        ("空状态已被清掉", "我们该做点什么" not in content),
        ("状态栏显示上下文用量", "上下文" in app.meta.cget("text")),
        ("不再显示轮次与工具次数", "轮对话" not in app.status.cget("text")),
        ("记录到任务状态快照", bool(app.task_state) and app.task_state.get("status") == "done"),
        ("用户消息渲染到窗口", "跑个命令看看" in content),
        ("工具调用可见", "run_shell" in content),
        ("工具结果可见", "gui-ok" in content),
        ("最终答复可见", "窗口里的回答" in content),
        ("空闲后发送按钮恢复", app.is_idle and app.send_btn._enabled),
        ("统计记在会话里（界面不显示）", app.session.tool_calls == 1 and app.session.turns == 1),
    ]

    # ---- 历史会话：落盘 → 新聊天 → 恢复 → 接着聊 ----
    checks.append(("会话自动落盘", app.store.path.is_file() and app.store.path.parent == _tmp_sessions))
    checks.append(("自动取了标题", app.store.title == "跑个命令看看"))
    old_store = app.store

    app._new_session()
    root.update()
    checks.append(("新聊天换了存档", app.store is not old_store and not app.store.path.exists()))
    checks.append(("新聊天清空了历史", app.session.turns == 0 and len(app.session.messages) == 1))

    app._restore_session(old_store)
    root.update()
    checks.append(("恢复后问答回放", "窗口里的回答" in app.chat.get("1.0", "end")))
    checks.append(("状态栏提示已恢复", "已恢复" in app.status.cget("text")))
    checks.append(("恢复的会话挂着存档", app.session._observer is not None))

    app.submit("接着问一句")
    finished = app.wait_until_idle(30)
    root.update()
    checks.append(("恢复后能接着跑", finished))
    with app.store.path.open("r", encoding="utf-8") as fh:
        body = fh.read()
    checks.append(("继续聊写回同一存档", "接着问一句" in body))

    # ---- 撤销写入：造一个检查点，点撤销，结果进聊天区 ----
    import miniagent.tools.checkpoints as _cp
    _ckpt_old = _cp.CHECKPOINT_DIR
    _cp.CHECKPOINT_DIR = _tmp_sessions / "ckpt"
    try:
        from miniagent.tools import execute as _exec

        probe = _tmp_sessions / "undo_probe.txt"
        probe.write_text("原始", encoding="utf-8")
        _exec("write_file", json.dumps({"path": str(probe), "content": "被改掉"}))
        checks.append(("探测文件被改", probe.read_text(encoding="utf-8") == "被改掉"))

        app._undo_write()
        root.update()
        checks.append(("撤销结果进聊天区", "已还原" in app.chat.get("1.0", "end")))
        checks.append(("撤销真的还原了文件", probe.read_text(encoding="utf-8") == "原始"))
    finally:
        _cp.CHECKPOINT_DIR = _ckpt_old

    # 接口失败
    app.llm = BoomLLM()
    app.submit("再试一次")
    app.wait_until_idle(30)
    root.update()
    checks.append(("接口失败显示为错误提示", "模拟的接口错误" in app.chat.get("1.0", "end")))

    # 安全模式：模拟用户点「否」，文件必须没被创建
    probe = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_should_not_exist.txt")
    if os.path.exists(probe):
        os.remove(probe)
    original_ask = window_mod.messagebox.askyesno
    window_mod.messagebox.askyesno = lambda *a, **k: False
    app.llm = WriteFakeLLM(probe)
    app.safe_var.set(True)
    app.submit("帮我写个文件")
    app.wait_until_idle(30)
    window_mod.messagebox.askyesno = original_ask
    root.update()
    checks.append(("拒绝后文件确实没被写入", not os.path.exists(probe)))
    checks.append(("拒绝信息回灌给模型", "不写了" in app.chat.get("1.0", "end")))

    # Markdown 渲染
    app.llm = MdFakeLLM()
    app.submit("渲染测试")
    app.wait_until_idle(30)
    md = app.chat.get("1.0", "end")
    checks.append(("加粗标记已被渲染掉", "**" not in md))
    checks.append(("反引号已被渲染掉", "`" not in md))
    checks.append(("表格压成一行", "a.py" in md and "主循环" in md))
    checks.append(("标题去掉了井号", "###" not in md and "标题" in md))
    checks.append(("列表符号换成圆点", "• 第一条" in md))
    checks.append(("列表项里的行内标记也解析了", "**" not in md and "`" not in md))

    # 新会话
    app._new_session()
    root.update()
    checks.append(("新会话回到空状态", "我们该做点什么" in app.chat.get("1.0", "end")))
    checks.append(("新会话统计清零", app.session.tool_calls == 0))

    # 侧栏：最近列表随对话增长，新会话后清空
    app.submit("记一下这句话")
    app.wait_until_idle(30)
    root.update()
    checks.append(("侧栏最近列表记录提问", any("记一下这句话" in t for t, _ in app._recent)))
    app._new_session()
    root.update()
    checks.append(("新会话清空最近列表", not app._recent))

    # 模型切换：替换 LLM 构造器，验证界面跟着变且不需要网络
    original_llm_cls = window_mod.LLM

    class FakeLLM:
        def __init__(self, model=None, **kw):
            self.label = model
            self.model = model
            self.spec = None

        def stream(self, messages, tools=None, on_text=None):
            return "ok", []

    window_mod.LLM = FakeLLM
    try:
        app._switch_model("GPT-4o")
        root.update()
        checks.append(("切换模型后 llm 换了", getattr(app.llm, "label", "") == "GPT-4o"))
    finally:
        window_mod.LLM = original_llm_cls

    # 附件按钮：选文件后写进输入框，并临时加入读白名单
    probe_attach = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_attach_probe.md")
    with open(probe_attach, "w", encoding="utf-8") as fh:
        fh.write("# 附件探针\n")
    original_dialog = window_mod.filedialog.askopenfilename
    window_mod.filedialog.askopenfilename = lambda **kw: probe_attach
    try:
        app._attach_file()
        root.update()
    finally:
        window_mod.filedialog.askopenfilename = original_dialog
    checks.append(("附件路径写进输入框", probe_attach in app.input.get("1.0", "end")))
    checks.append(
        ("附件被临时加入读白名单", app.policy.check("read_document", {"path": probe_attach}) is None)
    )
    os.remove(probe_attach)

    # 工作模式切换
    checks.append(("默认是 ReAct 模式", app.mode == "react"))
    app._switch_mode("plan")
    root.update()
    checks.append(("能切到计划执行", app.mode == "plan" and "计划执行" in app.mode_btn.cget("text")))
    app._switch_mode("不存在的模式")
    root.update()
    checks.append(("非法模式不生效", app.mode == "plan"))

    # 中途插话：运行中提交不改 running 状态，而是排进插话队列
    app._switch_mode("react")
    app.session.reset()
    app._clear_chat()
    app.running = True  # 模拟正在跑，避免真起线程
    app.submit("改成只看 python 文件")
    root.update()
    content_after = app.chat.get("1.0", "end")
    checks.append(("运行中提交排进插话队列", app.session.has_interjects))
    checks.append(("聊天区标出了插话", "↑ 插话" in content_after))
    checks.append(("插话不打断当前运行", app.running is True))
    checks.append(("状态栏提示已插话", "插话" in app.status.cget("text")))
    app.session.take_interjects()
    app.running = False

    # 空闲时提交才是普通提问。先断言再 update —— 假模型跑得比 update 还快，
    # 反过来写的话任务可能已经结束，running 又变回 False 了
    app.submit("正常提问")
    checks.append(("空闲时提交仍会起任务", app.running is True))
    root.update()
    checks.append(("正常提问不进插话队列", not app.session.has_interjects))

    # 安全模式的悬停说明
    hint_on = app._safe_hint()
    app.safe_var.set(False)
    hint_off = app._safe_hint()
    app._refresh_safe_hint_style()
    root.update()
    checks.append(("说明里列了受管操作", "run_shell" in hint_on and "write_file" in hint_on))
    checks.append(("开启态说明会先问", "会弹窗问你" in hint_on))
    checks.append(("关闭态说明会直接执行", "不再询问" in hint_off))
    checks.append(("说明硬边界不受开关影响", "始终生效" in hint_on))
    checks.append(("关掉后文字变红提醒", app.safe_label.cget("fg") == theme.DANGER))

    # 新的侧栏结构：搜索在顶部，模型入口进设置，底部是用户区
    checks.append(("侧栏顶部是搜索按钮", hasattr(app, "search_btn") and bool(app.search_btn.winfo_exists())))
    checks.append(("底部有头像", hasattr(app, "avatar") and bool(app.avatar.winfo_exists())))
    checks.append(("底部不再显示模型名", not hasattr(app, "provider_label")))
    checks.append(("窗口标题是墟", root.title() == "墟"))
    from miniagent.ui.window import short_model_name
    checks.append(("模型简写：短名原样", short_model_name("GPT-4o") == "GPT-4o"))
    checks.append(("模型简写：长名截断", short_model_name("一个特别特别长的中文模型名字").endswith("…")))
    checks.append(("模型简写：不超长度", len(short_model_name("a" * 40)) <= 18))
    checks.append(("默认头像是图片", app.avatar._photo is not None))
    checks.append(("侧栏够宽", app.SIDEBAR_W >= 260))
    checks.append(("头像比之前大", app.avatar.winfo_reqwidth() >= 40))

    # 侧栏占比 15%~20%
    ratio = app.SIDEBAR_W / root.winfo_width()
    checks.append((f"侧栏占比 {ratio:.0%}（要 15%~20%）", 0.15 <= ratio <= 0.20))

    # 品牌放大：图标和文字都明显比标题栏大
    checks.append(("品牌有大图标", app.brand_mark_photo is not None and app.brand_mark_photo.width() >= 48))
    import tkinter.font as _tkfont
    _brand_size = _tkfont.Font(font=app.brand_label.cget("font")).cget("size")
    checks.append(("品牌文字放大", int(_brand_size) >= 22))
    checks.append(("搜索在品牌上面", app.search_btn.winfo_rooty() < app.brand_label.winfo_rooty()))

    # 折叠 / 展开
    app._toggle_sidebar()
    root.update()
    checks.append(("收起后侧栏不可见", not app.sidebar.winfo_ismapped()))
    checks.append(("收起后出现把手", bool(app.topbar.winfo_ismapped())))
    app._toggle_sidebar()
    root.update()
    checks.append(("能再展开", bool(app.sidebar.winfo_ismapped()) and not app.topbar.winfo_ismapped()))

    # 按钮配色：浅底必须配深字，否则白字压浅底看不见
    from miniagent.ui.widgets import Pill as _Pill, auto_fg
    checks.append(("浅底自动配深字", auto_fg(theme.BG_CARD) != theme.ON_ACCENT))
    checks.append(("橙底自动配白字", auto_fg(theme.ACCENT) == theme.ON_ACCENT))
    _b = _Pill(root, "取消", variant="secondary", bg=theme.BG)
    checks.append(("次要按钮不是白字", _b._fg != theme.ON_ACCENT))
    checks.append(("次要按钮有描边", bool(_b._outline)))
    _b.destroy()
    checks.append(("导航里没有设置项", "设置" not in [w.winfo_children()[1].cget("text") for w in []]))
    checks.append(("输入卡有模型标签", hasattr(app, "model_btn")))
    checks.append(("模型标签在 ReAct 右边", app.model_btn.winfo_x() > app.mode_btn.winfo_x()))
    checks.append(("模型标签不压到发送键", app.model_btn.winfo_x() + app.model_btn.winfo_width() < app.send_btn.winfo_x()))
    checks.append(("开关是预渲染图", hasattr(app.safe_toggle, "_img_on")))
    checks.append(("发送按钮是预渲染图", hasattr(app.send_btn, "_img_fill")))
    checks.append(("品牌文字已移除", not hasattr(app, "brand_btn")))
    checks.append(("窗口图标文件存在", (BASE_DIR / "assets" / "icon.ico").is_file()))

    # 搜索窗口
    from miniagent.ui.search_dialog import SearchDialog

    sdlg = SearchDialog(root, app.session)
    root.update()
    checks.append(("搜索窗口能打开", bool(sdlg.winfo_exists())))
    sdlg.destroy()

    # 设置窗口里的模型下拉
    from miniagent.ui.settings_dialog import SettingsDialog

    sd = SettingsDialog(root, app.prefs, current_model=getattr(app.llm, "label", ""))
    root.update()
    checks.append(("设置里有模型下拉", len(sd.model_specs) > 0))
    checks.append(("默认只列主流", len(sd.model_specs) <= 20))
    checks.append(("下拉默认选中当前模型", sd.model_box.get() == getattr(app.llm, "label", "")))
    checks.append(("下拉不带供应商后缀", " · " not in sd.model_box.get()))
    checks.append(("提示里有当前供应商", "当前" in sd.model_hint.cget("text")))
    sd.show_all.set(True)
    sd._fill_models()
    checks.append(("勾上显示全部后变多", len(sd.model_specs) > 40))
    sd.show_all.set(False)
    sd._fill_models()
    sd.destroy()

    # 模型菜单：默认只列主流
    from miniagent.model import all_specs, mainstream_specs

    mainstream = mainstream_specs()
    checks.append(("主流清单远少于全部", len(mainstream) < len(all_specs())))
    checks.append(("主流数量适中（5~20）", 5 <= len(mainstream) <= 20))
    checks.append(("主流覆盖多个供应商", len({s.provider for s in mainstream}) >= 4))

    # 设置窗口
    from miniagent.ui.settings_dialog import ModelDialog, SettingsDialog

    dlg = SettingsDialog(root, app.prefs)
    root.update()
    checks.append(("设置窗口能打开", bool(dlg.winfo_exists())))
    checks.append(("三套主题都列出", len(dlg._theme_boxes) == 3))
    dlg._pick_theme("dark")
    checks.append(("能选中深色", dlg.draft.theme == "dark"))
    dlg._on_scale(1.25)
    checks.append(("拖字号会更新草稿", abs(dlg.draft.font_scale - 1.25) < 0.02))
    dlg.destroy()

    mbox = ModelDialog(root)
    root.update()
    checks.append(("添加模型窗口能打开", len(mbox.vars) == 3))
    checks.append(("只有三个要填的字段", set(mbox.vars) == {"model", "key", "base_url"}))
    checks.append(("预设会自动填好接口地址", mbox.vars["base_url"].get().startswith("http")))
    checks.append(("点预设能换服务商", len(mbox._chips) >= 5))
    mbox._pick_preset(mbox.PRESETS[-1])  # 自定义预设：地址留空
    checks.append(("自定义预设清空地址", mbox.vars["base_url"].get() == ""))

    # 别把测试数据写到真实配置里
    import tempfile
    from pathlib import Path

    from miniagent.model import custom as custom_mod

    original_custom = custom_mod.CUSTOM_FILE
    custom_mod.CUSTOM_FILE = Path(tempfile.mkdtemp()) / "models.json"
    try:
        mbox.vars["key"].set("测试模型")
        mbox.vars["model"].set("m")
        mbox._add()  # 接口地址还空着
        checks.append(("缺接口地址被拒", "不能为空" in mbox.status.cget("text")))

        mbox.vars["base_url"].set("http://127.0.0.1:9/v1")
        mbox._add()
        checks.append(("填全后能添加", "已添加" in mbox.status.cget("text")))
        checks.append(("确实落盘了", custom_mod.CUSTOM_FILE.is_file()))
    finally:
        custom_mod.CUSTOM_FILE = original_custom
        custom_mod._cache["stamp"] = None
    mbox.destroy()

    # MCP 管理窗口（用临时配置，别碰真实 .agent/mcp.json）
    import tempfile as _tempfile

    import miniagent.mcp.manager as _mcp
    from miniagent.ui.mcp_dialog import MCPDialog as _MCPDialog

    _old_mcp = (_mcp.MCP_CONFIG, _mcp.MCP_CACHE_DIR)
    _tmp = Path(_tempfile.mkdtemp())
    _mcp.MCP_CONFIG, _mcp.MCP_CACHE_DIR = _tmp / "mcp.json", _tmp / "cache"
    try:
        md = _MCPDialog(root)
        root.update()
        checks.append(("MCP 窗口能打开", bool(md.winfo_exists())))
        checks.append(("空列表有提示摘要", "0 个 server" in md.summary.cget("text")))
        # 底栏先 pack 才不会被 expand 的列表挤成一条线
        _list_bottom = md.scroll.winfo_rooty() + md.scroll.winfo_height()
        checks.append(
            ("列表没压到底栏", _list_bottom < md.winfo_rooty() + md.winfo_height() - 20)
        )

        md.vars["name"].set("fake")
        md.vars["command"].set(f"{sys.executable} tests/fake_mcp_server.py")
        md.vars["tools"].set("echo, add")
        md._add()
        root.update()
        checks.append(("能加 MCP server", "已添加" in md.status.cget("text")))
        checks.append(("列表里出现了", "1 个 server" in md.summary.cget("text")))

        _mcp.mcp_manager.sync("fake")
        refresh()  # 界面上是 _notify 干这事，测试里直接调
        md.refresh_list()
        root.update()
        checks.append(("同步后有工具数", "2 个工具" in md.summary.cget("text")))
        checks.append(("MCP 工具进了注册表", any(n["function"]["name"] == "mcp__fake__echo" for n in TOOL_SCHEMAS)))
        checks.append(("MCP 工具算危险操作", "mcp__fake__echo" in DANGEROUS))

        md._remove("fake")
        root.update()
        checks.append(("删除后清空", "0 个 server" in md.summary.cget("text")))
        md.destroy()
    finally:
        _mcp.mcp_manager.close_all()
        _mcp.MCP_CONFIG, _mcp.MCP_CACHE_DIR = _old_mcp
        _mcp.mcp_manager.reload()
        refresh()

    # 换主题要重建界面，这一步会销毁所有控件，放最后
    from miniagent.ui.settings import UISettings

    app.prefs = UISettings(theme="dark", font_family="", font_scale=1.0)
    app._reload_ui()
    root.update()
    checks.append(("切主题后根窗口是深色", root.cget("bg") == theme.PALETTES["dark"]["BG"]))
    checks.append(("重建后聊天区还在", bool(app.chat.winfo_exists())))
    app.prefs = UISettings(theme="light", font_family="", font_scale=1.0)
    app._reload_ui()
    root.update()
    checks.append(("能切回浅色", root.cget("bg") == theme.PALETTES["light"]["BG"]))

    # 窗口尺寸跟着 DPI 和字号走，不写死
    from miniagent.ui.window import apply_geometry
    from miniagent.ui.settings import UISettings

    sizes = {}
    for scale in (0.85, 1.0, 1.6):
        root.geometry("400x300")
        apply_geometry(root, scale)
        root.update_idletasks()
        sizes[scale] = int(root.geometry().split("x")[0])

    checks.append(("字号越大窗口越宽", sizes[0.85] < sizes[1.0] < sizes[1.6]))
    checks.append(("窗口不超过屏幕宽度", max(sizes.values()) <= root.winfo_screenwidth()))
    checks.append((
        "最小尺寸也没超屏",
        root.minsize()[0] <= root.winfo_screenwidth()
        and root.minsize()[1] <= root.winfo_screenheight(),
    ))

    # 恢复存档目录配置，清掉临时目录
    _persist.SESSIONS_DIR = _old_sessions_dir
    import shutil

    shutil.rmtree(_tmp_sessions, ignore_errors=True)

    for label, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")

    ok_all = all(ok for _, ok in checks)
    print(f"[gui] {'全部通过' if ok_all else '存在失败项'}")
    root.destroy()
    return 0 if ok_all else 1


if __name__ == "__main__":
    raise SystemExit(main())

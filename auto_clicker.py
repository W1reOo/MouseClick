# -*- coding: utf-8 -*-
"""
鼠标左键自动连点器 (Auto Clicker)

功能：
  * 自定义连点时间间隔（毫秒 / 秒）
  * 一键启动 / 停止（按钮或全局热键）
  * 无限循环 或 指定点击次数
  * 在当前光标位置点击，或固定到指定坐标点击
  * 启动前倒计时，方便把鼠标移到目标位置

依赖：仅 Python 标准库（tkinter + ctypes 调用 Win32 API），无需第三方库。
"""

import ctypes
import json
import os
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import messagebox, ttk

APP_NAME = "AutoClicker"

# ---------------------------------------------------------------------------
# Win32 API 封装
# ---------------------------------------------------------------------------

try:  # 让窗口在高分屏下不模糊（Windows 8.1+）
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

user32 = ctypes.WinDLL("user32", use_last_error=True)

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004

user32.mouse_event.restype = None
user32.mouse_event.argtypes = [
    wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p
]
user32.SetCursorPos.restype = wintypes.BOOL
user32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
user32.GetCursorPos.restype = wintypes.BOOL
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
user32.GetAsyncKeyState.restype = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

# 可选热键及其虚拟键码
HOTKEY_VK = {
    "F2": 0x71, "F3": 0x72, "F4": 0x73, "F5": 0x74,
    "F6": 0x75, "F7": 0x76, "F8": 0x77, "F9": 0x78,
    "F10": 0x79, "F11": 0x7A, "F12": 0x7B,
}


def get_cursor_pos():
    """获取当前鼠标屏幕坐标 (x, y)。"""
    pt = wintypes.POINT()
    if user32.GetCursorPos(ctypes.byref(pt)):
        return pt.x, pt.y
    return 0, 0


def click_left(press_ms=0, x=None, y=None):
    """发送一次鼠标左键点击（按下 + 抬起）。"""
    if x is not None and y is not None:
        user32.SetCursorPos(int(x), int(y))
        user32.mouse_event(MOUSEEVENTF_MOVE, 0, 0, 0, 0)
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
    if press_ms > 0:
        time.sleep(press_ms / 1000.0)
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


# ---------------------------------------------------------------------------
# 连点工作线程
# ---------------------------------------------------------------------------

class ClickerThread(threading.Thread):
    """后台执行连点；通过 stop_event 随时中止。"""

    def __init__(self, interval_ms, total, pos_mode, pos_x, pos_y, hold_ms, countdown):
        super().__init__(daemon=True)
        self.interval = interval_ms / 1000.0
        self.total = total            # None 表示无限
        self.pos_mode = pos_mode      # "current" / "fixed"
        self.pos_x, self.pos_y = pos_x, pos_y
        self.hold_ms = hold_ms
        self.countdown = countdown
        self.stop_event = threading.Event()
        self.count = 0
        self.state = "countdown"      # countdown / running / done

    def stop(self):
        self.stop_event.set()

    def run(self):
        # 启动倒计时（可随时中止）
        self.countdown_left = self.countdown
        while self.countdown_left > 0:
            if self.stop_event.wait(1):
                return
            self.countdown_left -= 1

        self.state = "running"
        target = None if self.pos_mode == "current" else (self.pos_x, self.pos_y)
        x = y = None
        if target:
            x, y = target

        next_at = time.perf_counter()
        while not self.stop_event.is_set():
            click_left(self.hold_ms, x, y)
            self.count += 1

            if self.total is not None and self.count >= self.total:
                self.state = "done"
                return

            next_at += self.interval
            delay = next_at - time.perf_counter()
            if delay > 0:
                # 分段等待，保证停止指令能立即响应
                self.stop_event.wait(delay)
            else:
                next_at = time.perf_counter()  # 间隔过小跟不上时，避免累积负债


# ---------------------------------------------------------------------------
# 主界面
# ---------------------------------------------------------------------------

HELP_TEXT = """使用说明

1. 间隔：设置每次点击之间的时间，可选「毫秒」或「秒」。
     例如 100 毫秒 = 每秒约 10 次点击。
2. 次数：选择「无限循环」或「指定次数」（达到次数后自动停止）。
3. 位置：默认在光标当前位置点击；也可勾选「固定坐标」，
     点「拾取」按钮后 3 秒内把鼠标移到目标处自动记录。
4. 倒计时：点击启动后等待的秒数，用于把鼠标移到目标位置。
     设为 0 则立即开始。
5. 启动 / 停止：点击按钮，或直接按全局热键（默认 F6，窗口不在焦点时也生效）。

提示：
  * 部分游戏 / 软件使用反作弊或底层输入检测，可能不响应模拟点击。
  * 停止连点最快的方式是再按一次热键（或点击「停止」按钮）。
  * 请遵守各软件与游戏的用户协议，勿用于违规场景。
"""


class AutoClickerApp:
    def __init__(self, root):
        self.root = root
        self.worker = None
        self.lock = threading.Lock()

        root.title("鼠标连点器 v1.0")
        root.resizable(False, False)

        self.cfg_path = os.path.join(
            os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME, "config.json"
        )

        self._build_ui()
        self._load_config()
        self._start_hotkey_watcher()
        self._poll_status()

        root.protocol("WM_DELETE_WINDOW", self.on_close)

    # ---------------- UI 构建 ----------------

    def _build_ui(self):
        pad = {"padx": 10, "pady": 6}

        # 标题
        tk.Label(self.root, text="鼠标左键自动连点器", font=("Microsoft YaHei", 14, "bold")).pack(pady=(12, 6))

        # ---- 间隔 ----
        box = ttk.LabelFrame(self.root, text="点击间隔")
        box.pack(fill="x", **pad)
        row = tk.Frame(box)
        row.pack(fill="x", padx=10, pady=8)
        self.interval_var = tk.StringVar(value="100")
        ttk.Entry(row, textvariable=self.interval_var, width=10, justify="center").pack(side="left")
        self.unit_var = tk.StringVar(value="毫秒")
        ttk.Combobox(row, textvariable=self.unit_var, values=["毫秒", "秒"],
                     state="readonly", width=6).pack(side="left", padx=6)
        tk.Label(row, text="（100 毫秒 ≈ 10 次/秒）", fg="#888").pack(side="left")

        # ---- 次数 ----
        box = ttk.LabelFrame(self.root, text="点击次数")
        box.pack(fill="x", **pad)
        self.mode_var = tk.StringVar(value="infinite")
        r1 = tk.Frame(box); r1.pack(fill="x", padx=10, pady=(8, 2))
        ttk.Radiobutton(r1, text="无限循环", value="infinite",
                        variable=self.mode_var, command=self._sync_mode).pack(side="left")
        r2 = tk.Frame(box); r2.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Radiobutton(r2, text="指定次数：", value="count",
                        variable=self.mode_var, command=self._sync_mode).pack(side="left")
        self.count_var = tk.StringVar(value="10")
        self.count_entry = ttk.Entry(r2, textvariable=self.count_var, width=10, justify="center", state="disabled")
        self.count_entry.pack(side="left", padx=4)
        tk.Label(r2, text="次").pack(side="left")

        # ---- 位置 ----
        box = ttk.LabelFrame(self.root, text="点击位置")
        box.pack(fill="x", **pad)
        r3 = tk.Frame(box); r3.pack(fill="x", padx=10, pady=(8, 2))
        self.pos_var = tk.StringVar(value="current")
        ttk.Radiobutton(r3, text="当前光标位置", value="current",
                        variable=self.pos_var, command=self._sync_mode).pack(side="left")
        r4 = tk.Frame(box); r4.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Radiobutton(r4, text="固定坐标：", value="fixed",
                        variable=self.pos_var, command=self._sync_mode).pack(side="left")
        self.x_var = tk.StringVar(value="0")
        self.y_var = tk.StringVar(value="0")
        self.x_entry = ttk.Entry(r4, textvariable=self.x_var, width=6, justify="center", state="disabled")
        self.x_entry.pack(side="left")
        tk.Label(r4, text="X").pack(side="left", padx=(2, 6))
        self.y_entry = ttk.Entry(r4, textvariable=self.y_var, width=6, justify="center", state="disabled")
        self.y_entry.pack(side="left")
        tk.Label(r4, text="Y").pack(side="left", padx=(2, 6))
        self.pick_btn = ttk.Button(r4, text="拾取", width=6, state="disabled", command=self.pick_position)
        self.pick_btn.pack(side="left")

        # ---- 其它 ----
        box = ttk.LabelFrame(self.root, text="其它设置")
        box.pack(fill="x", **pad)
        r5 = tk.Frame(box); r5.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(r5, text="启动倒计时：").pack(side="left")
        self.delay_var = tk.StringVar(value="3")
        ttk.Entry(r5, textvariable=self.delay_var, width=6, justify="center").pack(side="left", padx=4)
        tk.Label(r5, text="秒").pack(side="left")
        r6 = tk.Frame(box); r6.pack(fill="x", padx=10, pady=(4, 4))
        tk.Label(r6, text="按下保持：").pack(side="left")
        self.hold_var = tk.StringVar(value="0")
        ttk.Entry(r6, textvariable=self.hold_var, width=6, justify="center").pack(side="left", padx=4)
        tk.Label(r6, text="毫秒").pack(side="left")
        r7 = tk.Frame(box); r7.pack(fill="x", padx=10, pady=(4, 8))
        tk.Label(r7, text="全局热键：").pack(side="left")
        self.hotkey_var = tk.StringVar(value="F6")
        ttk.Combobox(r7, textvariable=self.hotkey_var, values=list(HOTKEY_VK.keys()),
                     state="readonly", width=6).pack(side="left", padx=4)
        self.top_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(r7, text="窗口置顶", variable=self.top_var,
                        command=self._apply_topmost).pack(side="left", padx=(10, 0))

        # ---- 控制 ----
        ctrl = tk.Frame(self.root)
        ctrl.pack(fill="x", pady=(10, 4))
        self.start_btn = tk.Button(ctrl, text="启 动", font=("Microsoft YaHei", 12, "bold"),
                                   bg="#2e7d32", fg="white", width=12, height=1,
                                   relief="flat", cursor="hand2", command=self.toggle)
        self.start_btn.pack(side="left", padx=(20, 8))
        ttk.Button(ctrl, text="使用说明", width=10, command=self.show_help).pack(side="left", padx=8)

        # ---- 状态 ----
        self.status_var = tk.StringVar(value="状态：就绪")
        self.count_lbl_var = tk.StringVar(value="已点击：0 次")
        tk.Label(self.root, textvariable=self.status_var, fg="#1565c0",
                 font=("Microsoft YaHei", 11, "bold")).pack(pady=(6, 0))
        tk.Label(self.root, textvariable=self.count_lbl_var, fg="#555").pack()

        self._apply_topmost()
        self._sync_mode()
        self._fit_window()

    def _fit_window(self):
        """按内容自适应窗口大小，避免底部控件被裁剪。"""
        self.root.update_idletasks()
        w = max(380, self.root.winfo_reqwidth() + 20)
        h = self.root.winfo_reqheight() + 4
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 3
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    # ---------------- 交互逻辑 ----------------

    def _sync_mode(self):
        """根据选项启用 / 禁用相关输入框。"""
        self.count_entry.configure(state="normal" if self.mode_var.get() == "count" else "disabled")
        fixed = self.pos_var.get() == "fixed"
        for w in (self.x_entry, self.y_entry):
            w.configure(state="normal" if fixed else "disabled")
        self.pick_btn.configure(state="normal" if fixed else "disabled")

    def _apply_topmost(self):
        self.root.attributes("-topmost", self.top_var.get())

    def _read_int(self, var, name, minimum=0):
        try:
            v = int(float(var.get()))
        except ValueError:
            raise ValueError(f"「{name}」必须是一个数字")
        if v < minimum:
            raise ValueError(f"「{name}」不能小于 {minimum}")
        return v

    def is_running(self):
        return self.worker is not None and self.worker.is_alive()

    def toggle(self):
        self.stop() if self.is_running() else self.start()

    def start(self):
        if self.is_running():
            return
        try:
            interval = self._read_int(self.interval_var, "点击间隔", 1)
            if self.unit_var.get() == "秒":
                interval *= 1000
            total = None
            if self.mode_var.get() == "count":
                total = self._read_int(self.count_var, "点击次数", 1)
            pos_x = pos_y = 0
            if self.pos_var.get() == "fixed":
                pos_x = self._read_int(self.x_var, "X 坐标", 0)
                pos_y = self._read_int(self.y_var, "Y 坐标", 0)
            hold = self._read_int(self.hold_var, "按下保持", 0)
            countdown = self._read_int(self.delay_var, "启动倒计时", 0)
            if hold >= interval:
                raise ValueError("「按下保持」必须小于点击间隔")
        except ValueError as e:
            messagebox.showwarning("输入有误", str(e))
            return

        self.worker = ClickerThread(interval, total, self.pos_var.get(),
                                    pos_x, pos_y, hold, countdown)
        self.worker.start()
        self.start_btn.configure(text="停 止", bg="#c62828")
        self.status_var.set(f"状态：{countdown} 秒后开始…")
        self.count_lbl_var.set("已点击：0 次")

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.start_btn.configure(text="启 动", bg="#2e7d32")
        self.status_var.set("状态：已停止")

    def pick_position(self):
        """3 秒后自动记录鼠标当前坐标。"""
        tip = tk.Toplevel(self.root)
        tip.title("拾取坐标")
        tip.geometry("260x90")
        tip.resizable(False, False)
        tip.attributes("-topmost", True)
        var = tk.StringVar(value="3")
        tk.Label(tip, textvariable=var, font=("Microsoft YaHei", 22, "bold")).pack(pady=4)
        tk.Label(tip, text="请把鼠标移到目标位置…").pack()

        def tick(n):
            var.set(str(n))
            if n <= 0:
                x, y = get_cursor_pos()
                self.x_var.set(str(x))
                self.y_var.set(str(y))
                tip.destroy()
            else:
                tip.after(1000, tick, n - 1)

        tick(3)

    def _start_hotkey_watcher(self):
        """后台轮询全局热键（无需焦点，兼容无管理员权限）。"""
        def watch():
            prev = False
            while True:
                vk = HOTKEY_VK.get(self.hotkey_var.get(), 0x75)
                down = bool(user32.GetAsyncKeyState(vk) & 0x8000)
                if down and not prev:
                    self.root.after(0, self.toggle)
                prev = down
                time.sleep(0.03)

        threading.Thread(target=watch, daemon=True).start()

    def _poll_status(self):
        w = self.worker
        if w is not None:
            if w.is_alive():
                if w.state == "countdown":
                    self.status_var.set(f"状态：{w.countdown_left} 秒后开始…")
                else:
                    self.status_var.set("状态：连点中（按热键停止）")
                self.count_lbl_var.set(f"已点击：{w.count} 次")
            else:
                if w.state == "done":
                    self.count_lbl_var.set(f"已点击：{w.count} 次")
                    self.status_var.set("状态：已完成设定次数")
                    self.start_btn.configure(text="启 动", bg="#2e7d32")
                    self.worker = None
        self.root.after(100, self._poll_status)

    def show_help(self):
        messagebox.showinfo("使用说明", HELP_TEXT)

    # ---------------- 配置持久化 ----------------

    def _collect_config(self):
        return {
            "interval": self.interval_var.get(), "unit": self.unit_var.get(),
            "mode": self.mode_var.get(), "count": self.count_var.get(),
            "pos_mode": self.pos_var.get(), "x": self.x_var.get(), "y": self.y_var.get(),
            "hold": self.hold_var.get(), "delay": self.delay_var.get(),
            "hotkey": self.hotkey_var.get(), "topmost": self.top_var.get(),
        }

    def _save_config(self):
        try:
            os.makedirs(os.path.dirname(self.cfg_path), exist_ok=True)
            with open(self.cfg_path, "w", encoding="utf-8") as f:
                json.dump(self._collect_config(), f, ensure_ascii=False)
        except Exception:
            pass

    def _load_config(self):
        try:
            with open(self.cfg_path, "r", encoding="utf-8") as f:
                c = json.load(f)
            self.interval_var.set(c.get("interval", "100"))
            self.unit_var.set(c.get("unit", "毫秒"))
            self.mode_var.set(c.get("mode", "infinite"))
            self.count_var.set(c.get("count", "10"))
            self.pos_var.set(c.get("pos_mode", "current"))
            self.x_var.set(c.get("x", "0"))
            self.y_var.set(c.get("y", "0"))
            self.hold_var.set(c.get("hold", "0"))
            self.delay_var.set(c.get("delay", "3"))
            self.hotkey_var.set(c.get("hotkey", "F6"))
            self.top_var.set(c.get("topmost", True))
            self._sync_mode()
        except Exception:
            pass

    def on_close(self):
        self.stop()
        self._save_config()
        self.root.destroy()


def main():
    root = tk.Tk()
    AutoClickerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()

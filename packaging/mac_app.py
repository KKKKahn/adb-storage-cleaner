#!/usr/bin/env python3
"""macOS app shell: wait for an ADB device, then open Klean Studio in WKWebView."""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import webview

from klean.studio import prepare_studio

if sys.stdout is None:
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - lives for the process
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w")  # noqa: SIM115 - lives for the process


def _resource_root() -> Path:
    bundled = getattr(sys, "_MEIPASS", None)
    if bundled:
        return Path(bundled)
    return Path(__file__).resolve().parents[1]


def _find_tool(name: str, env_name: str) -> Path:
    override = os.environ.get(env_name)
    if override and Path(override).is_file():
        return Path(override)
    bundled = _resource_root() / "bin" / name
    if bundled.is_file():
        return bundled
    found = shutil.which(name)
    if found:
        return Path(found)
    raise RuntimeError(f"找不到 {name}，请重新安装 klean")


ADB = _find_tool("adb", "KLEAN_ADB")
FFMPEG = _find_tool("ffmpeg", "KLEAN_FFMPEG")
FFPROBE = _find_tool("ffprobe", "KLEAN_FFPROBE")

WAITING_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Klean</title>
<style>
  :root { color-scheme: light dark; }
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; display: grid; place-items: center;
    color: #f4f7ff; font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", sans-serif;
    background:
      radial-gradient(900px 600px at 20% 0%, rgba(83, 96, 255, 0.20), transparent 60%),
      radial-gradient(700px 500px at 100% 100%, rgba(120, 70, 255, 0.16), transparent 60%),
      #0b0f17;
  }
  .card { width: min(620px, 88vw); padding: 42px 44px; text-align: center; }
  .logo {
    width: 78px; height: 78px; margin: 0 auto 20px;
    display: grid; place-items: center;
    background: none; box-shadow: none;
  }
  .bolt {
    width: 36px; height: 54px;
    background: linear-gradient(160deg, #38bdf8 0%, #6366f1 45%, #a855f7 75%, #f472b6 100%);
    clip-path: polygon(60% 0, 8% 56%, 44% 56%, 34% 100%, 92% 40%, 55% 40%);
    filter: drop-shadow(0 6px 18px rgba(91, 115, 255, .35));
  }
  h1 { margin: 0; font-size: 32px; letter-spacing: -.5px; }
  .sub { margin: 8px 0 30px; color: #9aa6bd; font-size: 14px; }
  .status-row { display: flex; align-items: center; justify-content: center; gap: 10px; min-height: 30px; }
  .spinner {
    width: 18px; height: 18px; border-radius: 50%; flex: 0 0 auto;
    border: 2px solid rgba(255,255,255,.18); border-top-color: #7c8cff;
    animation: spin .8s linear infinite;
  }
  .spinner.hidden { display: none; }
  #status { font-size: 16px; font-weight: 600; }
  #status.err { color: #ff9a9a; }
  #detail { margin: 10px auto 0; max-width: 500px; color: #93a0b8; font-size: 13px; line-height: 1.6; min-height: 42px; }
  .steps {
    margin: 26px auto 0; max-width: 430px; padding: 16px 20px; text-align: left;
    background: rgba(255,255,255,.045); border: 1px solid rgba(255,255,255,.08);
    border-radius: 14px; color: #b8c3d8; font-size: 13px; line-height: 1.9;
  }
  .steps b { color: #fff; font-weight: 600; }
  button {
    margin-top: 24px; padding: 9px 20px; border-radius: 10px; cursor: pointer;
    border: 1px solid rgba(255,255,255,.16); background: rgba(255,255,255,.08); color: #fff;
    font-size: 13px; font-weight: 600;
  }
  button:hover { background: rgba(255,255,255,.14); }
  button.hidden { display: none; }
  .privacy { margin-top: 26px; color: #6f7b91; font-size: 11px; }
  .overlay {
    position: fixed; inset: 0; z-index: 2147483647; display: grid; place-items: center;
    background: rgba(7, 11, 18, .94); backdrop-filter: blur(8px);
  }
  .overlay .panel {
    width: min(520px, 86vw); padding: 34px; text-align: center;
    background: #111827; border: 1px solid rgba(255,255,255,.1); border-radius: 18px;
    box-shadow: 0 24px 70px rgba(0,0,0,.5);
  }
  .overlay h2 { margin: 0 0 10px; font-size: 20px; }
  .overlay p { margin: 0; color: #9aa6bd; font-size: 13px; line-height: 1.7; }
  @media (prefers-color-scheme: light) {
    body {
      background:
        radial-gradient(900px 600px at 20% 0%, rgba(83, 96, 255, 0.12), transparent 60%),
        radial-gradient(700px 500px at 100% 100%, rgba(168, 85, 247, 0.10), transparent 60%),
        #f3f5f9;
      color: #171a21;
    }
    .sub, #detail, .privacy { color: #687083; }
    #status { color: #171a21; }
    .steps { background: rgba(0,0,0,.03); border-color: rgba(0,0,0,.08); color: #3f4657; }
    .steps b { color: #171a21; }
    button { border-color: rgba(0,0,0,.12); background: rgba(0,0,0,.04); color: #171a21; }
    .spinner { border-color: rgba(0,0,0,.12); border-top-color: #3f6df6; }
    .overlay { background: rgba(243,245,249,.94); }
    .overlay .panel { background: #fff; border-color: #dfe4ec; box-shadow: 0 24px 70px rgba(20,28,45,.18); }
    .overlay h2 { color: #171a21; }
    .overlay p { color: #687083; }
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
<div class="card">
  <div class="logo"><span class="bolt"></span></div>
  <h1>Klean</h1>
  <p class="sub">Android 实况照片压缩与存储清理</p>
  <div class="status-row">
    <div id="spinner" class="spinner"></div>
    <span id="status">正在查找手机…</span>
  </div>
  <p id="detail">用 USB 数据线连接手机，并打开 USB 调试。</p>
  <div class="steps">
    <div><b>1.</b> 用支持数据传输的 USB 线连接手机</div>
    <div><b>2.</b> 打开「开发者选项 → USB 调试」</div>
    <div><b>3.</b> 在手机上点击「允许」，建议勾选始终允许</div>
  </div>
  <button id="retry" class="hidden" onclick="retryNow()">重新检测</button>
  <p class="privacy">所有处理都在本机完成 · 不上传任何文件</p>
</div>
<script>
  function setStatus(status, detail, isError) {
    var s = document.getElementById('status');
    s.textContent = status || '';
    s.className = isError ? 'err' : '';
    document.getElementById('detail').textContent = detail || '';
    var sp = document.getElementById('spinner');
    if (isError) sp.classList.add('hidden'); else sp.classList.remove('hidden');
  }
  function setError(title, detail) {
    setStatus(title, detail, true);
    document.getElementById('retry').classList.remove('hidden');
  }
  function retryNow() {
    document.getElementById('retry').classList.add('hidden');
    setStatus('正在重新检测…', '请保持手机连接并解锁。', false);
    if (window.pywebview && window.pywebview.api) window.pywebview.api.retry();
  }
  function showDisconnected() {
    if (document.getElementById('klean-disconnected')) return;
    var d = document.createElement('div');
    d.id = 'klean-disconnected';
    d.className = 'overlay';
    d.innerHTML = '<div class="panel"><h2>手机已断开</h2><p>请重新连接同一台手机并解锁。<br>恢复后会自动刷新工作台。</p></div>';
    document.body.appendChild(d);
  }
  function hideDisconnected() {
    var d = document.getElementById('klean-disconnected');
    if (d) d.remove();
  }
  function showFatal(detail) {
    if (document.getElementById('klean-fatal')) return;
    var d = document.createElement('div');
    d.id = 'klean-fatal';
    d.className = 'overlay';
    d.innerHTML = '<div class="panel"><h2>工作台已停止</h2><p></p><button onclick="retryNow()">重启工作台</button></div>';
    d.querySelector('p').textContent = detail || '请重新连接手机后重试。';
    document.body.appendChild(d);
  }
</script>
</body>
</html>
"""

DISCONNECTED_JS = """(function () {
  if (document.getElementById('klean-disconnected')) return;
  var d = document.createElement('div');
  d.id = 'klean-disconnected';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;background:rgba(7,11,18,.94);backdrop-filter:blur(8px);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;';
  d.innerHTML = '<div style="width:min(520px,86vw);padding:34px;text-align:center;background:#111827;border:1px solid rgba(255,255,255,.1);border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,.5);color:#f4f7ff;"><h2 style="margin:0 0 10px;font-size:20px;">手机已断开</h2><p style="margin:0;color:#9aa6bd;font-size:13px;line-height:1.7;">请重新连接同一台手机并解锁。<br>恢复后会自动刷新工作台。</p></div>';
  document.body.appendChild(d);
})();"""

HIDE_DISCONNECTED_JS = """(function () {
  var d = document.getElementById('klean-disconnected');
  if (d) d.remove();
})();"""

FATAL_JS = """(function () {
  var detail = __DETAIL__;
  if (document.getElementById('klean-fatal')) return;
  var d = document.createElement('div');
  d.id = 'klean-fatal';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;display:flex;align-items:center;justify-content:center;background:rgba(7,11,18,.94);backdrop-filter:blur(8px);font-family:-apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif;';
  d.innerHTML = '<div style="width:min(520px,86vw);padding:34px;text-align:center;background:#111827;border:1px solid rgba(255,255,255,.1);border-radius:18px;box-shadow:0 24px 70px rgba(0,0,0,.5);color:#f4f7ff;"><h2 style="margin:0 0 10px;font-size:20px;">工作台已停止</h2><p style="margin:0 0 18px;color:#9aa6bd;font-size:13px;line-height:1.7;"></p><button style="padding:9px 20px;border-radius:10px;border:1px solid rgba(255,255,255,.16);background:rgba(255,255,255,.08);color:#fff;font-size:13px;font-weight:600;cursor:pointer;" onclick="window.pywebview.api.retry()">重启工作台</button></div>';
  d.querySelector('p').textContent = detail || '请重新连接手机后重试。';
  document.body.appendChild(d);
})();"""


def fatal_script(detail: str) -> str:
    return FATAL_JS.replace("__DETAIL__", json.dumps(detail, ensure_ascii=False))


class Bridge:
    def __init__(self) -> None:
        self.retry_event = threading.Event()

    def retry(self) -> bool:
        self.retry_event.set()
        return True

    def set_theme(self, theme: str) -> bool:
        with contextlib.suppress(Exception):
            from AppKit import NSApp, NSAppearance
            from PyObjCTools import AppHelper

            name = (
                "NSAppearanceNameDarkAqua"
                if theme == "dark"
                else "NSAppearanceNameAqua"
            )

            def apply() -> None:
                NSApp.setAppearance_(NSAppearance.appearanceNamed_(name))

            AppHelper.callAfter(apply)
        return True


class Controller:
    def __init__(self, window: webview.Window, bridge: Bridge) -> None:
        self.window = window
        self.bridge = bridge
        self.stop = False
        self.server = None
        self.server_thread = None
        self.url = ""
        self.serial = ""
        self.overlay_shown = False
        window.events.closed += self.on_closed

    # ---------------------------------------------------------------- helpers
    def on_closed(self) -> None:
        self.stop = True
        self.bridge.retry_event.set()
        self.stop_backend()

    def evaluate(self, script: str) -> None:
        with contextlib.suppress(Exception):
            self.window.evaluate_js(script)

    def set_status(self, status: str, detail: str = "", error: bool = False) -> None:
        script = "setStatus(%s, %s, %s);" % (
            json.dumps(status, ensure_ascii=False),
            json.dumps(detail, ensure_ascii=False),
            "true" if error else "false",
        )
        self.evaluate(script)

    def set_error(self, title: str, detail: str) -> None:
        script = "setError(%s, %s);" % (
            json.dumps(title, ensure_ascii=False),
            json.dumps(detail, ensure_ascii=False),
        )
        self.evaluate(script)

    def wait_retry(self) -> bool:
        self.bridge.retry_event.clear()
        while not self.stop:
            if self.bridge.retry_event.wait(timeout=0.5):
                return not self.stop
        return False

    def reset_waiting(self) -> None:
        with contextlib.suppress(Exception):
            self.window.set_title("Klean")
        with contextlib.suppress(Exception):
            self.window.load_html(WAITING_HTML)
        time.sleep(0.35)
        self.set_status("正在重新检测…", "请保持手机连接并解锁。")

    def run_adb(self, args: list[str], timeout: int = 12) -> str:
        try:
            proc = subprocess.run(
                [str(ADB), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except Exception:
            return ""
        return (proc.stdout or "") + (proc.stderr or "")

    def devices(self) -> tuple[list[tuple[str, str]], bool, int]:
        output = self.run_adb(["devices", "-l"])
        ready: list[tuple[str, str]] = []
        unauthorized = False
        offline = 0
        for raw in output.splitlines():
            line = raw.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            if state == "device":
                model = next(
                    (item[6:] for item in parts if item.startswith("model:")),
                    "Android",
                )
                ready.append((serial, model))
            elif state == "unauthorized":
                unauthorized = True
            elif state == "offline":
                offline += 1
        return ready, unauthorized, offline

    # --------------------------------------------------------------- lifecycle
    def run(self) -> None:
        while not self.stop:
            self.run_adb(["start-server"], timeout=15)
            serial = self.wait_for_device()
            if not serial or self.stop:
                return
            try:
                runtime = prepare_studio(self.backend_args(serial), status=self.prepare_status)
            except Exception as exc:
                self.set_error("工作台启动失败", str(exc))
                if not self.wait_retry():
                    return
                self.reset_waiting()
                continue

            self.serial = serial
            self.url = runtime.url
            self.server = runtime.server
            self.server_thread = threading.Thread(
                target=self.server.serve_forever, name="klean-studio", daemon=True
            )
            self.server_thread.start()
            self.set_status("已连接手机", "正在打开工作台…")
            try:
                self.window.load_url(self.url)
                self.window.set_title("Klean Studio")
            except Exception:
                pass
            outcome = self.monitor()
            self.stop_backend()
            if outcome == "quit" or self.stop:
                return
            self.reset_waiting()

    def wait_for_device(self) -> str:
        while not self.stop:
            ready, unauthorized, offline = self.devices()
            if ready:
                serial, model = ready[0]
                suffix = "（多台设备，使用第一台）" if len(ready) > 1 else ""
                self.set_status(f"已连接 {model}{suffix}", "正在启动工作台…")
                return serial
            if unauthorized:
                self.set_status(
                    "等待手机授权",
                    "请在手机上点「允许 USB 调试」；勾选“始终允许”后会自动继续。",
                )
            elif offline:
                self.set_status("手机处于离线状态", "请重新插拔数据线，并保持手机解锁。")
            else:
                self.set_status(
                    "未检测到手机",
                    "请用支持数据传输的 USB 线连接手机，并解锁屏幕。",
                )
            if self.stop:
                return ""
            time.sleep(1.2)
        return ""

    def backend_args(self, serial: str) -> SimpleNamespace:
        return SimpleNamespace(
            adb=str(ADB),
            serial=serial,
            host="127.0.0.1",
            port=0,
            rescan=False,
            cached=False,
            max_duration=5000,
            include_unknown=False,
            ffmpeg=str(FFMPEG),
            ffprobe=str(FFPROBE),
            workers=0,
            apply_workers=4,
            no_browser=True,
        )

    def prepare_status(self, message: str) -> None:
        if "扫描" in message:
            self.set_status(
                "首次扫描手机存储…",
                "首次使用大约需要 1–2 分钟，请保持手机解锁并停留在本窗口。",
            )
        elif "媒体" in message:
            self.set_status("正在读取媒体信息…", "马上就好，请保持手机解锁。")
        elif "视频" in message:
            self.set_status("正在读取视频列表…", "视频较多时可能需要几秒。")
        elif "准备" in message:
            self.set_status("正在准备工作台…", "正在生成访问地址。")

    def monitor(self) -> str:
        time.sleep(0.8)
        while not self.stop:
            if self.server_thread is None or not self.server_thread.is_alive():
                self.evaluate(fatal_script("工作台进程已停止。"))
                if self.wait_retry():
                    return "retry"
                return "quit"
            ready, _, _ = self.devices()
            present = any(serial == self.serial for serial, _ in ready)
            if not present and not self.overlay_shown:
                self.evaluate(DISCONNECTED_JS)
                self.overlay_shown = True
            elif present and self.overlay_shown:
                self.evaluate(HIDE_DISCONNECTED_JS)
                self.overlay_shown = False
                with contextlib.suppress(Exception):
                    self.window.load_url(self.url)
            if self.stop:
                return "quit"
            time.sleep(2.0)
        return "quit"

    def stop_backend(self) -> None:
        if self.server is not None:
            try:
                self.server.shutdown()
                self.server.server_close()
            except Exception:
                pass
            self.server = None
        self.server_thread = None


_LOCK_HANDLE = None


def acquire_single_instance() -> bool:
    global _LOCK_HANDLE
    path = Path.home() / "Library" / "Caches" / "klean" / "macapp.lock"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")  # noqa: SIM115 - held for process lifetime
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    _LOCK_HANDLE = handle
    return True


def main() -> None:
    if not acquire_single_instance():
        return
    bridge = Bridge()
    window = webview.create_window(
        "Klean",
        html=WAITING_HTML,
        js_api=bridge,
        width=1180,
        height=800,
        min_size=(900, 620),
        background_color="#0b0f17",
    )
    controller = Controller(window, bridge)
    webview.start(controller.run, gui="cocoa", debug=False)
    controller.stop_backend()


if __name__ == "__main__":
    main()

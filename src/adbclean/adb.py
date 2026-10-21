"""Thin, defensive wrapper around the adb command line client."""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence


class ADBError(RuntimeError):
    """A missing adb binary, no device, or a failed remote command."""

    def __init__(
        self,
        message: str,
        command: Optional[Sequence[str]] = None,
        returncode: Optional[int] = None,
        stderr: str = "",
    ):
        super().__init__(message)
        self.command = list(command or [])
        self.returncode = returncode
        self.stderr = stderr


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    transport_id: str = ""

    @property
    def ready(self) -> bool:
        return self.state == "device"


def quote(value: str) -> str:
    """POSIX quote used when composing commands for the device shell."""
    return shlex.quote(value)


class ADB:
    """One adb session bound to a single device serial."""

    def __init__(self, adb_path: str = "adb", serial: Optional[str] = None):
        self.adb_path = adb_path
        self.serial = serial

    # ------------------------------------------------------------------ process
    def _base(self) -> List[str]:
        command = [self.adb_path]
        if self.serial:
            command.extend(["-s", self.serial])
        return command

    def run(
        self,
        *args: str,
        timeout: int = 120,
        check: bool = True,
    ) -> subprocess.CompletedProcess:
        command = self._base() + [str(arg) for arg in args]
        try:
            result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
        except FileNotFoundError as exc:
            raise ADBError(
                "找不到 adb。请先安装 Android Platform Tools，并确保 adb 在 PATH 中。",
                command,
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise ADBError(f"adb 命令超时（{timeout}s）: {' '.join(command[:4])} ...", command) from exc
        if check and result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise ADBError(
                f"adb 命令失败（退出码 {result.returncode}）: {stderr or '无输出'}",
                command,
                result.returncode,
                stderr,
            )
        return result

    def sh(self, script: str, timeout: int = 120, check: bool = True, quiet: bool = True) -> str:
        """Run a shell snippet on the device and return stdout as text.

        adb merges stderr into stdout for `exec-out`, so `quiet` redirects it
        away by default. Paths inside `script` must be quoted with `quote()`.
        """
        if quiet:
            script = script.rstrip() + " 2>/dev/null"
        # Pass the snippet as a single argv element. adb preserves argv
        # boundaries (shell v2); wrapping it in shell quotes would make the
        # device shell treat the whole snippet as a filename. Paths inside the
        # snippet are already quoted by quote().
        result = self.run("exec-out", "sh", "-c", script, timeout=timeout, check=check)
        return result.stdout.decode("utf-8", "surrogateescape")

    def pull(self, remote: str, local: str, timeout: int = 900) -> bool:
        parent = os.path.dirname(os.path.abspath(local))
        if parent:
            os.makedirs(parent, exist_ok=True)
        result = self.run("pull", "-a", remote, local, timeout=timeout, check=False)
        return result.returncode == 0

    def push(self, local: str, remote: str, timeout: int = 900) -> bool:
        result = self.run("push", local, remote, timeout=timeout, check=False)
        return result.returncode == 0

    def remote_md5(self, path: str, timeout: int = 300) -> Optional[str]:
        text = self.sh(f"md5sum {quote(path)}", timeout=timeout, check=False).strip()
        if not text:
            return None
        digest = text.split()[0].lower()
        if re.fullmatch(r"[0-9a-f]{32}", digest):
            return digest
        return None

    def remote_mtime(self, path: str) -> Optional[int]:
        text = self.sh(f"stat -c %Y {quote(path)}", check=False).strip()
        try:
            return int(text)
        except ValueError:
            return None

    def remote_mode(self, path: str) -> Optional[str]:
        text = self.sh(f"stat -c %a {quote(path)}", check=False).strip()
        return text or None

    def media_scan(self, path: str) -> bool:
        script = (
            "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
            f"-d {quote('file://' + path)}"
        )
        result = self.run("exec-out", "sh", "-c", script, timeout=60, check=False)
        return result.returncode == 0

    # ------------------------------------------------------------------- devices
    @staticmethod
    def list_devices(adb_path: str = "adb") -> List[Device]:
        try:
            result = subprocess.run(
                [adb_path, "devices", "-l"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except FileNotFoundError as exc:
            raise ADBError("找不到 adb。请先安装 Android Platform Tools。") from exc
        except subprocess.TimeoutExpired as exc:
            raise ADBError("adb devices 超时。") from exc
        if result.returncode != 0:
            stderr = result.stderr.decode("utf-8", "replace").strip()
            raise ADBError(f"adb devices 失败: {stderr or '未知错误'}")
        devices: List[Device] = []
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            line = line.strip()
            if not line or line.startswith("List of devices") or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            info = {}
            for token in parts[2:]:
                if ":" in token:
                    key, value = token.split(":", 1)
                    info[key] = value
            devices.append(
                Device(
                    serial=parts[0],
                    state=parts[1],
                    model=info.get("model", ""),
                    product=info.get("product", ""),
                    transport_id=info.get("transport_id", ""),
                )
            )
        return devices

    @classmethod
    def connect(cls, adb_path: Optional[str] = None, serial: Optional[str] = None) -> "ADB":
        adb_path = adb_path or os.environ.get("ADB") or "adb"
        devices = cls.list_devices(adb_path)
        if not devices:
            raise ADBError(
                "未检测到设备。请检查：\n"
                "  1) 手机已用数据线连接，且不是“仅充电”模式；\n"
                "  2) 开发者选项 → USB 调试 已开启；\n"
                "  3) 手机上已点“允许”RSA 授权。"
            )
        serial = serial or os.environ.get("ANDROID_SERIAL")
        if serial:
            matches = [device for device in devices if device.serial == serial]
            if not matches:
                known = ", ".join(device.serial for device in devices)
                raise ADBError(f"未找到设备 {serial}。当前已连接: {known}")
            chosen = matches[0]
        else:
            ready = [device for device in devices if device.ready]
            if len(ready) == 1:
                chosen = ready[0]
            elif len(ready) > 1:
                known = ", ".join(device.serial for device in ready)
                raise ADBError(f"检测到多台设备，请用 --serial 指定：{known}")
            else:
                chosen = devices[0]
        if chosen.state == "unauthorized":
            raise ADBError("设备未授权。请解锁手机，在“允许 USB 调试吗？”弹窗中勾选“始终允许”并确定。")
        if chosen.state != "device":
            raise ADBError(f"设备状态为 {chosen.state}，无法操作。请重新插拔数据线或重启 adb。")
        return cls(adb_path, chosen.serial)

    # --------------------------------------------------------------------- info
    def getprop(self, name: str) -> str:
        return self.sh(f"getprop {quote(name)}", check=False).strip()

    def props(self) -> Dict[str, str]:
        text = self.sh("getprop", check=False)
        result: Dict[str, str] = {}
        for match in re.finditer(r"^\[(.*?)\]: \[(.*?)\]$", text, re.MULTILINE):
            result[match.group(1)] = match.group(2)
        return result

    def sdk(self) -> int:
        try:
            return int(self.getprop("ro.build.version.sdk") or 0)
        except ValueError:
            return 0

    def users(self) -> List[int]:
        text = self.sh("pm list users", check=False)
        return [int(match.group(1)) for match in re.finditer(r"UserInfo\{(\d+):", text)]

    def df(self, path: str = "/data") -> Dict[str, int]:
        text = self.sh(f"df -k {quote(path)}", check=False)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            raise ADBError(f"无法读取 {path} 的存储信息")
        parts = lines[-1].split()
        if len(parts) < 6 or not parts[1].isdigit():
            raise ADBError(f"无法解析 df 输出: {lines[-1]}")
        return {
            "total_kb": int(parts[1]),
            "used_kb": int(parts[2]),
            "avail_kb": int(parts[3]),
            "use_percent": int(parts[4].rstrip("%")) if parts[4].rstrip("%").isdigit() else 0,
        }

    def diskstats(self) -> Dict[str, int]:
        """Parse the subset of `dumpsys diskstats` that is useful in reports.

        Note: several OEM ROMs cache these values and they can be stale; treat
        them as a hint, never as the source of truth.
        """
        text = self.sh("dumpsys diskstats", check=False)
        wanted = {
            "Data-Free",
            "App Size",
            "App Data Size",
            "App Cache Size",
            "Photos Size",
            "Videos Size",
            "Audio Size",
            "Downloads Size",
            "System Size",
            "Other Size",
        }
        result: Dict[str, int] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            if key not in wanted:
                continue
            token = value.strip().split()[0] if value.strip() else ""
            token = token.rstrip("Kk")
            try:
                result[key] = int(token)
            except ValueError:
                continue
        return result

    def content_query(self, uri: str, projection: str, timeout: int = 180) -> str:
        script = f"content query --uri {quote(uri)} --projection {quote(projection)}"
        return self.sh(script, timeout=timeout, check=False)

    def remote_exists(self, path: str) -> bool:
        text = self.sh(f"test -e {quote(path)} && echo yes || echo no", check=False)
        return text.strip().endswith("yes")

    def remote_size(self, path: str) -> Optional[int]:
        text = self.sh(f"stat -c %s {quote(path)}", check=False).strip()
        try:
            return int(text)
        except ValueError:
            return None

"""Opt-in, stdin/stdout adapter for trusted noninteractive local programs."""
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time

from .adapters import DeliveryError

DEFAULTS = {"AI_MODE": "api", "AI_PROGRAM": "", "AI_PROGRAM_ARGS": "[]", "AI_PROGRAM_OUTPUT": "text", "AI_PROGRAM_TIMEOUT": "120"}


def validate(config):
    if config.get("AI_MODE", "api") not in ("api", "program"):
        raise DeliveryError("请选择接口或本地程序模式")
    if config.get("AI_MODE", "api") == "program" and os.environ.get("PAPERPLANE_ALLOW_PROGRAM") == "0":
        raise DeliveryError("此云端实例未启用程序型 Agent，请使用模型 API；服务器程序需由部署者单独安装配置")
    try:
        args = json.loads(config.get("AI_PROGRAM_ARGS", "[]"))
        timeout = int(config.get("AI_PROGRAM_TIMEOUT", "120"))
        if not isinstance(args, list) or len(args) > 80 or any(not isinstance(a, str) or "\0" in a or len(a) > 8000 for a in args):
            raise ValueError()
        if not 5 <= timeout <= 300:
            raise ValueError()
    except (TypeError, ValueError):
        raise DeliveryError("参数必须是 JSON 字符串数组，超时为 5–300 秒") from None
    if config.get("AI_PROGRAM_OUTPUT", "text") not in ("text", "json"):
        raise DeliveryError("输出格式需要为 text 或 json")
    program = config.get("AI_PROGRAM", "")
    resolved = shutil.which(program) if program and "\0" not in program else None
    if config.get("AI_MODE", "api") == "program":
        if not resolved or not Path(resolved).is_file():
            raise DeliveryError("找不到程序，请填写可执行文件的完整路径")
        if os.name == "nt" and Path(resolved).suffix.lower() != ".exe":
            raise DeliveryError("Windows 请使用 .exe；脚本请通过 node.exe 或 python.exe 调用")
    return resolved, args, timeout


def discover():
    script = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/WorkBuddy/resources/app.asar.unpacked/cli/bin/codebuddy"
    node = shutil.which("node")
    if node and script.is_file():
        return [{"name": "WorkBuddy 内置 CodeBuddy（需测试登录）", "program": node,
                 "args": [str(script), "-p", "--output-format", "json", "--tools", "", "--permission-mode", "dontAsk", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--no-session-persistence", "--settings", '{"disableAllHooks":true}'], "output": "json"}]
    return []


def terminate(process):
    if os.name == "nt":
        if process.poll() is not None:
            return
        subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=subprocess.CREATE_NO_WINDOW, timeout=10)
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=10)


def windows_job(process):
    """Attach the suspended child to a kill-on-close job before it can spawn."""
    import ctypes
    from ctypes import wintypes as w
    class BASIC(ctypes.Structure):
        _fields_ = [("ProcessTime", ctypes.c_int64), ("JobTime", ctypes.c_int64), ("LimitFlags", w.DWORD), ("MinWorkingSet", ctypes.c_size_t), ("MaxWorkingSet", ctypes.c_size_t), ("ActiveProcessLimit", w.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ("ReadOps", "WriteOps", "OtherOps", "ReadBytes", "WriteBytes", "OtherBytes")]
    class EXTENDED(ctypes.Structure):
        _fields_ = [("Basic", BASIC), ("Io", IO), ("ProcessMemory", ctypes.c_size_t), ("JobMemory", ctypes.c_size_t), ("PeakProcessMemory", ctypes.c_size_t), ("PeakJobMemory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.restype = w.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, w.LPCWSTR]
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
    kernel.CloseHandle.argtypes = [w.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = EXTENDED()
    limits.Basic.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, int(process._handle)):
        if handle:
            kernel.CloseHandle(handle)
        process.kill(); process.wait()
        raise DeliveryError("无法创建程序进程组，本次调用已停止")
    ntdll = ctypes.WinDLL("ntdll")
    ntdll.NtResumeProcess.argtypes = [w.HANDLE]
    if ntdll.NtResumeProcess(int(process._handle)) != 0:
        kernel.CloseHandle(handle)
        process.wait()
        raise DeliveryError("无法启动程序进程组")
    return lambda: kernel.CloseHandle(handle)


def run(prompt, config, data_dir, stop_event=None):
    def check_stopped():
        if stop_event is not None and stop_event.is_set():
            raise DeliveryError("本地模型调用已停止，未采用本次结果")

    check_stopped()
    config = {key: config.get(key, default) for key, default in DEFAULTS.items()}
    program, args, timeout = validate(config)
    folder = Path(data_dir) / "ai-runs"
    folder.mkdir(exist_ok=True)
    env = dict(os.environ, PYTHONIOENCODING="utf-8", NO_COLOR="1")
    for key in list(env):
        if key.upper().startswith("WECHAT_") or key.upper() in ("DEVTO_API_KEY", "AI_API_KEY"):
            env.pop(key, None)
    # Files avoid pipe deadlocks. Poll sizes so noisy programs cannot fill memory.
    with tempfile.TemporaryDirectory(dir=folder) as work:
        with tempfile.TemporaryFile(dir=work) as inp, tempfile.TemporaryFile(dir=work) as out, tempfile.TemporaryFile(dir=work) as err:
            inp.write(prompt.encode("utf-8")); inp.seek(0)
            check_stopped()
            try:
                process = subprocess.Popen([program, *args], stdin=inp, stdout=out, stderr=err, cwd=work, env=env, shell=False,
                                           creationflags=(subprocess.CREATE_NO_WINDOW | 0x4) if os.name == "nt" else 0, start_new_session=os.name != "nt")
            except OSError:
                raise DeliveryError("程序启动失败，请检查路径、运行环境和执行权限") from None
            deadline = time.monotonic() + timeout
            close_job = windows_job(process) if os.name == "nt" else None
            try:
                while process.poll() is None:
                    check_stopped()
                    if time.monotonic() >= deadline:
                        raise DeliveryError("程序调用超时，已停止本次进程；请检查登录或增加超时时间")
                    if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > 2_000_000:
                        raise DeliveryError("程序输出过多，已停止本次调用")
                    time.sleep(0.05)
                check_stopped()
                if process.returncode:
                    raise DeliveryError(f"程序退出码 {process.returncode}；请先在对应程序的 CLI 中完成登录，确认支持非交互调用")
                if os.fstat(out.fileno()).st_size + os.fstat(err.fileno()).st_size > 2_000_000:
                    raise DeliveryError("程序输出过多")
                out.seek(0)
                body = out.read(2_000_001).decode("utf-8-sig")
                if not body.strip():
                    err.seek(0)
                    diagnostic = err.read(8192).decode("utf-8", errors="replace").lower()
                    if "authentication required" in diagnostic or "please use /login" in diagnostic:
                        raise DeliveryError("本地模型程序尚未登录或登录已失效，请在对应程序中重新登录后重试")
                if config.get("AI_PROGRAM_OUTPUT", "text") == "json":
                    result = json.loads(body)
                    if isinstance(result, list):
                        result = next((item for item in reversed(result) if isinstance(item, dict) and item.get("type") == "result"), None)
                    if not isinstance(result, dict) or result.get("is_error"):
                        raise ValueError()
                    body = result.get("result")
                if not isinstance(body, str) or not body.strip() or len(body) > 200_000:
                    raise ValueError()
                check_stopped()
                return body.replace("\r\n", "\n").strip()
            except (ValueError, UnicodeError):
                raise DeliveryError("程序未返回有效正文；JSON 输出需包含字符串 result，文本输出需为 UTF-8") from None
            finally:
                if close_job:
                    close_job()
                    process.wait(timeout=10)
                    # Descendants may briefly retain the working directory while exiting.
                    time.sleep(0.15)
                else:
                    terminate(process)

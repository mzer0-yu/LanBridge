"""Read-only discovery of local Windows LanBridge runtimes."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor


def command_arguments(command):
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    shell.CommandLineToArgvW.argtypes = [ctypes.c_wchar_p, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(ctypes.c_wchar_p)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    count = ctypes.c_int()
    pointer = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not pointer:
        return []
    try:
        return [pointer[index] for index in range(count.value)]
    finally:
        kernel.LocalFree(ctypes.cast(pointer, ctypes.c_void_p))


def windows_processes():
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    native = ctypes.WinDLL("ntdll")
    psapi.EnumProcesses.argtypes = [ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    psapi.EnumProcesses.restype = wintypes.BOOL
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wintypes.BOOL
    native.NtQueryInformationProcess.argtypes = [wintypes.HANDLE, wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG)]
    native.NtQueryInformationProcess.restype = wintypes.LONG
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum", wintypes.USHORT), ("buffer", ctypes.c_void_p)]
    size = 4096
    while True:
        pids = (wintypes.DWORD * size)()
        used = wintypes.DWORD()
        if not psapi.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
            raise ctypes.WinError(ctypes.get_last_error())
        if used.value < ctypes.sizeof(pids):
            break
        size *= 2
    records = []
    for pid in pids[:used.value // ctypes.sizeof(wintypes.DWORD)]:
        handle = kernel.OpenProcess(0x1000, False, pid)  # Query limited information only.
        if not handle:
            continue
        try:
            image = ctypes.create_unicode_buffer(32768)
            image_length = wintypes.DWORD(len(image))
            if not kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(image_length)):
                continue
            if Path(image.value).name.lower() not in {"python.exe", "pythonw.exe"}:
                continue
            needed = wintypes.ULONG()
            native.NtQueryInformationProcess(handle, 60, None, 0, ctypes.byref(needed))
            if not ctypes.sizeof(UnicodeString) <= needed.value <= 1024 * 1024:
                continue
            buffer = ctypes.create_string_buffer(needed.value)
            if native.NtQueryInformationProcess(handle, 60, buffer, len(buffer), ctypes.byref(needed)) != 0:
                continue
            value = UnicodeString.from_buffer(buffer)
            beginning = ctypes.addressof(buffer)
            if not value.buffer or not beginning <= value.buffer <= beginning + len(buffer) - value.length:
                continue
            command = ctypes.wstring_at(value.buffer, value.length // 2)
            records.append({"ProcessId": pid, "ExecutablePath": image.value, "CommandLine": command})
        finally:
            kernel.CloseHandle(handle)
    return records


def listening_ports():
    from ctypes import wintypes
    import socket
    import struct
    api = ctypes.WinDLL("iphlpapi")
    api.GetExtendedTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD), wintypes.BOOL, wintypes.ULONG, ctypes.c_int, wintypes.ULONG]
    api.GetExtendedTcpTable.restype = wintypes.DWORD
    size = wintypes.DWORD()
    api.GetExtendedTcpTable(None, ctypes.byref(size), False, 2, 3, 0)
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = api.GetExtendedTcpTable(buffer, ctypes.byref(size), False, 2, 3, 0)
        if result == 122:
            continue
        if result:
            raise OSError("Cannot read local TCP listeners")
        count = struct.unpack_from("I", buffer)[0]
        ports = {}
        for index in range(count):
            state, address, port, _, _, pid = struct.unpack_from("6I", buffer, 4 + index * 24)
            if state == 2 and socket.inet_ntoa(struct.pack("I", address)) == "127.0.0.1":
                ports.setdefault(pid, []).append(socket.ntohs(port & 0xffff))
        return ports
    raise OSError("Local TCP listeners changed during query")


def process_candidate(process):
    args = command_arguments(process.get("CommandLine") or "")
    if len(args) < 3:
        return None
    script = Path(args[1])
    if script.name.lower() != "run.py" or "serve" not in args[2:]:
        return None
    if not script.is_absolute():
        # The supported launcher runs from its own project virtual environment.
        executable = Path(process.get("ExecutablePath") or "")
        if executable.parent.name.lower() != "scripts" or executable.parent.parent.name.lower() != ".venv":
            return None
        script = executable.parent.parent.parent / script
    project = script.resolve().parent
    if not (project / "lanbridge" / "admin.py").is_file():
        return None
    data = project / "data"
    if "--data-dir" in args:
        index = args.index("--data-dir")
        if index + 1 >= len(args):
            return None
        data = Path(args[index + 1])
        if not data.is_absolute():
            return None  # Unknown process working directory: do not guess.
    elif any(arg.startswith("--data-dir=") for arg in args):
        data = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--data-dir=")))
        if not data.is_absolute():
            return None
    return {"project": str(project), "data": str(data.resolve()), "pid": process["ProcessId"]}


def discover_instances(current_root, processes=None):
    from run import acquire_runtime, open_existing
    import httpx
    if processes is None:
        if os.name != "nt":
            return {"instances": [], "error": "实例检测目前仅支持 Windows。"}
        try:
            processes = windows_processes()
        except OSError:
            return {"instances": [], "error": "无法读取本机进程，请检查系统权限后重试。"}
    candidates = {}
    for process in processes:
        candidate = process_candidate(process)
        if candidate:
            key = os.path.normcase(candidate["data"])
            if key in candidates:
                candidates[key]["pids"].append(candidate["pid"])
            else:
                candidates[key] = candidate | {"pids": [candidate["pid"]]}
    try:
        ports_by_pid = listening_ports() if os.name == "nt" else {}
    except OSError:
        ports_by_pid = {}
    def verify(candidate):
        data = Path(candidate["data"])
        try:
            if open_existing(data, open_browser=False):
                info = json.loads((data / "runtime.json").read_text(encoding="utf-8"))
                return candidate | {"port": info["admin_port"], "current": data == current_root.resolve(), "legacy": False, "instance": info["instance"], "controllable": bool(info.get("launcher_control"))}
            # Older running versions lack a marker: require the project process,
            # its loopback listener, a held data lock and the admin response shape.
            if not data.exists() or (data / "runtime.json").exists():
                return None
            try:
                lock = acquire_runtime(data)
            except ValueError:
                pass
            else:
                lock.close()
                return None
            for pid in candidate["pids"]:
                for port in ports_by_pid.get(pid, []):
                    try:
                        with httpx.Client(trust_env=False, timeout=0.5, follow_redirects=False) as client:
                            response = client.get(f"http://127.0.0.1:{port}/api/bootstrap")
                        payload = response.json()
                        if response.status_code == 200 and isinstance(payload, dict) and payload.get("remote") is False and all(type(payload.get(key)) is bool for key in ("initialized", "authenticated", "public_client_enabled")) and "scope" in payload and "csrf" in payload:
                            return candidate | {"pid": pid, "port": port, "current": data == current_root.resolve(), "legacy": True, "instance": None, "controllable": False}
                    except (httpx.HTTPError, ValueError):
                        continue
            return None
        except (OSError, ValueError, KeyError):
            return None
    with ThreadPoolExecutor(max_workers=8) as pool:
        instances = [item for item in pool.map(verify, candidates.values()) if item]
    return {"instances": sorted(instances, key=lambda item: (not item["current"], item["port"])), "error": ""}

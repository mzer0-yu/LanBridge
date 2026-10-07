import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

from lanbridge.instances import command_arguments, discover_instances, process_candidate, windows_processes
from lanbridge.service import Service

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows launcher")


def test_candidates_require_lanbridge_script_and_unambiguous_directory(tmp_path):
    project = Path(__file__).resolve().parents[1]
    command = subprocess.list2cmdline([sys.executable, str(project / "run.py"), "--data-dir", str(tmp_path / "data with spaces"), "serve"])
    assert command_arguments(command)[3] == str(tmp_path / "data with spaces")
    candidate = process_candidate({"CommandLine": command, "ProcessId": 123})
    assert candidate["data"] == str(tmp_path / "data with spaces")
    assert process_candidate({"CommandLine": 'python other.py serve', "ProcessId": 1}) is None
    relative = subprocess.list2cmdline([sys.executable, str(project / "run.py"), "--data-dir", "relative-data", "serve"])
    assert process_candidate({"CommandLine": relative, "ProcessId": 1}) is None


def test_two_running_instances_are_verified_and_exited_instance_disappears(tmp_path):
    project = Path(__file__).resolve().parents[1]
    processes = []
    records = []
    sockets = []
    # Reserve four different ports together before releasing them for servers.
    for _ in range(4):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        sockets.append(listener)
    ports = [listener.getsockname()[1] for listener in sockets]
    for listener in sockets:
        listener.close()
    try:
        for index in range(2):
            data = tmp_path / f"instance {index}"
            service = Service(data)
            service.store.set("settings", service.settings() | {"admin_port": ports[index * 2], "gateway_port": ports[index * 2 + 1]})
            service.store.db.close()
            result = tmp_path / f"ready-{index}.json"
            command = [sys.executable, str(project / "run.py"), "--data-dir", str(data), "serve", "--startup-result", str(result)]
            process = subprocess.Popen(command, cwd=project, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            processes.append(process)
            records.append({"ProcessId": process.pid, "CommandLine": subprocess.list2cmdline(command)})
            deadline = time.monotonic() + 15
            while not result.exists():
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.1)
            assert json.loads(result.read_text(encoding="utf-8"))["ok"]
        found = discover_instances(tmp_path / "instance 0", records)
        assert found["error"] == ""
        assert len(found["instances"]) == 2
        assert found["instances"][0]["current"]
        assert {item["port"] for item in found["instances"]} == {ports[0], ports[2]}
        # Exercise native Windows enumeration without relying on formal-service state.
        actual = discover_instances(tmp_path / "instance 0")
        assert {p.pid for p in processes} <= {item["pid"] for item in actual["instances"]}
        records = [record for record in windows_processes() if str(tmp_path) in record["CommandLine"] and process_candidate(record)]
        marker = tmp_path / "instance 1" / "runtime.json"
        original = marker.read_text(encoding="utf-8")
        marker.unlink()
        compatible = discover_instances(tmp_path / "instance 0", records)
        assert any(item["port"] == ports[2] and item["legacy"] for item in compatible["instances"])
        # A present but mismatched marker must not fall back to legacy detection.
        marker.write_text(json.dumps({"instance": "0" * 32, "admin_port": ports[2]}), encoding="utf-8")
        assert len(discover_instances(tmp_path / "instance 0", records)["instances"]) == 1
        marker.write_text(original, encoding="utf-8")
        processes[1].terminate()
        processes[1].wait(timeout=10)
        assert len(discover_instances(tmp_path / "instance 0", records)["instances"]) == 1
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=10)

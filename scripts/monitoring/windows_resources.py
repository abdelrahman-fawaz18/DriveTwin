"""Windows physical-memory, PDH paging and labeled process working-set collector."""

import ctypes as c
import json
import threading
import time
from ctypes import wintypes as w


class Memory(c.Structure):
    _fields_ = [("length", w.DWORD), ("load", w.DWORD)] + [
        (key, c.c_ulonglong)
        for key in (
            "physical_total",
            "physical_available",
            "pagefile_total",
            "pagefile_available",
            "virtual_total",
            "virtual_available",
            "extended_available",
        )
    ]


class Process(c.Structure):
    _fields_ = [
        ("size", w.DWORD),
        ("usage", w.DWORD),
        ("pid", w.DWORD),
        ("heap", c.c_size_t),
        ("module", w.DWORD),
        ("threads", w.DWORD),
        ("parent", w.DWORD),
        ("priority", w.LONG),
        ("flags", w.DWORD),
        ("name", w.WCHAR * 260),
    ]


class ProcessMemory(c.Structure):
    _fields_ = [("size", w.DWORD), ("page_faults", w.DWORD)] + [
        (key, c.c_size_t)
        for key in (
            "peak_working_set",
            "working_set",
            "peak_paged_pool",
            "paged_pool",
            "peak_nonpaged_pool",
            "nonpaged_pool",
            "pagefile",
            "peak_pagefile",
        )
    ]


class CounterValue(c.Structure):
    _fields_ = [("status", w.DWORD), ("value", c.c_double)]


class Sampler:
    def __init__(self, path, *, on_sample=None):
        self.path = path
        self.on_sample = on_sample
        self.stop = threading.Event()
        self.error = None
        self.kernel = c.WinDLL("kernel32", use_last_error=True)
        self.psapi = c.WinDLL("psapi", use_last_error=True)
        self.pdh = c.WinDLL("pdh")
        k = self.kernel
        k.CreateToolhelp32Snapshot.restype = w.HANDLE
        k.OpenProcess.restype = w.HANDLE
        k.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
        k.CloseHandle.argtypes = [w.HANDLE]
        k.Process32FirstW.argtypes = [w.HANDLE, c.POINTER(Process)]
        k.Process32NextW.argtypes = [w.HANDLE, c.POINTER(Process)]
        self.psapi.GetProcessMemoryInfo.argtypes = [w.HANDLE, c.POINTER(ProcessMemory), w.DWORD]
        self.query = w.HANDLE()
        self.counters = {}
        self.pdh.PdhOpenQueryW.argtypes = [w.LPCWSTR, c.c_size_t, c.POINTER(w.HANDLE)]
        if self.pdh.PdhOpenQueryW(None, 0, c.byref(self.query)):
            raise OSError("Cannot open Windows paging query")
        self.pdh.PdhAddEnglishCounterW.argtypes = [
            w.HANDLE,
            w.LPCWSTR,
            c.c_size_t,
            c.POINTER(w.HANDLE),
        ]
        self.pdh.PdhCollectQueryData.argtypes = [w.HANDLE]
        self.pdh.PdhGetFormattedCounterValue.argtypes = [
            w.HANDLE,
            w.DWORD,
            c.c_void_p,
            c.POINTER(CounterValue),
        ]
        self.pdh.PdhCloseQuery.argtypes = [w.HANDLE]
        for name, counter in (
            ("pages_input_s", r"\Memory\Pages Input/sec"),
            ("pages_output_s", r"\Memory\Pages Output/sec"),
            ("committed_bytes", r"\Memory\Committed Bytes"),
            ("commit_limit_bytes", r"\Memory\Commit Limit"),
            ("wsl_working_set_bytes", r"\Process(vmmemWSL)\Working Set"),
        ):
            handle = w.HANDLE()
            if self.pdh.PdhAddEnglishCounterW(self.query, counter, 0, c.byref(handle)):
                raise OSError("Cannot add Windows paging counter")
            self.counters[name] = handle
        if self.pdh.PdhCollectQueryData(self.query):
            raise OSError("Cannot initialize Windows paging counters")

    def processes(self):
        snapshot = self.kernel.CreateToolhelp32Snapshot(2, 0)
        if snapshot == w.HANDLE(-1).value:
            raise OSError("Cannot inventory process memory")
        result = {"browser_name_total_bytes": 0, "wsl_name_total_bytes": 0}
        result.update(
            browser_processes=0,
            wsl_processes=0,
            unreadable_browser_processes=0,
            unreadable_wsl_processes=0,
        )
        try:
            item = Process()
            item.size = c.sizeof(item)
            present = self.kernel.Process32FirstW(snapshot, c.byref(item))
            while present:
                name = item.name.lower()
                category = (
                    "browser_name_total_bytes"
                    if name in ("msedge.exe", "chrome.exe")
                    else "wsl_name_total_bytes"
                    if name in ("vmmemwsl", "vmmemwsl.exe", "vmmem", "vmmem.exe")
                    else None
                )
                if category:
                    role = "browser" if category.startswith("browser") else "wsl"
                    result[role + "_processes"] += 1
                    # Windows 7+ supports this limited query right without VM_READ.
                    handle = self.kernel.OpenProcess(0x1000, False, item.pid)
                    read = False
                    if handle:
                        try:
                            memory = ProcessMemory()
                            memory.size = c.sizeof(memory)
                            if self.psapi.GetProcessMemoryInfo(
                                handle, c.byref(memory), memory.size
                            ):
                                result[category] += memory.working_set
                                read = True
                        finally:
                            self.kernel.CloseHandle(handle)
                    if not read:
                        result["unreadable_" + role + "_processes"] += 1
                present = self.kernel.Process32NextW(snapshot, c.byref(item))
        finally:
            self.kernel.CloseHandle(snapshot)
        return result

    def sample(self, sequence):
        memory = Memory()
        memory.length = c.sizeof(memory)
        if not self.kernel.GlobalMemoryStatusEx(c.byref(memory)):
            raise OSError("Cannot read Windows physical memory")
        if self.pdh.PdhCollectQueryData(self.query):
            raise OSError("Cannot sample Windows paging counters")
        paging = {}
        for name, handle in self.counters.items():
            value = CounterValue()
            status = self.pdh.PdhGetFormattedCounterValue(handle, 0x200, None, c.byref(value))
            if status or value.status not in (0, 1):
                if name == "wsl_working_set_bytes":
                    paging[name] = None
                    continue
                raise OSError("Invalid Windows paging sample")
            paging[name] = value.value
        processes = self.processes()
        processes["wsl_name_total_bytes"] = (
            int(paging.pop("wsl_working_set_bytes"))
            if paging["wsl_working_set_bytes"] is not None
            else None
        )
        paging.pop("wsl_working_set_bytes", None)
        processes["wsl_memory_method"] = "PDH Process(vmmemWSL) Working Set"
        total = int(paging.pop("committed_bytes"))
        limit = int(paging.pop("commit_limit_bytes"))
        if not 0 <= total <= limit or limit <= 0:
            raise OSError("Invalid Windows commit counters")
        return {
            "sequence": sequence,
            "utc_s": time.time(),
            "monotonic_s": time.monotonic(),
            "physical_total_bytes": memory.physical_total,
            "physical_available_bytes": memory.physical_available,
            "commit": {
                "total_bytes": total,
                "limit_bytes": limit,
                "available_bytes": limit - total,
            },
            "paging": paging,
            **processes,
        }

    def run(self):
        try:
            start = time.monotonic()
            # PDH rates require two samples with elapsed time.
            if self.stop.wait(1):
                return
            with self.path.open("x", encoding="utf-8") as output:
                sequence = 0
                while not self.stop.is_set():
                    row = self.sample(sequence)
                    output.write(json.dumps(row, allow_nan=False) + "\n")
                    output.flush()
                    if self.on_sample:
                        self.on_sample(row)
                    sequence += 1
                    self.stop.wait(max(0, start + 1 + sequence - time.monotonic()))
        except Exception as exc:
            self.error = str(exc)
        finally:
            self.pdh.PdhCloseQuery(self.query)

    def start(self):
        self.thread = threading.Thread(target=self.run, name="evdt-windows-resources", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=10)
        if self.thread.is_alive() or self.error:
            raise RuntimeError(self.error or "Windows sampler did not stop")

"""Writes key events into another process's console input without focusing its window (AttachConsole +
WriteConsoleInputW). Usage: win_console.py <pid> escape, or win_console.py <pid> line
<part>... to type each part as a write of its own, a pause apart, then press Return. Stdlib only."""
import ctypes
import sys
import time
from ctypes import wintypes

KEY_EVENT = 0x0001
VK_ESCAPE = 0x1B
VK_RETURN = 0x0D
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_SHARE_READ, FILE_SHARE_WRITE = 1, 2
OPEN_EXISTING = 3


class KEY_EVENT_RECORD(ctypes.Structure):
    _fields_ = [("bKeyDown", wintypes.BOOL), ("wRepeatCount", wintypes.WORD), ("wVirtualKeyCode", wintypes.WORD),
                ("wVirtualScanCode", wintypes.WORD), ("uChar", wintypes.WCHAR), ("dwControlKeyState", wintypes.DWORD)]


class _Event(ctypes.Union):
    _fields_ = [("KeyEvent", KEY_EVENT_RECORD), ("_pad", ctypes.c_byte * 16)]


class INPUT_RECORD(ctypes.Structure):
    _fields_ = [("EventType", wintypes.WORD), ("Event", _Event)]


def key_record(down: bool, key: int, scan: int, char: str) -> INPUT_RECORD:
    record = INPUT_RECORD()
    record.EventType = KEY_EVENT
    record.Event.KeyEvent = KEY_EVENT_RECORD(down, 1, key, scan, char, 0)
    return record


def press(key: int, scan: int, char: str) -> list[INPUT_RECORD]:
    return [key_record(True, key, scan, char), key_record(False, key, scan, char)]


def escape_records() -> list[INPUT_RECORD]:
    return press(VK_ESCAPE, 0x01, "\x1b")


SETTLE_S = 0.3


def text_records(text: str) -> list[INPUT_RECORD]:
    units = text.encode("utf-16-le")
    chars = [units[i:i + 2].decode("utf-16-le", "surrogatepass") for i in range(0, len(units), 2)]
    return [record for char in chars for record in press(0, 0, char)]


def line_writes(parts: list[str]) -> list[list[INPUT_RECORD]]:
    return [text_records(part) for part in parts] + [press(VK_RETURN, 0x1C, "\r")]


def main(pid: int, writes: list[list[INPUT_RECORD]]) -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        print(f"AttachConsole({pid}) failed: error {ctypes.get_last_error()}", file=sys.stderr)
        return 1
    try:
        k32.CreateFileW.restype = wintypes.HANDLE
        handle = k32.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, 0, None)
        for n, records in enumerate(writes):
            if n:
                time.sleep(SETTLE_S)
            batch = (INPUT_RECORD * len(records))(*records)
            written = wintypes.DWORD()
            ok = k32.WriteConsoleInputW(wintypes.HANDLE(handle), batch, len(records), ctypes.byref(written))
            if not ok or written.value != len(records):
                print(f"WriteConsoleInput wrote {written.value} records: error {ctypes.get_last_error()}", file=sys.stderr)
                return 1
        k32.CloseHandle(wintypes.HANDLE(handle))
    finally:
        k32.FreeConsole()
    return 0


def writes_for(what: list[str]) -> list[list[INPUT_RECORD]]:
    if what == ["escape"]:
        return [escape_records()]
    if len(what) >= 2 and what[0] == "line":
        return line_writes(what[1:])
    raise SystemExit("usage: win_console.py <pid> escape | win_console.py <pid> line <part>...")


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]), writes_for(sys.argv[2:])))

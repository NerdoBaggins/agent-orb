"""Writes one Escape key event into another process's console input without focusing its window
(AttachConsole + WriteConsoleInputW, proven in test 0.2). Usage: win_console.py <pid>. Stdlib only."""
import ctypes
import sys
from ctypes import wintypes

KEY_EVENT = 0x0001
VK_ESCAPE = 0x1B
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


def escape_record(down: bool) -> INPUT_RECORD:
    record = INPUT_RECORD()
    record.EventType = KEY_EVENT
    record.Event.KeyEvent = KEY_EVENT_RECORD(down, 1, VK_ESCAPE, 0x01, "\x1b", 0)
    return record


def main(pid: int) -> int:
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FreeConsole()
    if not k32.AttachConsole(pid):
        print(f"AttachConsole({pid}) failed: error {ctypes.get_last_error()}", file=sys.stderr)
        return 1
    try:
        k32.CreateFileW.restype = wintypes.HANDLE
        handle = k32.CreateFileW("CONIN$", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, None,
                                 OPEN_EXISTING, 0, None)
        records = (INPUT_RECORD * 2)(escape_record(True), escape_record(False))
        written = wintypes.DWORD()
        ok = k32.WriteConsoleInputW(wintypes.HANDLE(handle), records, 2, ctypes.byref(written))
        error = ctypes.get_last_error()
        k32.CloseHandle(wintypes.HANDLE(handle))
    finally:
        k32.FreeConsole()
    if not ok or written.value != 2:
        print(f"WriteConsoleInput wrote {written.value} records: error {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1])))

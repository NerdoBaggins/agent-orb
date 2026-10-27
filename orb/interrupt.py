"""Stop while thinking under Claude Code: one Escape into the session's own terminal, through a per-platform adapter.
The session is identified by `sessionPid` in channel.json, the process that spawned the channel, and the adapter
confirms it still looks like a Claude Code process before sending anything."""
import ntpath
import subprocess
import sys
from dataclasses import dataclass

from orb import PLUGIN_ROOT

CLAUDE_IMAGE_NAMES = ("claude.exe", "claude", "node", "node.exe", "bun", "bun.exe")


@dataclass
class Target:
    pid: int
    image: str

    def looks_like_claude(self) -> bool:
        # ntpath splits on both separators on every platform; pathlib's Path treats `\` as a name character on POSIX.
        return ntpath.basename(self.image).lower() in CLAUDE_IMAGE_NAMES


def process_image(pid: int) -> str | None:
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return None
        try:
            size = wintypes.DWORD(1024)
            buffer = ctypes.create_unicode_buffer(size.value)
            if not k32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
                return None
            return buffer.value
        finally:
            k32.CloseHandle(handle)
    try:
        return subprocess.run(["ps", "-o", "comm=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def target_for(session_pid: int | None) -> Target | None:
    if not session_pid:
        return None
    image = process_image(session_pid)
    return Target(session_pid, image) if image else None


class NoAdapter(RuntimeError):
    pass


def send_escape(target: Target) -> bool:
    """True when the Escape was delivered. Raises NoAdapter where no adapter exists or none can be verified."""
    if not target.looks_like_claude():
        raise NoAdapter(f"pid {target.pid} is {target.image}, not a Claude Code process; nothing sent")
    if sys.platform == "win32":
        return _windows_console_escape(target.pid)
    if sys.platform == "darwin":
        return _macos_terminal_escape(target.pid)
    raise NoAdapter(f"no interrupt adapter for {sys.platform}")


def _windows_console_escape(pid: int) -> bool:
    # The attach/detach dance runs in a child so the server process never swaps its own console.
    result = subprocess.run([sys.executable, str(PLUGIN_ROOT / "orb" / "win_console.py"), str(pid)],
                            capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        raise NoAdapter(result.stderr.strip() or result.stdout.strip() or f"console escape failed ({result.returncode})")
    return True


def _escape_once_selected(process: str, selected_tty: str, device: str) -> str:
    # `select` and `activate` both return before they take effect; a key code sent in that gap goes to whatever tab
    # and app were in front. `selected_tty` is the app's own expression for the tty of its selected tab.
    return f'''
repeat 30 times
  if ({selected_tty}) is "{device}" then exit repeat
  delay 0.1
end repeat
if ({selected_tty}) is not "{device}" then return "tab not selected"
tell application "System Events"
  repeat 30 times
    if frontmost of process "{process}" then exit repeat
    delay 0.1
  end repeat
  if not frontmost of process "{process}" then return "not frontmost"
  key code 53
end tell'''


MACOS_TERMINAL_APPS = {"iTerm2": "iTerm", "iTermServer": "iTerm", "Terminal": "Terminal"}


def _terminal_app_of(pid: int) -> str | None:
    """Which terminal app hosts this process, read off its ancestors (claude ← zsh ← login ← Terminal.app)."""
    for _ in range(12):
        line = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.split(None, 1)
        if len(line) < 2:
            return None
        ppid, image = line
        name = ntpath.basename(image.strip()).split("-")[0]
        if name in MACOS_TERMINAL_APPS:
            return MACOS_TERMINAL_APPS[name]
        pid = int(ppid)
        if pid <= 1:
            return None
    return None


def _macos_terminal_escape(pid: int) -> bool:
    """iTerm2 writes the Escape byte into the session's pty, which touches no window. Terminal has no such call, so
    there the session's tab is selected and Escape goes through System Events once the app is frontmost: Terminal
    takes focus. The app is read off the session's ancestors, because enumerating iTerm's sessions over AppleScript
    costs seconds. Both verified on macOS 26 (test 0.2)."""
    tty = subprocess.run(["ps", "-o", "tty=", "-p", str(pid)], capture_output=True, text=True, timeout=5).stdout.strip()
    if not tty or tty == "??":
        raise NoAdapter(f"pid {pid} has no terminal; cannot send Escape")
    app = _terminal_app_of(pid)
    if app is None:
        raise NoAdapter(f"pid {pid} runs under no terminal app with an adapter (iTerm, Terminal)")
    device = f"/dev/{tty}"
    scripts = {
        "iTerm": f'''
tell application "iTerm2"
  repeat with w in windows
    repeat with t in tabs of w
      repeat with s in sessions of t
        if tty of s is "{device}" then
          tell s to write text (character id 27) newline NO
          return "sent"
        end if
      end repeat
    end repeat
  end repeat
end tell
return "not found"''',
        "Terminal": f'''
tell application "Terminal"
  repeat with w in windows
    repeat with t in tabs of w
      if tty of t is "{device}" then
        set selected tab of w to t
        set frontmost of w to true
        activate
        {_escape_once_selected("Terminal", "tty of selected tab of front window", device)}
        return "sent"
      end if
    end repeat
  end repeat
end tell
return "not found"''',
    }
    result = subprocess.run(["osascript", "-e", scripts[app]], capture_output=True, text=True, timeout=15)
    outcome = result.stdout.strip() if result.returncode == 0 else result.stderr.strip()
    if outcome == "sent":
        return True
    raise NoAdapter(f"{app}: {outcome or 'osascript failed'} for {device}")

"""Native Windows keyboard typing for the standalone ManuCodeAI desktop app."""
from __future__ import annotations

import ctypes
import random
import sys
import threading
import time
from ctypes import wintypes


if sys.platform == "win32":
    ULONG_PTR = wintypes.WPARAM

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                    ("dwExtraInfo", ULONG_PTR)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                    ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                    ("wParamH", wintypes.WORD)]

    class INPUT_UNION(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _anonymous_ = ("u",)
        _fields_ = [("type", wintypes.DWORD), ("u", INPUT_UNION)]


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
VK_CONTROL = 0x11
VK_S = 0x53
VK_A = 0x41
VK_RETURN = 0x0D
VK_TAB = 0x09
CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


class TypingCancelled(Exception):
    """Raised when the user stops an active typing session."""


class KeyboardKillSwitch:
    """Global Ctrl+Shift+X emergency stop; works while VS Code has focus."""

    HOTKEY_ID = 0x4D43
    MOD_SHIFT = 0x0004
    MOD_CONTROL = 0x0002
    MOD_NOREPEAT = 0x4000
    VK_X = 0x58
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012

    def __init__(self, cancel_event: threading.Event, on_trigger=None):
        self.cancel_event = cancel_event
        self.on_trigger = on_trigger
        self._thread = None
        self._thread_id = None
        self._ready = threading.Event()
        self.error = None

    def start(self) -> bool:
        if sys.platform != "win32":
            self.error = "Global keyboard kill switch is supported on Windows only."
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._ready.clear()
        self.error = None
        self._thread = threading.Thread(
            target=self._message_loop, name="ManuCodeAI-KillSwitch", daemon=True
        )
        self._thread.start()
        self._ready.wait(2.0)
        return self.error is None and self._thread.is_alive()

    def _message_loop(self) -> None:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        self._thread_id = kernel32.GetCurrentThreadId()
        registered = bool(user32.RegisterHotKey(
            None, self.HOTKEY_ID,
            self.MOD_CONTROL | self.MOD_SHIFT | self.MOD_NOREPEAT,
            self.VK_X,
        ))
        if not registered:
            self.error = (
                "Could not register Ctrl+Shift+X. Another application may already "
                "use this shortcut; ManuCodeAI's Stop button remains available."
            )
            self._ready.set()
            return

        self._ready.set()
        message = wintypes.MSG()
        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result <= 0:
                    break
                if message.message == self.WM_HOTKEY and message.wParam == self.HOTKEY_ID:
                    self.cancel_event.set()
                    if self.on_trigger:
                        self.on_trigger("EMERGENCY STOP: Ctrl+Shift+X pressed. Typing and agent work are stopping.")
                    break
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)

    def stop(self) -> None:
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, self.WM_QUIT, 0, 0
            )
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread_id = None


class WindowsTypingEngine:
    """Types code progressively with a steady pace and small natural pauses."""

    def __init__(self, min_delay: float = 0.18, max_delay: float = 0.28,
                 line_delay_min: float = 1.00, line_delay_max: float = 1.40):
        # Slow, deliberate human-style character entry with a natural pause between lines.
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.line_delay_min = line_delay_min
        self.line_delay_max = line_delay_max
        self._random = random.Random()

    @staticmethod
    def _send_input(scan_code: int, flags: int) -> None:
        if sys.platform != "win32":
            raise OSError("Native editor typing is supported on Windows only.")
        item = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
            wVk=0, wScan=scan_code, dwFlags=flags, time=0, dwExtraInfo=0))
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
        if sent != 1:
            raise ctypes.WinError()

    @staticmethod
    def _key(virtual_key: int, key_up: bool = False) -> None:
        flags = KEYEVENTF_KEYUP if key_up else 0
        item = INPUT(type=INPUT_KEYBOARD, ki=KEYBDINPUT(
            wVk=virtual_key, wScan=0, dwFlags=flags, time=0, dwExtraInfo=0))
        sent = ctypes.windll.user32.SendInput(1, ctypes.byref(item), ctypes.sizeof(INPUT))
        if sent != 1:
            raise ctypes.WinError()

    @classmethod
    def shortcut(cls, *keys: int) -> None:
        for key in keys:
            cls._key(key)
        for key in reversed(keys):
            cls._key(key, key_up=True)

    @staticmethod
    def _clipboard_get_unicode_text() -> str | None:
        """Read existing plain-text clipboard content so it can be restored."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        if not user32.OpenClipboard(None):
            return None
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return None
            kernel32.GlobalLock.restype = ctypes.c_void_p
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return None
            try:
                return ctypes.wstring_at(pointer)
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()

    @staticmethod
    def _clipboard_set_unicode_text(value: str) -> None:
        """Set plain text on the clipboard for a paste that bypasses editor auto-indent."""
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        kernel32.GlobalAlloc.restype = ctypes.c_void_p
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
        user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        user32.SetClipboardData.restype = ctypes.c_void_p
        data = (value + "\0").encode("utf-16-le")
        if not user32.OpenClipboard(None):
            raise ctypes.WinError()
        handle = None
        try:
            if not user32.EmptyClipboard():
                raise ctypes.WinError()
            handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
            if not handle:
                raise ctypes.WinError()
            kernel32.GlobalLock.restype = ctypes.c_void_p
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                raise ctypes.WinError()
            try:
                ctypes.memmove(pointer, data, len(data))
            finally:
                kernel32.GlobalUnlock(handle)
            if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                raise ctypes.WinError()
            handle = None  # The system owns the memory after SetClipboardData.
        finally:
            if handle:
                kernel32.GlobalFree(handle)
            user32.CloseClipboard()

    @classmethod
    def _paste_clipboard(cls) -> None:
        cls.shortcut(VK_CONTROL, 0x56)  # Ctrl+V

    def type_text(self, text: str, cancel_event: threading.Event | None = None,
                  progress_callback=None) -> None:
        if sys.platform != "win32":
            raise OSError("ManuCodeAI's visible typing engine requires Windows.")

        # Ctrl+V inserts a literal newline without triggering VS Code's automatic
        # indentation. The next line's spaces are then typed exactly as generated.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        total = len(text)
        previous_clipboard = self._clipboard_get_unicode_text()
        clipboard_changed = False

        try:
            for index, character in enumerate(text, start=1):
                if cancel_event and cancel_event.is_set():
                    raise TypingCancelled("Typing was stopped by the user.")

                if character == "\n":
                    self._clipboard_set_unicode_text("\n")
                    clipboard_changed = True
                    self._paste_clipboard()
                elif character == "\t":
                    self._clipboard_set_unicode_text("\t")
                    clipboard_changed = True
                    self._paste_clipboard()
                else:
                    encoded = character.encode("utf-16-le")
                    for offset in range(0, len(encoded), 2):
                        unit = int.from_bytes(encoded[offset:offset + 2], "little")
                        self._send_input(unit, KEYEVENTF_UNICODE)
                        self._send_input(unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)

                if progress_callback and (
                    character == "\n" or index % 25 == 0 or index == total
                ):
                    progress_callback(index, total)

                if character == "\n":
                    delay = self._random.uniform(
                        self.line_delay_min, self.line_delay_max
                    )
                else:
                    delay = self._random.uniform(self.min_delay, self.max_delay)
                    if character in ",;":
                        delay += self._random.uniform(0.16, 0.28)
                    elif character in ".!?":
                        delay += self._random.uniform(0.22, 0.38)
                    elif character in "{}[]()":
                        delay += self._random.uniform(0.06, 0.13)
                    elif character == " ":
                        delay += self._random.uniform(0.02, 0.05)

                if cancel_event:
                    if cancel_event.wait(delay):
                        raise TypingCancelled("Typing was stopped by the user.")
                else:
                    time.sleep(delay)
        finally:
            # Restore previous plain text if the clipboard contained text.
            # If it held a non-text format, it cannot be reconstructed here.
            if clipboard_changed:
                self._clipboard_set_unicode_text(previous_clipboard or "")

    @classmethod
    def replace_current_document(cls) -> None:
        cls.shortcut(VK_CONTROL, VK_A)
        cls._key(0x08)  # Backspace: clear selection; never append to old content.

    @classmethod
    def save_document(cls) -> None:
        cls.shortcut(VK_CONTROL, VK_S)



class LinuxTypingEngine:
    """Visible Linux keyboard typing using xdotool and the desktop clipboard."""

    def __init__(self, min_delay: float = 0.18, max_delay: float = 0.28,
                 line_delay_min: float = 1.00, line_delay_max: float = 1.40):
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.line_delay_min = line_delay_min
        self.line_delay_max = line_delay_max
        self._random = random.Random()
        self._clipboard_tool = (
            "wl-copy" if os.environ.get("WAYLAND_DISPLAY") and shutil.which("wl-copy")
            else "xclip" if shutil.which("xclip")
            else "xsel" if shutil.which("xsel")
            else None
        )
        if not shutil.which("xdotool"):
            raise OSError(
                "Linux visible typing requires xdotool. Install it with your distribution package manager."
            )
        if not self._clipboard_tool:
            raise OSError(
                "Linux visible typing requires wl-clipboard (Wayland), xclip, or xsel (X11)."
            )

    def _run(self, args: list[str], *, input_text: str | None = None,
             capture: bool = False) -> str:
        result = subprocess.run(
            args, input=input_text, text=True, capture_output=capture, check=False
        )
        if result.returncode:
            detail = (result.stderr or "").strip()
            raise OSError(detail or f"Command failed: {' '.join(args)}")
        return result.stdout if capture else ""

    def _clipboard_get(self) -> str | None:
        try:
            if self._clipboard_tool == "wl-copy":
                return self._run(["wl-paste", "--no-newline"], capture=True)
            if self._clipboard_tool == "xclip":
                return self._run(["xclip", "-selection", "clipboard", "-o"], capture=True)
            return self._run(["xsel", "--clipboard", "--output"], capture=True)
        except (OSError, FileNotFoundError):
            return None

    def _clipboard_set(self, value: str) -> None:
        if self._clipboard_tool == "wl-copy":
            self._run(["wl-copy", "--type", "text/plain;charset=utf-8"], input_text=value)
        elif self._clipboard_tool == "xclip":
            self._run(["xclip", "-selection", "clipboard", "-i"], input_text=value)
        else:
            self._run(["xsel", "--clipboard", "--input"], input_text=value)

    @staticmethod
    def shortcut(*keys: str) -> None:
        subprocess.run(["xdotool", "key", "--clearmodifiers", "+".join(keys)], check=True)

    @classmethod
    def replace_current_document(cls) -> None:
        cls.shortcut("ctrl+a")
        cls.shortcut("BackSpace")

    @classmethod
    def save_document(cls) -> None:
        cls.shortcut("ctrl+s")

    def _paste_clipboard(self) -> None:
        self.shortcut("ctrl+v")

    def type_text(self, text: str, cancel_event: threading.Event | None = None,
                  progress_callback=None) -> None:
        if sys.platform != "linux":
            raise OSError("LinuxTypingEngine can only run on Linux.")
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        total = len(text)
        previous_clipboard = self._clipboard_get()
        clipboard_changed = False
        try:
            for index, character in enumerate(text, start=1):
                if cancel_event and cancel_event.is_set():
                    raise TypingCancelled("Typing was stopped by the user.")
                if character in {"\n", "\t"}:
                    self._clipboard_set(character)
                    clipboard_changed = True
                    self._paste_clipboard()
                else:
                    self._run(["xdotool", "type", "--clearmodifiers", "--delay", "0", "--", character])
                if progress_callback and (
                    character == "\n" or index % 25 == 0 or index == total
                ):
                    progress_callback(index, total)
                if character == "\n":
                    delay = self._random.uniform(self.line_delay_min, self.line_delay_max)
                else:
                    delay = self._random.uniform(self.min_delay, self.max_delay)
                    if character in ",;":
                        delay += self._random.uniform(0.16, 0.28)
                    elif character in ".!?":
                        delay += self._random.uniform(0.22, 0.38)
                    elif character in "{}[]()":
                        delay += self._random.uniform(0.06, 0.13)
                    elif character == " ":
                        delay += self._random.uniform(0.02, 0.05)
                if cancel_event:
                    if cancel_event.wait(delay):
                        raise TypingCancelled("Typing was stopped by the user.")
                else:
                    time.sleep(delay)
        finally:
            if clipboard_changed and previous_clipboard is not None:
                self._clipboard_set(previous_clipboard)


def create_typing_engine(**kwargs):
    """Return the native visible typing backend for the current desktop OS."""
    if sys.platform == "win32":
        return WindowsTypingEngine(**kwargs)
    if sys.platform.startswith("linux"):
        return LinuxTypingEngine(**kwargs)
    raise OSError(f"Visible VS Code typing is not supported on {sys.platform}.")

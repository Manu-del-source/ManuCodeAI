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


class TypingCancelled(Exception):
    """Raised when the user stops an active typing session."""


class WindowsTypingEngine:
    """Types code progressively with a steady pace and small natural pauses."""

    def __init__(self, min_delay: float = 0.12, max_delay: float = 0.20,
                 line_delay_min: float = 0.80, line_delay_max: float = 1.20):
        # Deliberately slow defaults: visible editor typing should be readable,
        # not a near-instant paste disguised as keyboard input.
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

    def type_text(self, text: str, cancel_event: threading.Event | None = None,
                  progress_callback=None) -> None:
        if sys.platform != "win32":
            raise OSError("ManuCodeAI's visible typing engine requires Windows.")

        # Normalize Windows CRLF and legacy CR endings to one real newline.
        # This guarantees one Enter keystroke for every line break.
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        total = len(text)

        for index, character in enumerate(text, start=1):
            if cancel_event and cancel_event.is_set():
                raise TypingCancelled("Typing was stopped by the user.")

            if character == "\n":
                self.shortcut(VK_RETURN)
            elif character == "\t":
                self.shortcut(VK_TAB)
            else:
                encoded = character.encode("utf-16-le")
                for offset in range(0, len(encoded), 2):
                    unit = int.from_bytes(encoded[offset:offset + 2], "little")
                    self._send_input(unit, KEYEVENTF_UNICODE)
                    self._send_input(unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)

            if progress_callback and (character == "\n" or index % 25 == 0 or index == total):
                progress_callback(index, total)

            if character == "\n":
                # Pause after completing each line before typing the next one.
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

            # Event.wait keeps Stop responsive during long typing delays.
            if cancel_event:
                if cancel_event.wait(delay):
                    raise TypingCancelled("Typing was stopped by the user.")
            else:
                time.sleep(delay)

    @classmethod
    def replace_current_document(cls) -> None:
        cls.shortcut(VK_CONTROL, VK_A)

    @classmethod
    def save_document(cls) -> None:
        cls.shortcut(VK_CONTROL, VK_S)

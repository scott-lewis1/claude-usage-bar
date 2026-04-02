"""Windows API declarations and Taskbar helper class."""

import ctypes
import ctypes.wintypes as wintypes
import tkinter as tk

# ─── Constants ───────────────────────────────────────────────────────────────

GWL_EXSTYLE = -20
GWL_STYLE = -16
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
WS_CHILD = 0x40000000
WS_VISIBLE = 0x10000000
LWA_ALPHA = 0x00000002
LWA_COLORKEY = 0x00000001
HWND_BOTTOM = 1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_NOZORDER = 0x0004

# ─── API bindings ────────────────────────────────────────────────────────────

user32 = ctypes.windll.user32

FindWindowW = user32.FindWindowW
FindWindowW.restype = wintypes.HWND
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]

GetWindowRect = user32.GetWindowRect
GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]

SetWindowLongW = user32.SetWindowLongW
SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
SetWindowLongW.restype = ctypes.c_long

GetWindowLongW = user32.GetWindowLongW
GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongW.restype = ctypes.c_long

SetLayeredWindowAttributes = user32.SetLayeredWindowAttributes
SetLayeredWindowAttributes.argtypes = [
    wintypes.HWND, wintypes.COLORREF, wintypes.BYTE, wintypes.DWORD,
]

SetWindowPos = user32.SetWindowPos
SetWindowPos.argtypes = [
    wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
    ctypes.c_int, ctypes.c_int, ctypes.c_uint,
]

SetParent = user32.SetParent
SetParent.argtypes = [wintypes.HWND, wintypes.HWND]
SetParent.restype = wintypes.HWND


# ─── Taskbar ─────────────────────────────────────────────────────────────────

class Taskbar:
    """Finds and interacts with the Windows taskbar."""

    def __init__(self):
        self.hwnd = None
        self.rect = None
        self.refresh()

    def refresh(self):
        """Re-read taskbar HWND and rect."""
        self.hwnd = FindWindowW("Shell_TrayWnd", None)
        if self.hwnd:
            r = wintypes.RECT()
            GetWindowRect(self.hwnd, ctypes.byref(r))
            self.rect = (r.left, r.top, r.right, r.bottom)
        else:
            self.rect = None

    @property
    def width(self):
        if not self.rect:
            return 0
        return self.rect[2] - self.rect[0]

    @property
    def height(self):
        if not self.rect:
            return 0
        return self.rect[3] - self.rect[1]

    def reparent_child(self, tk_toplevel):
        """Reparent a tkinter Toplevel as a click-through child of the taskbar."""
        tk_toplevel.update_idletasks()
        tk_toplevel.update()
        frame = ctypes.c_long(tk_toplevel.winfo_id())
        hwnd = user32.GetParent(frame)
        if not hwnd or not self.hwnd:
            return hwnd

        SetParent(hwnd, self.hwnd)

        style = GetWindowLongW(hwnd, GWL_STYLE)
        style = (style | WS_CHILD | WS_VISIBLE) & ~0x80000000
        SetWindowLongW(hwnd, GWL_STYLE, style)

        ex = GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
        SetWindowLongW(hwnd, GWL_EXSTYLE, ex)

        return hwnd

    @staticmethod
    def position_child(hwnd, x, y, w, h):
        """Position a child window without changing z-order."""
        SetWindowPos(hwnd, 0, x, y, w, h,
                     SWP_NOZORDER | SWP_NOACTIVATE | SWP_SHOWWINDOW)

    @staticmethod
    def set_opacity(hwnd, opacity):
        """Set a window's alpha opacity."""
        SetLayeredWindowAttributes(hwnd, 0, opacity, LWA_ALPHA)

    @staticmethod
    def set_chroma_key(hwnd, color_rgb, opacity):
        """Set chroma key + alpha on a layered window."""
        SetLayeredWindowAttributes(hwnd, color_rgb, opacity,
                                   LWA_COLORKEY | LWA_ALPHA)

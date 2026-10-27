from __future__ import annotations

import ctypes
import ctypes.util
import logging

logger = logging.getLogger("hagaki.hotkeys")

CMD, SHIFT, ALT, CTRL = 1 << 8, 1 << 9, 1 << 11, 1 << 12
MODIFIERS = {"cmd": CMD, "shift": SHIFT, "alt": ALT, "ctrl": CTRL}
MODIFIER_ALIASES = {
    "command": "cmd", "meta": "cmd", "⌘": "cmd",
    "option": "alt", "opt": "alt", "⌥": "alt",
    "control": "ctrl", "⌃": "ctrl",
    "⇧": "shift",
}
MODIFIER_ORDER = ("ctrl", "alt", "shift", "cmd")
MODIFIER_SYMBOLS = {"ctrl": "⌃", "alt": "⌥", "shift": "⇧", "cmd": "⌘"}

KEYCODES: dict[str, int] = {
    "a": 0x00, "s": 0x01, "d": 0x02, "f": 0x03, "h": 0x04, "g": 0x05, "z": 0x06,
    "x": 0x07, "c": 0x08, "v": 0x09, "b": 0x0B, "q": 0x0C, "w": 0x0D, "e": 0x0E,
    "r": 0x0F, "y": 0x10, "t": 0x11, "1": 0x12, "2": 0x13, "3": 0x14, "4": 0x15,
    "6": 0x16, "5": 0x17, "equal": 0x18, "9": 0x19, "7": 0x1A, "minus": 0x1B,
    "8": 0x1C, "0": 0x1D, "bracketright": 0x1E, "o": 0x1F, "u": 0x20,
    "bracketleft": 0x21, "i": 0x22, "p": 0x23, "l": 0x25, "j": 0x26, "quote": 0x27,
    "k": 0x28, "semicolon": 0x29, "backslash": 0x2A, "comma": 0x2B, "slash": 0x2C,
    "n": 0x2D, "m": 0x2E, "period": 0x2F, "backquote": 0x32, "grave": 0x32,
    "enter": 0x24, "return": 0x24, "tab": 0x30, "space": 0x31, "backspace": 0x33,
    "delete": 0x75, "escape": 0x35, "home": 0x73, "end": 0x77, "pageup": 0x74,
    "pagedown": 0x79, "arrowleft": 0x7B, "arrowright": 0x7C, "arrowdown": 0x7D,
    "arrowup": 0x7E,
    "f1": 0x7A, "f2": 0x78, "f3": 0x63, "f4": 0x76, "f5": 0x60, "f6": 0x61,
    "f7": 0x62, "f8": 0x64, "f9": 0x65, "f10": 0x6D, "f11": 0x67, "f12": 0x6F,
    "f13": 0x69, "f14": 0x6B, "f15": 0x71, "f16": 0x6A, "f17": 0x40, "f18": 0x4F,
    "f19": 0x50, "f20": 0x5A,
}
KEY_LABELS = {
    "space": "Space", "enter": "↩", "return": "↩", "tab": "⇥", "backspace": "⌫",
    "delete": "⌦", "escape": "esc", "home": "↖", "end": "↘", "pageup": "⇞",
    "pagedown": "⇟", "arrowleft": "←", "arrowright": "→", "arrowup": "↑",
    "arrowdown": "↓", "equal": "=", "minus": "-", "bracketright": "]",
    "bracketleft": "[", "quote": "'", "semicolon": ";", "backslash": "\\",
    "comma": ",", "slash": "/", "period": ".", "backquote": "`", "grave": "`",
}


class SpecError(ValueError):
    pass


def normalize_key(code: str) -> str:
    k = str(code or "").strip().lower()
    if k.startswith("key") and len(k) == 4:
        k = k[3:]
    elif k.startswith("digit") and len(k) == 6:
        k = k[5:]
    return k


def parse_spec(spec: str) -> tuple[int, int, frozenset[str], str]:
    parts = [p.strip().lower() for p in str(spec or "").split("+")]
    parts = [p for p in parts if p]
    if len(parts) < 2:
        raise SpecError("a shortcut needs at least one modifier and a key")
    *mods, key = parts
    names = set()
    for m in mods:
        m = MODIFIER_ALIASES.get(m, m)
        if m not in MODIFIERS:
            raise SpecError(f"unknown modifier {m!r}")
        names.add(m)
    key = normalize_key(key)
    if key not in KEYCODES:
        raise SpecError(f"unknown key {key!r}")
    flags = 0
    for m in names:
        flags |= MODIFIERS[m]
    return KEYCODES[key], flags, frozenset(names), key


def canonical(spec: str) -> str:
    _kc, _flags, names, key = parse_spec(spec)
    return "+".join([m for m in MODIFIER_ORDER if m in names] + [key])


def display(spec: str) -> str:
    try:
        _kc, _flags, names, key = parse_spec(spec)
    except SpecError:
        return str(spec)
    return "".join(MODIFIER_SYMBOLS[m] for m in MODIFIER_ORDER if m in names) + KEY_LABELS.get(key, key.upper())


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("mac_roman"), "big")


kEventClassKeyboard = _fourcc("keyb")
kEventHotKeyPressed = 5
kEventParamDirectObject = _fourcc("----")
typeEventHotKeyID = _fourcc("hkid")
SIGNATURE = _fourcc("hgki")


class _EventTypeSpec(ctypes.Structure):
    _fields_ = [("eventClass", ctypes.c_uint32), ("eventKind", ctypes.c_uint32)]


class _EventHotKeyID(ctypes.Structure):
    _fields_ = [("signature", ctypes.c_uint32), ("id", ctypes.c_uint32)]


_HANDLER = ctypes.CFUNCTYPE(ctypes.c_int32, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)

_carbon = None
_registry: dict[int, object] = {}
_next_id = 1
_handler_ref = None
_cfunc = None


def _lib():
    global _carbon
    if _carbon is None:
        path = ctypes.util.find_library("Carbon") or "/System/Library/Frameworks/Carbon.framework/Carbon"
        lib = ctypes.CDLL(path)
        lib.GetApplicationEventTarget.restype = ctypes.c_void_p
        lib.InstallEventHandler.restype = ctypes.c_int32
        lib.InstallEventHandler.argtypes = [
            ctypes.c_void_p, _HANDLER, ctypes.c_uint32, ctypes.POINTER(_EventTypeSpec),
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        lib.RegisterEventHotKey.restype = ctypes.c_int32
        lib.RegisterEventHotKey.argtypes = [
            ctypes.c_uint32, ctypes.c_uint32, _EventHotKeyID, ctypes.c_void_p,
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p)]
        lib.UnregisterEventHotKey.restype = ctypes.c_int32
        lib.UnregisterEventHotKey.argtypes = [ctypes.c_void_p]
        lib.GetEventParameter.restype = ctypes.c_int32
        lib.GetEventParameter.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32),
            ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32), ctypes.c_void_p]
        _carbon = lib
    return _carbon


def _dispatch(_call_ref, event, _user) -> int:
    try:
        hk = _EventHotKeyID()
        err = _lib().GetEventParameter(event, kEventParamDirectObject, typeEventHotKeyID, None,
                                       ctypes.sizeof(hk), None, ctypes.byref(hk))
        if err == 0 and hk.signature == SIGNATURE:
            callback = _registry.get(int(hk.id))
            if callback is not None:
                callback()
    except Exception:
        logger.exception("hot key handler failed")
    return 0


def _install() -> None:
    global _handler_ref, _cfunc
    if _handler_ref is not None:
        return
    lib = _lib()
    _cfunc = _HANDLER(_dispatch)
    spec = _EventTypeSpec(kEventClassKeyboard, kEventHotKeyPressed)
    out = ctypes.c_void_p()
    err = lib.InstallEventHandler(lib.GetApplicationEventTarget(), _cfunc, 1,
                                  ctypes.byref(spec), None, ctypes.byref(out))
    if err != 0:
        _cfunc = None
        raise OSError(f"InstallEventHandler failed: {err}")
    _handler_ref = out


class HotKeySet:
    def __init__(self) -> None:
        self._bound: dict[int, tuple[object, str]] = {}

    def bind(self, spec: str, callback) -> int:
        global _next_id
        keycode, flags, _names, _key = parse_spec(spec)
        canon = canonical(spec)
        _install()
        lib = _lib()
        hk_id = _next_id
        _next_id += 1
        out = ctypes.c_void_p()
        err = lib.RegisterEventHotKey(keycode, flags, _EventHotKeyID(SIGNATURE, hk_id),
                                      lib.GetApplicationEventTarget(), 0, ctypes.byref(out))
        if err != 0:
            raise OSError(f"RegisterEventHotKey({canon}) failed: {err}")
        _registry[hk_id] = callback
        self._bound[hk_id] = (out, canon)
        return hk_id

    def clear(self) -> None:
        for hk_id in list(self._bound):
            entry = self._bound.pop(hk_id, None)
            _registry.pop(hk_id, None)
            if entry is not None:
                try:
                    _lib().UnregisterEventHotKey(entry[0])
                except Exception:
                    logger.debug("UnregisterEventHotKey failed", exc_info=True)

    def specs(self) -> list[str]:
        return [e[1] for e in self._bound.values()]

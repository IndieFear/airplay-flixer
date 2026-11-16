"""A process-owned macOS assertion: keep the relay awake, allow display sleep."""
import ctypes
import logging
import sys

LOG = logging.getLogger(__name__)


class SleepGuard:
    def __init__(self, reason='Airplay Flixer - diffusion en cours'):
        self.identifier = None
        self.iokit = None
        self.reason = reason

    def acquire(self):
        if self.identifier is not None or sys.platform != 'darwin':
            return
        try:
            cf = ctypes.CDLL('/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation')
            io = ctypes.CDLL('/System/Library/Frameworks/IOKit.framework/IOKit')
            cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
            cf.CFStringCreateWithCString.restype = ctypes.c_void_p
            cf.CFRelease.argtypes = [ctypes.c_void_p]
            cf.CFRelease.restype = None
            io.IOPMAssertionCreateWithName.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
            io.IOPMAssertionCreateWithName.restype = ctypes.c_int32
            io.IOPMAssertionRelease.argtypes = [ctypes.c_uint32]
            io.IOPMAssertionRelease.restype = ctypes.c_int32
            kind = cf.CFStringCreateWithCString(None, b'PreventUserIdleSystemSleep', 0x08000100)
            reason = None
            try:
                reason = cf.CFStringCreateWithCString(None, self.reason.encode('utf-8'), 0x08000100)
                if not kind or not reason:
                    raise RuntimeError('CFString creation failed')
                identifier = ctypes.c_uint32()
                result = io.IOPMAssertionCreateWithName(kind, 255, reason, ctypes.byref(identifier))
                if result:
                    raise RuntimeError(f'IOKit result {result}')
                self.iokit = io
                self.identifier = identifier.value
            finally:
                if kind:
                    cf.CFRelease(kind)
                if reason:
                    cf.CFRelease(reason)
        except Exception as error:
            LOG.warning('Sleep protection unavailable: %s', type(error).__name__)

    def release(self):
        if self.identifier is None:
            return
        result = self.iokit.IOPMAssertionRelease(self.identifier)
        if result:
            LOG.warning('Sleep protection release failed: IOKit result %s', result)
            return
        self.identifier = None
        self.iokit = None

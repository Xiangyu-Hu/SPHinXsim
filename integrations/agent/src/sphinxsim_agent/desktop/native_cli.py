"""Launch the unchanged native CLI with the verified Intel runtime lifetime fix."""
import ctypes
import os
from pathlib import Path
import sys


if os.environ.get("TCM_ROOT"):
    ctypes.CDLL("libhwloc.so.15", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)
    ctypes.CDLL("libtcm.so.1", mode=os.RTLD_GLOBAL | os.RTLD_NODELETE)

if os.environ.get("SPHINXSIM_DESKTOP_RENDERER") == "nvidia":
    # A flag local to this launcher avoids relying on undocumented VGL internals.
    if os.environ.get("SPHINXSIM_VGL_LAUNCHED") != "1":
        os.environ["SPHINXSIM_VGL_LAUNCHED"] = "1"
        os.execv("/opt/VirtualGL/bin/vglrun", ["vglrun", "-d", "egl", sys.executable, __file__, *sys.argv[1:]])

    # Xvnc's root visual need not belong to an EGL framebuffer configuration.
    # QVTK embeds its render window into a Qt-created X window, so Qt must use
    # a visual selected through VirtualGL rather than the X server's default.
    class XVisualInfo(ctypes.Structure):
        _fields_ = [
            ("visual", ctypes.c_void_p), ("visualid", ctypes.c_ulong),
            ("screen", ctypes.c_int), ("depth", ctypes.c_int),
            ("visual_class", ctypes.c_int),
            ("red_mask", ctypes.c_ulong), ("green_mask", ctypes.c_ulong),
            ("blue_mask", ctypes.c_ulong),
            ("colormap_size", ctypes.c_int), ("bits_per_rgb", ctypes.c_int),
        ]

    x11 = ctypes.CDLL("libX11.so.6")
    gl = ctypes.CDLL("libGL.so.1")
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XDefaultScreen.argtypes = [ctypes.c_void_p]
    x11.XDefaultScreen.restype = ctypes.c_int
    x11.XFree.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    gl.glXChooseVisual.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_int)]
    gl.glXChooseVisual.restype = ctypes.POINTER(XVisualInfo)
    display = x11.XOpenDisplay(None)
    if not display:
        raise RuntimeError("Cannot open the desktop X display")
    try:
        # GLX_RGBA, DOUBLEBUFFER, RGB sizes and depth; terminated with None.
        attributes = (ctypes.c_int * 11)(4, 5, 8, 8, 9, 8, 10, 8, 12, 24, 0)
        visual = gl.glXChooseVisual(display, x11.XDefaultScreen(display), attributes)
        if not visual:
            raise RuntimeError("VirtualGL did not provide a compatible Qt/VTK visual")
        visual_id = visual.contents.visualid
        x11.XFree(visual)
    finally:
        x11.XCloseDisplay(display)
    from qtpy.QtWidgets import QApplication

    qt_application = QApplication(["sphinxsim-native", "-visual", hex(visual_id)])

os.chdir(Path(os.environ.get("SPHINXSIM_WORK_ROOT", "/work")) / "project")
if os.environ.get("SPHINXSIM_DESKTOP_MANAGED") == "1":
    from sphinxsim_agent.desktop.managed import main
    raise SystemExit(main())
from sphinxsim.cli import main

raise SystemExit(main())

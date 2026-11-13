"""Interactive Qt presentation of the existing PyVista/VTK scene.

Present VTK frames through Qt rather than a separate native WGL child window.
This avoids blank native surfaces with some Windows Qt/graphics combinations,
and lets WebEngine and the preview share one normally composited Qt window.
"""
from __future__ import annotations

import numpy as np
import pyvista as pv
from PySide6 import QtCore, QtGui, QtWidgets
from vtkmodules.util.numpy_support import vtk_to_numpy
from vtkmodules.vtkRenderingCore import vtkWindowToImageFilter


class PreviewViewport(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(320, 220)
        self.setFocusPolicy(QtCore.Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setToolTip("Drag to rotate; middle-drag to pan; wheel to zoom; R resets the view.")
        self.plotter = pv.Plotter(off_screen=True, border=False)
        self.plotter.iren.initialize()
        self._image = QtGui.QImage()
        self._queued = False
        self._closed = False
        self._reader = vtkWindowToImageFilter()
        self._reader.SetInput(self.plotter.render_window)
        self._reader.ReadFrontBufferOff()
        self._reader.SetInputBufferTypeToRGB()
        self._reader.ShouldRerenderOff()
        self._observer = self.plotter.render_window.AddObserver("EndEvent", self._frame_ready)
        # BasePlotter deliberately waits for show(); this embedded plotter is
        # realized by the Qt widget instead of opening a standalone VTK window.
        self.plotter._first_time = False

    def _frame_ready(self, *_):
        if not self._queued and not self._closed:
            self._queued = True
            QtCore.QTimer.singleShot(0, self._present)

    def _present(self):
        self._queued = False
        if self._closed:
            return
        self._reader.Modified()
        self._reader.Update()
        output = self._reader.GetOutput()
        width, height, _ = output.GetDimensions()
        if not width or not height:
            return
        pixels = vtk_to_numpy(output.GetPointData().GetScalars()).reshape(height, width, 3)
        pixels = np.ascontiguousarray(pixels[::-1])
        self._image = QtGui.QImage(pixels.data, width, height, pixels.strides[0],
                                   QtGui.QImage.Format.Format_RGB888).copy()
        self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.fillRect(self.rect(), QtGui.QColor("#202124"))
        if not self._image.isNull():
            painter.drawImage(self.rect(), self._image)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "plotter") and not self._closed:
            self.plotter.window_size = (max(1, self.width()), max(1, self.height()))
            self.plotter.render()

    def _position(self, event):
        modifiers = event.modifiers()
        self.plotter.iren.interactor.SetEventInformationFlipY(
            int(event.position().x()), int(event.position().y()),
            int(bool(modifiers & QtCore.Qt.KeyboardModifier.ControlModifier)),
            int(bool(modifiers & QtCore.Qt.KeyboardModifier.ShiftModifier)),
        )

    def mousePressEvent(self, event):
        self.setFocus()
        self._position(event)
        name = self._button(event.button())
        if name:
            self.plotter.iren.interactor.InvokeEvent(name + "PressEvent")
        event.accept()

    def mouseReleaseEvent(self, event):
        self._position(event)
        name = self._button(event.button())
        if name:
            self.plotter.iren.interactor.InvokeEvent(name + "ReleaseEvent")
        event.accept()

    @staticmethod
    def _button(button):
        return {QtCore.Qt.MouseButton.LeftButton: "LeftButton",
                QtCore.Qt.MouseButton.MiddleButton: "MiddleButton",
                QtCore.Qt.MouseButton.RightButton: "RightButton"}.get(button)

    def mouseMoveEvent(self, event):
        self._position(event)
        self.plotter.iren.interactor.InvokeEvent("MouseMoveEvent")
        event.accept()

    def wheelEvent(self, event):
        self._position(event)
        steps = event.angleDelta().y()
        if not steps:
            event.ignore()
            return
        for _ in range(max(1, abs(steps) // 120)):
            self.plotter.iren.interactor.InvokeEvent("MouseWheelForwardEvent" if steps > 0 else "MouseWheelBackwardEvent")
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == QtCore.Qt.Key.Key_R:
            self.plotter.subplot(0, 0)
            self.plotter.reset_camera()
            self.plotter.render()
        else:
            super().keyPressEvent(event)

    def close(self):
        self._closed = True
        self.plotter.render_window.RemoveObserver(self._observer)
        self.plotter.close()
        return super().close()

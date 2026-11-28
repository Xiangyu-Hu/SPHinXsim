"""Headless 2D scientific HUD compositor (colorbars, physical time, legend) for Cycles renders.

Uses ``PySide6.QtGui`` (a core dependency of ``sphinxsim``) so publication
colorbars, tick marks, and physical timestamps can be composited cleanly onto
ray-traced Cycles PNGs without requiring ``matplotlib`` or ``Pillow``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


def _ensure_qt_gui_application() -> Any:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6 import QtWidgets

    app = QtWidgets.QApplication.instance()
    if app is None:
        app = QtWidgets.QApplication(["sphinxsim-overlay"])
    return app


def _format_tick(value: float) -> str:
    abs_val = abs(value)
    if abs_val == 0.0:
        return "0"
    if abs_val >= 1e4 or abs_val < 1e-3:
        return f"{value:.2e}"
    return f"{value:.4g}"


def composite_scientific_overlay(
    image_path: str | Path,
    overlay_spec: dict[str, Any],
    *,
    output_path: str | Path | None = None,
) -> Path:
    """Composite physical timestamp, legend swatches, and scalar colorbars onto ``image_path``."""
    target = Path(output_path or image_path).resolve()
    source = Path(image_path).resolve()
    if not overlay_spec:
        if target != source:
            target.write_bytes(source.read_bytes())
        return target

    time_val = overlay_spec.get("time")
    is_relaxation = bool(overlay_spec.get("is_relaxation", False))
    colorbars = overlay_spec.get("colorbars") or []
    legend = overlay_spec.get("legend") or []
    if time_val is None and not is_relaxation and not colorbars and not legend:
        if target != source:
            target.write_bytes(source.read_bytes())
        return target

    _ensure_qt_gui_application()
    from PySide6 import QtCore, QtGui

    img = QtGui.QImage(str(source))
    if img.isNull():
        raise ValueError(f"Could not load rendered PNG for overlay compositing: {source}")
    if img.format() != QtGui.QImage.Format.Format_ARGB32_Premultiplied:
        img = img.convertToFormat(QtGui.QImage.Format.Format_ARGB32_Premultiplied)

    w = max(img.width(), 1)
    h = max(img.height(), 1)
    scale = max(min(w / 1600.0, h / 900.0), 0.55)
    margin = int(round(24 * scale))

    painter = QtGui.QPainter(img)
    painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QtGui.QPainter.RenderHint.TextAntialiasing, True)

    font_title = QtGui.QFont("Sans Serif")
    font_title.setPixelSize(max(int(round(16 * scale)), 10))
    font_title.setBold(True)

    font_tick = QtGui.QFont("Monospace")
    font_tick.setStyleHint(QtGui.QFont.StyleHint.TypeWriter)
    font_tick.setPixelSize(max(int(round(14 * scale)), 9))

    panel_bg = QtGui.QColor(18, 21, 26, 208)
    panel_border = QtGui.QColor(140, 150, 165, 110)
    text_col = QtGui.QColor(235, 240, 246, 255)

    top_left_y = margin
    # 1. Physical timestamp badge (top-left)
    if time_val is not None or is_relaxation:
        label_txt = f"t = {float(time_val):.4g} s" if time_val is not None else "Particle relaxation"
        painter.setFont(font_title)
        fm = QtGui.QFontMetrics(font_title)
        pad_x, pad_y = int(round(14 * scale)), int(round(8 * scale))
        box_w = fm.horizontalAdvance(label_txt) + 2 * pad_x
        box_h = fm.height() + 2 * pad_y
        rect = QtCore.QRectF(margin, top_left_y, box_w, box_h)
        painter.setPen(QtGui.QPen(panel_border, 1.0))
        painter.setBrush(panel_bg)
        painter.drawRoundedRect(rect, 6.0 * scale, 6.0 * scale)
        painter.setPen(text_col)
        painter.drawText(rect, int(QtCore.Qt.AlignmentFlag.AlignCenter), label_txt)
        top_left_y += int(box_h + round(12 * scale))

    # 2. Body legend swatches (top-left, below timestamp)
    if legend:
        painter.setFont(font_tick)
        fm_leg = QtGui.QFontMetrics(font_tick)
        swatch = max(int(round(14 * scale)), 8)
        pad = int(round(12 * scale))
        row_h = max(fm_leg.height(), swatch) + int(round(6 * scale))
        max_txt_w = max(fm_leg.horizontalAdvance(str(item["label"])) for item in legend)
        leg_w = pad * 2 + swatch + int(round(10 * scale)) + max_txt_w
        leg_h = pad * 2 + row_h * len(legend) - int(round(6 * scale))
        leg_rect = QtCore.QRectF(margin, top_left_y, leg_w, leg_h)
        painter.setPen(QtGui.QPen(panel_border, 1.0))
        painter.setBrush(panel_bg)
        painter.drawRoundedRect(leg_rect, 6.0 * scale, 6.0 * scale)

        for idx, item in enumerate(legend):
            ry = top_left_y + pad + idx * row_h
            rgb = item.get("color", [0.6, 0.6, 0.6])
            c_q = QtGui.QColor(
                int(round(float(rgb[0]) * 255)),
                int(round(float(rgb[1]) * 255)),
                int(round(float(rgb[2]) * 255)),
            )
            s_rect = QtCore.QRectF(margin + pad, ry + (row_h - swatch) * 0.5 - 2, swatch, swatch)
            painter.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 120), 1.0))
            painter.setBrush(c_q)
            painter.drawRoundedRect(s_rect, 3.0 * scale, 3.0 * scale)
            painter.setPen(text_col)
            t_rect = QtCore.QRectF(
                margin + pad + swatch + round(10 * scale),
                ry - 2,
                max_txt_w + 8,
                row_h,
            )
            painter.drawText(
                t_rect,
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                str(item["label"]),
            )

    # 3. Scalar colorbars (bottom-right stack)
    if colorbars:
        bar_w = max(int(round(280 * scale)), 140)
        bar_h = max(int(round(16 * scale)), 10)
        pad = int(round(14 * scale))
        fm_title = QtGui.QFontMetrics(font_title)
        fm_tick = QtGui.QFontMetrics(font_tick)
        card_w = bar_w + 2 * pad
        card_h = pad * 2 + fm_title.height() + bar_h + fm_tick.height() + int(round(16 * scale))

        cursor_y = h - margin - card_h
        for cb in reversed(colorbars[:3]):
            card_rect = QtCore.QRectF(w - margin - card_w, cursor_y, card_w, card_h)
            painter.setPen(QtGui.QPen(panel_border, 1.0))
            painter.setBrush(panel_bg)
            painter.drawRoundedRect(card_rect, 7.0 * scale, 7.0 * scale)

            # Title
            painter.setFont(font_title)
            painter.setPen(text_col)
            title_rect = QtCore.QRectF(
                card_rect.left() + pad,
                card_rect.top() + pad - 2,
                bar_w,
                fm_title.height() + 4,
            )
            elided = fm_title.elidedText(str(cb.get("title", "Scalar")), QtCore.Qt.TextElideMode.ElideRight, bar_w)
            painter.drawText(
                title_rect,
                int(QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter),
                elided,
            )

            # Gradient bar
            grad_top = title_rect.bottom() + round(6 * scale)
            grad_rect = QtCore.QRectF(card_rect.left() + pad, grad_top, bar_w, bar_h)
            grad = QtGui.QLinearGradient(grad_rect.left(), grad_rect.top(), grad_rect.right(), grad_rect.top())
            for pos, rgb in cb.get("stops") or [[0.0, [0.23, 0.30, 0.75]], [1.0, [0.71, 0.02, 0.15]]]:
                grad.setColorAt(
                    float(max(0.0, min(1.0, pos))),
                    QtGui.QColor(
                        int(round(float(rgb[0]) * 255)),
                        int(round(float(rgb[1]) * 255)),
                        int(round(float(rgb[2]) * 255)),
                    ),
                )
            painter.setPen(QtGui.QPen(QtGui.QColor(220, 225, 235, 160), 1.0))
            painter.setBrush(QtGui.QBrush(grad))
            painter.drawRoundedRect(grad_rect, 2.0 * scale, 2.0 * scale)

            # Min / mid / max ticks
            vmin = float(cb.get("vmin", 0.0))
            vmax = float(cb.get("vmax", 1.0))
            vmid = 0.5 * (vmin + vmax)
            painter.setFont(font_tick)
            painter.setPen(text_col)
            tick_top = grad_rect.bottom() + round(5 * scale)
            tick_rect = QtCore.QRectF(card_rect.left() + pad, tick_top, bar_w, fm_tick.height() + 2)
            painter.drawText(tick_rect, int(QtCore.Qt.AlignmentFlag.AlignLeft), _format_tick(vmin))
            painter.drawText(tick_rect, int(QtCore.Qt.AlignmentFlag.AlignHCenter), _format_tick(vmid))
            painter.drawText(tick_rect, int(QtCore.Qt.AlignmentFlag.AlignRight), _format_tick(vmax))

            cursor_y -= int(card_h + round(10 * scale))

    painter.end()
    target.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(target), "PNG"):
        raise OSError(f"Failed to save composited overlay PNG to {target}")
    return target

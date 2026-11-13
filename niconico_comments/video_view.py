"""動画の表示領域。アスペクト比を保って中央に描き、その矩形をコメントに通知する。"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QSizePolicy, QWidget


class VideoView(QWidget):
    """QVideoSink から受け取ったフレームを描画するウィジェット。

    描画後・サイズ変更後・各種状態変化時に `video_rect_changed` を出す。
    """

    video_rect_changed = Signal(QRectF)
    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(320, 180)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setStyleSheet("background: black;")
        self._frame = None
        self._has_video = False
        self._video_rect = QRectF()

    # ------------------------------------------------------------------ API
    def set_frame(self, image) -> None:
        """QImage を受け取って表示する。"""
        self._frame = image
        self._has_video = True
        self.update()

    def clear(self) -> None:
        self._frame = None
        self._has_video = False
        self.update()

    @property
    def has_video(self) -> bool:
        return self._has_video

    def video_rect(self) -> QRectF:
        return QRectF(self._video_rect)

    # ------------------------------------------------------------------ 描画
    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0))
        if self._frame is not None and not self._frame.isNull():
            src_w = self._frame.width()
            src_h = self._frame.height()
            if src_w > 0 and src_h > 0:
                scale = min(self.width() / src_w, self.height() / src_h)
                w = src_w * scale
                h = src_h * scale
                rect = QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)
                painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
                painter.drawImage(rect, self._frame)
                if not self._video_rect.isValid() or rect != self._video_rect:
                    self._video_rect = rect
                    self.video_rect_changed.emit(QRectF(rect))
                return
        self._video_rect = QRectF(0, 0, self.width(), self.height())

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._frame is None or self._frame.isNull():
            self._video_rect = QRectF(0, 0, self.width(), self.height())
            self.video_rect_changed.emit(QRectF(self._video_rect))
        else:
            self.update()

    # ------------------------------------------------------------------ マウス
    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)

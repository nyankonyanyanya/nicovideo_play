"""コメントを動画の上に描画する QWidget（ニコ動のコメントレイヤー相当）。"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QWidget

from .comment_parser import Comment
from .layout import CommentLayout

# コメントの縁取り太さ（px）。文字サイズに応じて少し太くする。
OUTLINE_MIN_PX = 2.0
OUTLINE_RATIO = 0.06

# ニコ動風のゴシック体を優先順に探す
PREFERRED_FONTS = (
    "MS Gothic",
    "Meiryo",
    "Yu Gothic UI",
    "Yu Gothic",
    "Noto Sans JP",
    "MS UI Gothic",
)


def resolve_comment_font() -> QFont:
    """この環境で実際に使える日本語フォントを選ぶ。"""
    try:
        families = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001
        families = set()
    for name in PREFERRED_FONTS:
        if name in families:
            return QFont(name)
    return QFont("sans-serif")


class CommentOverlay(QWidget):
    """動画の上に重ねる透明なコメント描画レイヤー。

    動画の表示領域（アスペクト比維持後の矩形）にだけ描画する。
    マウス操作は裏の動画ビューに渡す。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        self.layout = CommentLayout([])
        self.current_time_ms: int = 0
        self.comments_enabled: bool = True
        self.video_rect = QRectF(0, 0, 1280, 720)

        self._font_cache: dict[int, QFont] = {}
        self._base_font = resolve_comment_font()
        self.resize(1280, 720)

    # ------------------------------------------------------------------ 設定
    def set_comments(self, comments: list[Comment]) -> None:
        self.layout = CommentLayout(comments)
        self.layout.rebuild(self.video_rect.width(), self.video_rect.height())
        self.update()

    def set_current_time(self, time_ms: int) -> None:
        self.current_time_ms = max(0, int(time_ms))

    def set_comments_enabled(self, enabled: bool) -> None:
        self.comments_enabled = enabled
        self.update()

    def set_video_rect(self, rect: QRectF) -> None:
        """動画が表示されている領域を設定する。変わったらレイアウトを組み直す。"""
        rect = QRectF(rect)
        if rect.width() < 1 or rect.height() < 1:
            return
        if (
            abs(rect.width() - self.video_rect.width()) > 0.5
            or abs(rect.height() - self.video_rect.height()) > 0.5
        ):
            self.layout.rebuild(rect.width(), rect.height())
        self.video_rect = rect
        self.update()

    # ------------------------------------------------------------------ 描画
    def paintEvent(self, event) -> None:  # noqa: N802 (Qt API)
        if not self.comments_enabled or not self.layout.laid:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        area = self.video_rect
        painter.setClipRect(area)
        for item, x, y in self.layout.visible(self.current_time_ms):
            self._draw_item(painter, item, area.left() + x, area.top() + y)

    def _font_for(self, px: float) -> QFont:
        key = max(6, int(round(px)))
        font = self._font_cache.get(key)
        if font is None:
            font = QFont(self._base_font)
            font.setPixelSize(key)
            font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
            self._font_cache[key] = font
        return font

    def _draw_item(self, painter: QPainter, item, x: float, y: float) -> None:
        style = item.style
        text = item.comment.body
        font = self._font_for(item.font_px)
        fm = QFontMetricsF(font)
        width = fm.horizontalAdvance(text)
        baseline = y + fm.ascent()

        painter.save()
        if style.rotate:
            # 文字ブロックの中心を軸に回転させる（回転コメント）
            center = QPointF(x + width / 2.0, y + item.font_px / 2.0)
            painter.translate(center)
            painter.rotate(style.rotate)
            painter.translate(-center)

        path = QPainterPath()
        path.addText(x, baseline, font, text)

        outline_px = max(OUTLINE_MIN_PX, item.font_px * OUTLINE_RATIO)
        pen = QPen(QColor(0, 0, 0), outline_px)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        painter.setPen(pen)
        painter.setBrush(QColor(style.color))
        painter.drawPath(path)
        painter.restore()

    # ------------------------------------------------------------------ 情報
    def visible_count(self, time_ms: int) -> int:
        return len(self.layout.visible(time_ms))

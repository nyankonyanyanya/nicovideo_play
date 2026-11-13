"""コメントの画面レイアウトとスクロール位置を計算するモジュール（GUI非依存）。

ニコ動と同じく「コメントは動画時刻に紐づき、動画を一時停止すると止まる」モデルで扱う。
スクロール中の位置は毎フレーム時刻から求め直す。

座標系はコメントレイヤー（動画の表示領域）の幅・高さ(px)、左上原点。
"""

from __future__ import annotations

import bisect
import unicodedata
from dataclasses import dataclass

from .commands import POS_BOTTOM, POS_MIDDLE, POS_TOP, CommandStyle
from .comment_parser import Comment

# ニコ動の標準コメントサイズ
BASE_FONT_PX = 25
# スクロール速度（px/秒）。動画幅を通過する時間は約4秒。
SCROLL_PX_PER_SEC = 285.0
# 固定コメント（ue/shita）の表示時間
FIXED_DURATION_MS = 3000
# レーン間の余白
LANE_GAP_PX = 4
LANE_HEIGHT_RATIO = 1.2
# 同じレーンで同時に重ねてよいコメント数（超えると間引いて読めるようにする）
MAX_PER_LANE = 3
# ニコ動本家の初期座標（動画高さに対する比率）
FIXED_BASE_RATIO = 4.0 / 5.0

_POS_INDEX_TOP = 0
_POS_INDEX_BOTTOM = 1
_POS_INDEX_MIDDLE = 2


@dataclass
class LaidOutComment:
    comment: Comment
    lane: int
    font_px: float
    text_width: float
    fixed: bool
    end_ms: int

    @property
    def style(self) -> CommandStyle:
        return self.comment.style

    @property
    def start_ms(self) -> int:
        return self.comment.vpos_ms

    @property
    def lane_height(self) -> float:
        return self.font_px * LANE_HEIGHT_RATIO + LANE_GAP_PX


class CommentLayout:
    """ウィンドウサイズに応じてコメントを配置し、時刻ごとの描画位置を返す。"""

    def __init__(self, comments: list[Comment]) -> None:
        self.comments = comments
        self.width = 1280.0
        self.height = 720.0
        self.scale = 1.0
        self.laid: list[LaidOutComment] = []
        self._starts: list[list[int]] = [[], [], []]  # ue / shita / naka ごとの開始時刻
        self._items: list[list[LaidOutComment]] = [[], [], []]
        self._base_px = 0.0
        self._lane_count = 0
        self.rebuild(1280.0, 720.0)

    # ------------------------------------------------------------------ 構築
    def rebuild(self, width: float, height: float) -> None:
        """幅/高さが変わったら配置をやり直す。サイズが同じ間は何もしない。"""
        width = max(1.0, float(width))
        height = max(1.0, float(height))
        # 720p を基準に、小さい画面では全体を縮小する
        scale = min(1.0, height / 720.0)
        base_px = BASE_FONT_PX * scale
        lane_count = max(1, int(height // (base_px * LANE_HEIGHT_RATIO + LANE_GAP_PX)))
        if (
            self.laid
            and lane_count == self._lane_count
            and abs(base_px - self._base_px) < 0.01
            and abs(width - self.width) < 0.5
        ):
            self.height = height
            return
        self.width = width
        self.height = height
        self.scale = scale
        self._base_px = base_px
        self._lane_count = lane_count

        # 位置ごとに (レーン, そこに居るコメントの終了時刻リスト) を管理する
        lanes: dict[str, list[list[int]]] = {
            POS_TOP: [[] for _ in range(lane_count)],
            POS_MIDDLE: [[] for _ in range(lane_count)],
            POS_BOTTOM: [[] for _ in range(lane_count)],
        }
        laid: list[LaidOutComment] = []
        for cmt in sorted(self.comments, key=lambda c: (c.vpos_ms, c.no)):
            style = cmt.style
            font_px = base_px * style.size_scale
            text_width = measure_text(cmt.body, font_px)
            fixed = style.position != POS_MIDDLE
            start_ms = cmt.vpos_ms
            if fixed:
                end_ms = start_ms + FIXED_DURATION_MS
            else:
                end_ms = start_ms + int((width + text_width) / SCROLL_PX_PER_SEC * 1000)
            lane = _acquire_lane(lanes[style.position], start_ms, end_ms)
            laid.append(
                LaidOutComment(
                    comment=cmt,
                    lane=lane,
                    font_px=font_px,
                    text_width=text_width,
                    fixed=fixed,
                    end_ms=end_ms,
                )
            )

        self.laid = laid
        buckets: list[list[LaidOutComment]] = [[], [], []]
        for item in laid:
            buckets[_position_index(item.style.position)].append(item)
        self._items = buckets
        self._starts = [[item.start_ms for item in bucket] for bucket in buckets]

    # ------------------------------------------------------------------ 取得
    def visible(self, time_ms: int) -> list[tuple[LaidOutComment, float, float]]:
        """指定時刻に表示中のコメントを (コメント, x, y) で返す。x は描画左上。

        x は動画座標（レイヤー左上が原点）。シークで巻き戻しても正しく出せるよう、
        時刻が正しいコメントだけを二分探索で拾う。

        動画では「下段コメント(=1行分)を入れると画面のほぼ下端、
        中段スクロール(=中央行)を入れるとほぼ中央」になっていることがあるため、
        下段・中段の1行目はニコ動本家と同じく動画高さの 4/5 の位置に置く。
        """
        out: list[tuple[LaidOutComment, float, float]] = []
        for bucket_index in range(3):
            starts = self._starts[bucket_index]
            if not starts:
                continue
            items = self._items[bucket_index]
            # 終了時刻は「開始 + (幅 + 文字幅)/速度」なので、十分余裕を取った下限から走査する
            lo_ms = time_ms - int((self.width + 6000) / SCROLL_PX_PER_SEC * 1000)
            idx = bisect.bisect_left(starts, max(0, lo_ms))
            is_scroll = bucket_index == _POS_INDEX_MIDDLE
            for item in items[idx:]:
                start = item.start_ms
                if start > time_ms:
                    break
                if item.fixed:
                    if time_ms - start < FIXED_DURATION_MS:
                        out.append((item, 0.0, self._fixed_y(item)))
                    continue
                x = self.width - (time_ms - start) / 1000.0 * SCROLL_PX_PER_SEC
                if x + item.text_width >= 0:
                    y = item.lane * item.lane_height if is_scroll else self._fixed_y(item)
                    out.append((item, x, y))
        return out

    def upcoming_count(self, time_ms: int) -> int:
        return sum(len(b) - bisect.bisect_right(s, time_ms) for s, b in zip(self._starts, self._items))

    # ------------------------------------------------------------------ 内部
    def _fixed_y(self, item: LaidOutComment) -> float:
        """ue / shita の y 座標。1行目は動画高さの 4/5 を基準にする。"""
        lane_h = item.lane_height
        if item.style.position == POS_BOTTOM:
            # 1行目（レーン0）が 4/5 の位置に来るよう、下に向かって重ねる
            return self.height * FIXED_BASE_RATIO + item.lane * lane_h
        return item.lane * lane_h


def _position_index(position: str) -> int:
    if position == POS_TOP:
        return _POS_INDEX_TOP
    if position == POS_BOTTOM:
        return _POS_INDEX_BOTTOM
    return _POS_INDEX_MIDDLE


def _acquire_lane(lanes: list[list[int]], start_ms: int, end_ms: int) -> int:
    """空いているレーンを確保する。混雑してきたら上から順に間引く。

    レーンを使うのは「その時刻にまだ画面にいるため終了時刻が start より後」の
    コメントだけなので、古い終了時刻を捨てつつ残数を数える。
    """
    first_with_room = -1
    least_loaded = 0
    least_load = 10**9
    for lane, ends in enumerate(lanes):
        alive = [e for e in ends if e > start_ms]
        lanes[lane] = alive
        # 終了が最も遅いコメントで「次に空くのはいつか」を判断する。
        # 自分の開始に間に合ったかは、開始時刻以降に終わる相手がいるかで見る。
        if not alive:
            lanes[lane].append(end_ms)
            return lane
        load = len(alive)
        if load < least_load:
            least_load = load
            least_loaded = lane
        if load < MAX_PER_LANE and first_with_room < 0:
            # 上から見ていき、ラップ速度より遅い相手がいないレーンを優先する
            head_end = max(alive)
            gap = end_ms - head_end
            if gap >= start_ms:
                first_with_room = lane
    if first_with_room >= 0:
        lanes[first_with_room].append(end_ms)
        return first_with_room
    # 全レーンが混雑: 一番空いているレーンに重ねる
    lanes[least_loaded].append(end_ms)
    return least_loaded


def measure_text(text: str, font_px: float) -> float:
    """フォントを使わず文字幅を概算する（全角1em / 半角0.55em）。"""
    width = 0.0
    for ch in text:
        width += 0.55 if unicodedata.east_asian_width(ch) in ("Na", "H") else 1.0
    return width * font_px

"""ニコニコ動画のコメントコマンド（commands 配列）を解釈するモジュール。

対応しているコマンド:
    ue / shita / naka          位置        big / medium / small   サイズ
    red blue green yellow cyan magenta purple black white  色
    niconicoblack             投稿者による黒コメント
    full                     全画面（初期版は既定サイズで扱う）
    invisible                 非表示
    rotate / ender / patissier テキストを回転
    device:xxx                デバイス情報（表示しない）
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

# 位置
POS_TOP = "top"
POS_BOTTOM = "bottom"
POS_MIDDLE = "middle"

# サイズ倍率（big は公式の 1.5 倍より少し控えめに 1.25 倍にしている）
SIZE_SMALL = 0.75
SIZE_MEDIUM = 1.0
SIZE_BIG = 1.5

COLOR_NAMES: dict[str, str] = {
    "white": "#FFFFFF",
    "red": "#FF0000",
    "pink": "#FF8080",
    "orange": "#FFC000",
    "yellow": "#FFFF00",
    "green": "#00FF00",
    "cyan": "#00FFFF",
    "blue": "#0000FF",
    "purple": "#C000FF",
    "black": "#000000",
}

# 回転がありえない設定値のときに付く prefix を保持する
_IGNORED_COMMANDS = {
    "184",
    "issp",
    "premium",
    "akari",
    "nicoru",
    "niconicoblack",
    "full",
    "invisible",
    "ender",
    "patissier",
    "sender",
    "ninja",
    "s_wall_side",
}


def _parse_color(token: str) -> Optional[str]:
    """コマンド1個から16進カラーを取り出す。取れなければ None。"""
    if token in COLOR_NAMES:
        return COLOR_NAMES[token]
    if len(token) == 7 and token.startswith("#"):
        try:
            int(token[1:], 16)
        except ValueError:
            return None
        return token.upper()
    return None


def _parse_vpos(token: str) -> Optional[int]:
    """投稿者コメントの @vpos（センチ秒）をミリ秒に変換する。"""
    try:
        return int(float(token) * 10)
    except ValueError:
        return None


@dataclass(frozen=True)
class CommandStyle:
    """コマンド解釈の結果。"""

    position: str = POS_MIDDLE
    emoji: str = ""
    size_scale: float = SIZE_MEDIUM
    color: str = COLOR_NAMES["white"]
    rotate: float = 0.0  # 度。0 で回転なし。
    invisible: bool = False
    has_color: bool = False
    vpos_override: Optional[int] = None  # 投稿者コメントの @vpos 指定 (ms)


def parse_commands(commands: Iterable[str] | None) -> CommandStyle:
    """`commands` 配列を CommandStyle に変換する。未知のコマンドは無視する。"""
    position = POS_MIDDLE
    emoji = ""
    size_scale = SIZE_MEDIUM
    color = COLOR_NAMES["white"]
    rotate = 0.0
    invisible = False
    has_color = False
    vpos_override: Optional[int] = None

    vpos_text: Optional[str] = None

    for raw in commands or ():
        token = (raw or "").strip()
        if not token:
            continue
        low = token.lower()
        if vpos_text is not None:
            # "@vpos" の直後に来たトークンを値として扱う
            if token.lstrip("-").isdigit():
                vpos_override = _parse_vpos(token)
                vpos_text = None
                continue
            vpos_text = None
        if low in ("ue", "top"):
            position = POS_TOP
        elif low in ("shita", "bottom"):
            position = POS_BOTTOM
        elif low in ("naka", "middle"):
            position = POS_MIDDLE
        elif low == "big":
            size_scale = SIZE_BIG
        elif low == "medium":
            size_scale = SIZE_MEDIUM
        elif low == "small":
            size_scale = SIZE_SMALL
        elif low == "invisible":
            invisible = True
        elif low in ("rotate", "ender"):
            rotate = -90.0
        elif low == "patissier":
            rotate = 90.0
        elif low == "@vpos":
            vpos_text = token
        elif low.startswith("@vpos"):
            parsed_vpos = _parse_vpos(token[5:])
            if parsed_vpos is not None:
                vpos_override = parsed_vpos
        elif token.startswith("@emoji"):
            emoji = token[6:]
        else:
            parsed = _parse_color(low)
            if parsed is not None:
                color = parsed
                has_color = True
    return CommandStyle(
        position=position,
        emoji=emoji,
        size_scale=size_scale,
        color=color,
        rotate=rotate,
        invisible=invisible,
        has_color=has_color,
        vpos_override=vpos_override,
    )


def normalise_rotate(angle: float) -> float:
    """-90 / 90 の回転を -90..90 に正規化する（PI の倍数以外は丸める）。"""
    if angle == 0.0:
        return 0.0
    return math.copysign(90.0, angle)


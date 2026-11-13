"""ニコニコ動画のコメントJSONを読み込み、コメント一覧に変換するモジュール。

対応している JSON 形式:

1. ニコ動API (nvapi) 形式 — 手元の sm32738447.json もこの形式
   {
     "meta": {...},
     "data": {
       "globalComments": [...],
       "threads": [
         {"id": "1518607117", "fork": "owner", "commentCount": 0, "comments": []},
         {"id": "1518607117", "fork": "main",  "commentCount": 1719,
          "comments": [{"id": "...", "no": 538, "vposMs": 121560,
                        "body": "原曲知らないニキやべえぞ",
                        "commands": ["184"], "userId": "...", ...}]}
       ]
     }
   }

2. シンプルな配列形式（自作 JSON や投稿者コメントのエクスポート用）
   [{"vpos": 0.5, "body": "test"}, {"vposMs": 1200, "text": "hoge", "commands": ["ue"]}]
   {"comments": [{"time": 1.0, "body": "fuga"}]}

時刻は `vpos` (秒) / `vposMs` / `vpos_ms` / `time` / `start` を受け付ける。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Iterable

from .commands import CommandStyle, parse_commands

FORK_OWNER = "owner"

# コメントが無くても GUI では日本語の案内を出すためのエラークラス
class CommentParseError(ValueError):
    """JSONをコメント一覧に変換できなかったときに送出する。"""


@dataclass(frozen=True)
class Comment:
    no: int
    vpos_ms: int  # 動画内の時刻（ミリ秒）
    body: str
    style: CommandStyle
    is_owner: bool = False
    is_easy: bool = False
    posted_at: str = ""

    @property
    def time_ms(self) -> int:
        return self.vpos_ms


@dataclass
class CommentSet:
    comments: list[Comment] = field(default_factory=list)
    thread_id: str = ""
    source_path: str = ""
    detected_format: str = ""
    warnings: list[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.comments)

    def duration_ms(self) -> int:
        return max((c.vpos_ms for c in self.comments), default=0)


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return int(float(text))
    except ValueError:
        return None


def _time_to_ms(obj: dict[str, Any]) -> int | None:
    """コメント辞書から時刻（ミリ秒）を拾う。優先度: vposMs > vpos > time > start。"""
    for key in ("vposMs", "vpos_ms"):
        if key in obj:
            val = _as_int(obj[key])
            if val is not None:
                return val
    for key in ("vpos", "time", "start", "second", "sec"):
        if key in obj:
            raw = obj[key]
            try:
                return int(round(float(raw) * 1000.0))
            except (TypeError, ValueError):
                continue
    return None


def _normalise_commands(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return value.split()
    if isinstance(value, (list, tuple)):
        result: list[str] = []
        for item in value:
            if isinstance(item, str):
                result.extend(item.split())
        return result
    return []


def _comment_from_dict(
    obj: dict[str, Any], *, is_owner: bool, is_easy: bool, index: int
) -> Comment | None:
    body = obj.get("body")
    if body is None:
        body = obj.get("text", obj.get("content", ""))
    if body is None:
        body = ""
    body = str(body).replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    if not body.strip():
        return None

    commands = _normalise_commands(obj.get("commands"))
    commands.extend(_normalise_commands(obj.get("command")))
    style = parse_commands(commands)
    if style.invisible:
        return None

    vpos = style.vpos_override
    if vpos is None:
        vpos = _time_to_ms(obj)
    if vpos is None:
        return None
    if vpos < 0:
        vpos = 0

    no = _as_int(obj.get("no", obj.get("index")))
    if no is None:
        no = index

    return Comment(
        no=no,
        vpos_ms=vpos,
        body=body,
        style=style,
        is_owner=is_owner,
        is_easy=is_easy,
        posted_at=str(obj.get("postedAt") or obj.get("date") or ""),
    )


def _iter_list_of_dicts(container: Any) -> Iterable[dict[str, Any]]:
    if isinstance(container, list):
        for item in container:
            if isinstance(item, dict):
                yield item
    elif isinstance(container, dict):
        for value in container.values():
            if isinstance(value, dict):
                yield value


def parse_comment_json(obj: Any) -> CommentSet:
    """JSONとして読み込んだオブジェクトを CommentSet に変換する。"""
    result = CommentSet()

    data = obj.get("data") if isinstance(obj, dict) else None
    if isinstance(data, dict) and ("threads" in data or "thread" in data):
        result.detected_format = "nvapi"
        result.thread_id = str(data.get("threadId") or data.get("id") or "")
        threads = data.get("threads")
        if threads is None and isinstance(data.get("thread"), dict):
            threads = [data["thread"]]
        for thread in threads or []:
            if not isinstance(thread, dict):
                continue
            fork = str(thread.get("fork") or "").lower()
            # 投稿者コメントは fork="owner" にまとまっている（スレッドIDは本編と同じ）
            is_owner = fork == FORK_OWNER
            is_easy = fork == "easy"
            for idx, raw in enumerate(thread.get("comments") or []):
                if not isinstance(raw, dict):
                    continue
                cmt = _comment_from_dict(raw, is_owner=is_owner, is_easy=is_easy, index=idx)
                if cmt is not None:
                    result.comments.append(cmt)
        if not result.thread_id:
            first = next((t for t in threads or [] if isinstance(t, dict)), None)
            if first:
                result.thread_id = str(first.get("id") or "")
        for thread in threads or []:
            if not isinstance(thread, dict):
                continue
            count = _as_int(thread.get("commentCount")) or 0
            got = len(thread.get("comments") or [])
            if count and count > got:
                result.warnings.append(
                    f"スレッド {thread.get('id')} ({thread.get('fork')}) のコメントは "
                    f"{count} 件中 {got} 件のみ含まれています（APIのページ分けが必要）。"
                )
    elif isinstance(obj, dict) and any(k in obj for k in ("comments", "thread", "threads", "list")):
        result.detected_format = "list"
        container = obj.get("comments")
        if container is None:
            container = obj.get("thread", obj.get("threads", obj.get("list")))
        for idx, raw in enumerate(_iter_list_of_dicts(container)):
            cmt = _comment_from_dict(raw, is_owner=False, is_easy=False, index=idx)
            if cmt is not None:
                result.comments.append(cmt)
    elif isinstance(obj, list):
        result.detected_format = "array"
        for idx, raw in enumerate(obj):
            if not isinstance(raw, dict):
                continue
            cmt = _comment_from_dict(raw, is_owner=False, is_easy=False, index=idx)
            if cmt is not None:
                result.comments.append(cmt)
    else:
        raise CommentParseError(
            "ニコ動のコメントJSONとして認識できませんでした。"
            "'data.threads' またはコメント配列が必要です。"
        )

    result.comments.sort(key=lambda c: (c.vpos_ms, c.no))
    return result


def load_comment_json(path: str) -> CommentSet:
    """JSON ファイルを読み込んで CommentSet を返す。"""
    if not os.path.isfile(path):
        raise CommentParseError(f"ファイルが見つかりません: {path}")
    try:
        with open(path, "r", encoding="utf-8-sig") as fp:
            obj = json.load(fp)
    except json.JSONDecodeError as exc:
        raise CommentParseError(f"JSONの形式が不正です: {exc}") from exc
    except OSError as exc:
        raise CommentParseError(f"ファイルを開けません: {exc}") from exc
    result = parse_comment_json(obj)
    result.source_path = path
    if not result.comments:
        raise CommentParseError("コメントが1件も見つかりませんでした。")
    return result

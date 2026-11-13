"""サンプル JSON の投稿密度（時刻ごとのコメント数）を調べる確認用スクリプト。"""

from __future__ import annotations

import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from niconico_comments.comment_parser import load_comment_json  # noqa: E402


def main(path: str) -> None:
    comment_set = load_comment_json(path)
    buckets = collections.Counter(c.vpos_ms // 5000 * 5 for c in comment_set.comments)
    print(f"{path}: {len(comment_set)} comments, max={comment_set.duration_ms() / 1000:.1f}s")
    for start in range(0, int(comment_set.duration_ms() / 1000) + 5, 5):
        n = buckets.get(start, 0)
        print(f"  {start:4d}s {'#' * n} ({n})")
    by_no = sorted(comment_set.comments, key=lambda c: c.no)
    print("first 10 by no:")
    for c in by_no[:10]:
        print(f"  no={c.no} {c.vpos_ms / 1000:7.3f}s {c.body!r}")
    print("first 10 by time:")
    for c in comment_set.comments[:10]:
        print(f"  no={c.no} {c.vpos_ms / 1000:7.3f}s {c.body!r}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "sm32738447.json")

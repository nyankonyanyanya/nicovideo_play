"""ニコ動コメントプレイヤーの起動スクリプト。

使い方:
    python niconico_player.py                     # 空の画面から開始
    python niconico_player.py video.mp4 data.json # ファイルを指定して起動
    python niconico_player.py sm32738447.mp4      # 同名の .json を自動で読み込む
"""

from __future__ import annotations

import os
import sys


def _check_pyside() -> None:
    try:
        import PySide6  # noqa: F401
        from PySide6.QtMultimedia import QMediaPlayer  # noqa: F401
    except ImportError:
        message = (
            "PySide6 が見つかりません。\n\n"
            "次のコマンドでインストールしてください:\n\n"
            "    python -m pip install PySide6\n\n"
            "（動画再生には QtMultimedia が必要です）"
        )
        if sys.platform == "win32" and "--no-gui" not in sys.argv:
            try:
                import tkinter
                from tkinter import messagebox

                root = tkinter.Tk()
                root.withdraw()
                messagebox.showerror("ニコ動コメントプレイヤー", message)
                root.destroy()
            except Exception:  # noqa: BLE001
                print(message, file=sys.stderr)
        else:
            print(message, file=sys.stderr)
        sys.exit(1)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    _check_pyside()

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    from niconico_comments.main_window import MainWindow

    if sys.platform == "win32":
        # Windows のタスクバーでアイコンを分けて表示する
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "niconico.comment.player.1"
            )
        except Exception:  # noqa: BLE001
            pass

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication(argv)
    app.setApplicationName("ニコ動コメントプレイヤー")
    app.setOrganizationName("niconico-comments")

    window = MainWindow()
    window.show()

    paths = [os.path.abspath(p) for p in argv[1:] if os.path.isfile(p)]
    if paths:
        window.start_with_paths(paths)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())

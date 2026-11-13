# ニコ動コメントプレイヤー

ローカルの動画ファイルと、ニコニコ動画のコメントJSON（`public.nvcomment` のスレッドAPIレスポンス形式）を重ねて再生するデスクトップアプリです。動画はネットに接続せず、手元のファイルだけで再生します。

## セットアップ

```powershell
python -m pip install -r requirements.txt   # PySide6
```

## 使い方

```powershell
python niconico_player.py                     # 画面からファイルを選択
python niconico_player.py sm32738447.mp4      # 同じ名前の .json を自動で読み込み
python niconico_player.py sm32738447.mp4 sm32738447.json
```

フォルダへ動画とJSONをまとめてドラッグ&ドロップしても開けます。動画を開いたとき、同じ場所に `<動画名>.json` があれば自動で読み込みます。

### 対応しているJSON

| 形式 | 特徴 |
| --- | --- |
| nvapi スレッドAPI形式 | `data.threads[].comments[]`。`vposMs`（ミリ秒）と `commands` を解釈。`fork` が `owner` の投稿者コメントも表示可 |
| 配列/オブジェクト形式 | `[{"vpos": 1.5, "body": "test", "commands": ["ue"]}]` などのシンプルな形。`vpos` は秒 |

時刻のキーは `vposMs` / `vpos` / `time` / `start` に対応しています。投稿者コメントの `@vpos` にも対応しています。

### コマンドの対応状況

| コマンド | 対応 |
| --- | --- |
| `ue` / `shita` / `naka` | ○ |
| `big` / `medium` / `small` | ○ |
| 色名 / `#RRGGBB` | ○ |
| `ender` / `patissier`（回転） | ○ |
| `@vpos` | ○ |
| `invisible` | 非表示として扱う |
| `full` / `device:*` / `184` / 絵文字 `@emoji` | 未対応（無視） |

### 操作

| キー | 動作 |
| --- | --- |
| Space | 再生 / 一時停止 |
| ← / → | 5秒シーク（Shift併用で1秒） |
| ↑ / ↓ | 音量 |
| M | 消音の切り替え |
| F11 / Esc | 全画面 / 解除（全画面中は操作バーを隠します） |
| Ctrl+O / Ctrl+J | 動画 / JSONを開く |
| Ctrl+M | コメント表示の切り替え |
| Ctrl+R | 音声デバイスの再読み込み（Bluetooth 接続後など） |

画面下の「速度」で 0.5x〜2x の再生速度を選べます。100% 以外だと音程が変わります。

### 音声について

- 画面下の「音量」スライダー、消音ボタン（スピーカーアイコン）、出力先の選択（ドロップダウン）で調整できます。設定は作業中の画面だけで、保存はしません。
- Windows では既定の音声デバイスが「ヘッドホン (接続されませんでした)」など未接続のものになっていることがあります。その場合は無音になるため、実際に接続されているデバイス（`スピーカー (Realtek(R) Audio)` など）を自動で選びます。ヘッドホンの抜き差しにも追従します。
- **Bluetooth イヤホン/ヘッドホンが出てこないとき**: Windows の Bluetooth がオフ・未接続・ペアリング解除だと、Windows の音声一覧にも出ません。一度 Bluetooth 設定（Win+A → Bluetooth とデバイス）で「接続済み」にしてから、一覧の隣の **⟳**（音声デバイスを再読み込み、Ctrl+R）を押してください。接続済みの機種名（`WH-1000XM4` など）が一覧に出れば選べます。
- 再読み込みしても出ない場合は Windows 側の問題です。`設定 → サウンド` にそのイヤホンが出るかを確認してください。出なければデバイスマネージャーの Bluetooth を確認するか、Bluetooth アダプタの再接続（USB 抜き差し・PC 再起動）が必要です。
- 出力先が「ヘッドホン (接続されませんでした)」のままにしていると、音は出ません（無効化された Bluetooth デバイスを示す状態です）。
- 音量スライダーが 0 のときは、消音アイコンになります。

## 構成

```
niconico_player.py            起動スクリプト
niconico_comments/
    commands.py               コマンド解釈（色・サイズ・位置・回転）
    comment_parser.py         JSON読み込み → Comment 一覧
    layout.py                 レーン割り当てとスクロール位置の計算（GUI非依存）
    video_view.py             QVideoSink のフレーム表示（アスペクト比維持）
    overlay.py                動画に重ねるコメント描画
    main_window.py            メインウィンドウと操作パネル
tools/inspect_json.py         JSONの時刻分布を確認する補助スクリプト
```

## 制限とメモ

- 動画コーデックは Qt（Windows は Media Foundation）が対応する形式のみです。MP4(H.264/AAC) が無難です。再生不可の場合は画面に警告が出ます。
- コメントは 1 レーンあたり 3 件まで重ね、それ以上は間引いて読めるようにしています（本家より控えめ）。
- 下段・中段の初期位置はニコ動本家（下段 = 動画高さの 4/5、中段 = その中央）に合わせています。
- コメントJSONの `commentCount` より `comments` が少ない場合（APIのページ分けが必要な場合）はステータスバーに警告を出します。

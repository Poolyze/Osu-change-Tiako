Osu3tja
=======

把 osu! 譜面（`.osz`）轉成 TJA（太鼓模擬器通用格式）。

本版本修正了原專案的多個 bug，並新增圖形介面：在瀏覽器裡一步步操作，不用打指令。
**所有程式都在這個資料夾裡，不需要 `pip install` 任何東西**，只需要電腦裝有 Python 3（3.8 以上）。

## 開始使用

1. 在 GitHub 頁面按 **Code → Download ZIP**，解壓縮。
2. 啟動：
   - **Windows**：雙擊 `run.bat`
   - **macOS / Linux**：在終端機執行 `./run.sh`
3. 瀏覽器會自動打開操作頁面（網址通常是 `http://localhost:8765`），照畫面上的三個步驟做：
   選 `.osz` → 選難度對應 → 選儲存位置 → 開始轉換。
   - **下載 ZIP**：解壓縮後，把整個資料夾放進模擬器的歌曲資料夾。
   - **直接存進資料夾**：選「瀏覽…」或貼上路徑（例如 TJAPlayer3 的 `Songs` 資料夾），轉好的譜面就直接放進去，
     到模擬器重新掃描歌曲即可。程式會記住上次用的資料夾；同名資料夾已存在時會加編號，不會覆蓋。
4. 用完後關閉黑色的執行視窗即可。

> 還沒有 Python？到 <https://www.python.org/downloads/> 安裝，Windows 安裝時請勾選 **Add python.exe to PATH**。

所有檔案都只在你自己的電腦上處理，不會傳到網路上。暫存檔會在程式結束時自動刪除。

## 命令列（進階）

```
python main.py <來源.osz> <輸出資料夾>                  # 互動式選難度
python main.py song.osz out --oni 0 --hard 1            # 直接指定
python main.py song.osz out --oni 0 --oni-level 9       # 自訂 Oni 的星數（1~10）
python main.py song.osz out --oni 0 --encoding shift_jis
```

星數（TJA 的 `LEVEL`）沒有指定時，預設用 osu! 譜面的 OverallDifficulty 取整數（限制在 1~10）。
網頁版每個難度右邊的欄位就是自訂星數，留空即使用預設值。

輸出為 `<輸出資料夾>/<標題>/<標題>.tja` 與音檔。TJA 預設用 UTF-8（含 BOM）、CRLF 換行；
若舊版播放器出現亂碼可改用 Shift-JIS（網頁的「進階設定」或 `--encoding shift_jis`，但不支援中文字）。

圖形介面的選項：`python app.py --port 9000`、`--no-browser`。
`--host 0.0.0.0` 可讓同一網路的其他裝置連入，但**沒有任何登入保護**，請只在信任的網路使用；
此模式下「直接存進資料夾」會被停用（只能下載 ZIP），避免外部連線寫入你電腦上的任意位置。

## 專案結構

- `app.py`、`static/index.html`：圖形介面（只用 Python 內建函式庫）
- `converter.py`：非互動式核心 API（圖形介面與命令列共用）
- `main.py`：命令列
- `osu2tja/osu2tja.py`：osu → tja 轉換核心
- `tja2osu/`：反向轉換（原專案遺留，仍是 Python 2 程式碼，**尚未移植，無法在 Python 3 執行**）

## 本版本修正的主要問題

- 遇到「不完整小節」（變速、拍號變化較多的長歌）時 `NameError` 崩潰；
  且自訂拍號寫成 `#MEASURE 373/24`（單位錯誤，等於 62 拍），導致後段譜面錯位。
- 超長歌曲（數小時）的時間漂移：每個不完整小節的 `#DELAY` 以前用 `int()` 捨去毫秒、且丟掉不到 1ms 的零頭，
  變速點多的歌會一路累積；現在改為精確計算並把零頭累積到下一次延遲。
- 綠線（只改捲動速度）上的拍號被誤當成換拍，導致 `BAR END POS ERROR`；現在綠線沿用前一條紅線的拍號。
- 同一毫秒疊了大量音符的譜面（常見於特殊／娛樂譜）會 `ZeroDivisionError`；現在只保留一個並在 TJA 註解標示 `[Warning]`。
- `#MEASURE` 指令缺空格、整數除法變浮點數、`time_per_beat` 未初始化、重疊音符使輸出崩潰。
- 輸出的 TJA 未指定編碼，在 Windows 上容易亂碼；標題含冒號會被截斷；標題含非法字元會建立資料夾失敗。
- `main.py` 的無窮迴圈風險、遺留的除錯輸出、資料夾已存在時崩潰、壓縮檔路徑未驗證。

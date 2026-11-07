# -*- coding: utf-8 -*-
"""Osu3tja 的非互動式核心 API（給命令列與網頁前端共用）。

osu2tja.osu2tja 內部大量使用模組層級的全域變數，因此「同一個行程內」不能
同時轉換兩份譜面。網頁版請用 convert_isolated()，它會為每次轉換開一個
獨立的子行程，既能多人同時使用，也能在逾時時強制結束。
"""
import io
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Dict, List, Optional
from zipfile import BadZipFile, ZipFile, is_zipfile

from osu2tja.osu2tja import osu2tja, reset_global_variables

COURSES = ("Oni", "Hard", "Normal", "Easy")

# 防止惡意或異常的壓縮檔（zip bomb）把共用電腦的記憶體或硬碟塞爆
MAX_OSU_BYTES = 20 * 1024 * 1024
MAX_AUDIO_BYTES = 300 * 1024 * 1024
MAX_CHARTS = 60

DEFAULT_ENCODING = "utf-8-sig"


class ConvertError(Exception):
    """可以直接顯示給使用者看的錯誤。"""


# ---------------------------------------------------------------- helpers
_ILLEGAL = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def sanitize_filename(name: str, fallback: str = "chart") -> str:
    """把標題變成各作業系統都能用的檔名／資料夾名。"""
    name = _ILLEGAL.sub("_", name or "").strip().strip(".")
    name = name[:100].rstrip(" .")
    return name or fallback


MIN_LEVEL, MAX_LEVEL = 1, 10  # TJA 的星數範圍


def default_level(difficulty: float) -> int:
    """沒有自訂時的預設星數：osu! 的 OverallDifficulty 取整數，限制在 1~10。"""
    return max(MIN_LEVEL, min(MAX_LEVEL, int(difficulty)))


def _split_var(line: str):
    """'Key: value' -> ('Key', 'value')，只切第一個冒號（標題內可以有冒號）。"""
    if ":" not in line:
        return "", ""
    key, value = line.split(":", 1)
    return key.strip(), value.strip()


def parse_osu_header(text: str) -> Dict[str, object]:
    """只解析 .osu 的 General / Metadata / Difficulty，並數出物件數量。"""
    info: Dict[str, object] = {
        "version": "", "difficulty": 0.0, "mode": 0, "audio": "",
        "title": "", "artist": "", "objects": 0,
    }
    section = ""
    in_objects = False
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            in_objects = section == "HitObjects"
            continue
        if in_objects:
            info["objects"] += 1
            continue
        if line.startswith("//"):
            continue
        key, value = _split_var(line)
        if section == "General":
            if key == "AudioFilename":
                info["audio"] = value
            elif key == "Mode":
                info["mode"] = int(value) if value.isdigit() else 0
        elif section == "Metadata":
            if key in ("Title", "TitleUnicode"):
                info["title"] = value or info["title"]
            elif key in ("Artist", "ArtistUnicode"):
                info["artist"] = value or info["artist"]
            elif key == "Version":
                info["version"] = value
        elif section == "Difficulty":
            if key == "OverallDifficulty":
                try:
                    info["difficulty"] = float(value)
                except ValueError:
                    pass
    return info


def _open_osz(path: str) -> ZipFile:
    if not is_zipfile(path):
        raise ConvertError("這不是有效的 .osz 檔（它不是 zip 格式）。")
    try:
        return ZipFile(path, "r")
    except BadZipFile:
        raise ConvertError("這個 .osz 檔已損毀，無法開啟。")


def _read_osu_text(z: ZipFile, member: str) -> str:
    size = z.getinfo(member).file_size
    if size > MAX_OSU_BYTES:
        raise ConvertError("譜面檔 %s 過大（超過 %d MB）。" %
                           (member, MAX_OSU_BYTES // 1024 // 1024))
    return z.read(member).decode("utf-8-sig", errors="replace")


def _find_member(z: ZipFile, name: str) -> Optional[str]:
    """用不分大小寫、容許 / 與 \\ 的方式找壓縮檔內的檔案。"""
    want = name.replace("\\", "/").lower()
    for member in z.namelist():
        if member.replace("\\", "/").lower() == want:
            return member
    return None


# ------------------------------------------------------------------- API
def list_difficulties(osz_path: str) -> Dict[str, object]:
    """列出 .osz 內所有難度，依整體難度由高到低排序（index 即選擇用的編號）。"""
    with _open_osz(osz_path) as z:
        members = [n for n in z.namelist() if n.lower().endswith(".osu")]
        if not members:
            raise ConvertError("這個 .osz 裡面找不到任何譜面（.osu）。")
        if len(members) > MAX_CHARTS:
            raise ConvertError("壓縮檔內的譜面數量異常（超過 %d 個）。" % MAX_CHARTS)
        charts: List[Dict[str, object]] = []
        for member in members:
            info = parse_osu_header(_read_osu_text(z, member))
            info["filename"] = member
            charts.append(info)

    charts.sort(key=lambda c: c["difficulty"], reverse=True)
    for i, chart in enumerate(charts):
        chart["index"] = i
        chart["default_level"] = default_level(chart["difficulty"])
    first = charts[0]
    return {
        "title": first["title"] or "untitled",
        "artist": first["artist"],
        "audio": first["audio"],
        "charts": charts,
    }


def convert_osz(osz_path: str, out_dir: str, selection: Dict[str, int],
                encoding: str = DEFAULT_ENCODING,
                levels: Optional[Dict[str, int]] = None) -> Dict[str, str]:
    """把 .osz 轉成 TJA。

    selection: {"Oni": 0, "Hard": 2, ...}，值是 list_difficulties() 的 index，
               沒選的難度不要放進來（或填 -1）。
    levels:    {"Oni": 9, ...} 自訂星數（1~10）；沒填的難度用預設值。
    回傳 {"folder", "tja", "audio"}，資料夾會建立在 out_dir 底下。
    """
    selection = {c: i for c, i in selection.items() if i is not None and i >= 0}
    unknown = set(selection) - set(COURSES)
    if unknown:
        raise ConvertError("不認得的難度名稱：%s" % ", ".join(sorted(unknown)))
    if not selection:
        raise ConvertError("至少要選一個難度。")

    levels = {c: v for c, v in (levels or {}).items() if v is not None}
    for course, value in levels.items():
        if course not in COURSES:
            raise ConvertError("不認得的難度名稱：%s" % course)
        if (not isinstance(value, int) or isinstance(value, bool)
                or not MIN_LEVEL <= value <= MAX_LEVEL):
            raise ConvertError("%s 的星數必須是 %d 到 %d 的整數。"
                               % (course, MIN_LEVEL, MAX_LEVEL))

    meta = list_difficulties(osz_path)
    charts = meta["charts"]
    for course, idx in selection.items():
        if idx >= len(charts):
            raise ConvertError("%s 選到不存在的難度編號 %d。" % (course, idx))

    title = sanitize_filename(meta["title"])
    audio_name = meta["audio"]
    # 以第一個被選中的難度所指定的音檔為準
    ordered = [c for c in COURSES if c in selection]
    audio_name = charts[selection[ordered[0]]]["audio"] or audio_name
    new_audio_name = title + os.path.splitext(audio_name)[1].lower()

    head: List[str] = []
    bodies: List[List[str]] = []
    with _open_osz(osz_path) as z:
        for course in ordered:
            chart = charts[selection[course]]
            level = levels.get(course) or default_level(chart["difficulty"])
            text = _read_osu_text(z, chart["filename"])
            reset_global_variables()
            try:
                h, body = osu2tja(io.StringIO(text), course, level,
                                  new_audio_name)
            except ConvertError:
                raise
            except Exception as e:  # 轉換核心的任何內部錯誤
                raise ConvertError(
                    "難度「%s」轉換失敗（%s: %s）。這個譜面可能有目前不支援的"
                    "特殊結構。" % (chart["version"], type(e).__name__, e))
            head = head or h
            bodies.append(body)

        # 建立輸出資料夾（若已存在就加編號，不覆蓋別人的檔案）
        folder = os.path.join(out_dir, title)
        n = 1
        while os.path.exists(folder):
            n += 1
            folder = os.path.join(out_dir, "%s (%d)" % (title, n))
        os.makedirs(folder)

        # 抽出音檔（直接串流寫入指定檔名，不信任壓縮檔內的路徑）
        member = _find_member(z, audio_name)
        if member is None:
            raise ConvertError("壓縮檔裡找不到音檔 %s。" % audio_name)
        if z.getinfo(member).file_size > MAX_AUDIO_BYTES:
            raise ConvertError("音檔過大（超過 %d MB）。" %
                               (MAX_AUDIO_BYTES // 1024 // 1024))
        audio_path = os.path.join(folder, new_audio_name)
        with z.open(member) as src, open(audio_path, "wb") as dst:
            shutil.copyfileobj(src, dst)

    lines = list(head)
    for body in bodies:
        lines.extend(body)
    tja_path = os.path.join(folder, title + ".tja")
    try:
        with open(tja_path, "w", encoding=encoding, newline="\r\n") as f:
            f.write("\n".join(lines))
            f.write("\n")
    except (LookupError, UnicodeEncodeError) as e:
        raise ConvertError("無法用 %s 編碼寫入（標題含有該編碼不支援的字元）：%s"
                           % (encoding, e))
    return {"folder": folder, "tja": tja_path, "audio": audio_path}


# ------------------------------------------------------- process isolation
def convert_isolated(osz_path: str, out_dir: str, selection: Dict[str, int],
                     encoding: str = DEFAULT_ENCODING,
                     timeout: float = 180,
                     levels: Optional[Dict[str, int]] = None) -> Dict[str, str]:
    """在獨立子行程中轉換：全域變數互不干擾，逾時會被強制結束。"""
    cmd = [sys.executable, os.path.abspath(__file__), "--worker",
           osz_path, out_dir, json.dumps(selection), encoding,
           json.dumps(levels or {})]
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout,
                              cwd=os.path.dirname(os.path.abspath(__file__)))
    except subprocess.TimeoutExpired:
        raise ConvertError("轉換超過 %d 秒仍未完成，已中止。" % timeout)
    out = proc.stdout.decode("utf-8", errors="replace").strip().splitlines()
    try:
        result = json.loads(out[-1]) if out else {}
    except ValueError:
        result = {}
    if proc.returncode == 0 and "tja" in result:
        return result
    raise ConvertError(result.get("error") or "轉換程序異常結束。")


def _worker_main(argv: List[str]) -> int:
    osz_path, out_dir, selection_json, encoding, levels_json = argv
    try:
        result = convert_osz(osz_path, out_dir, json.loads(selection_json),
                             encoding, json.loads(levels_json))
        print(json.dumps(result))
        return 0
    except ConvertError as e:
        print(json.dumps({"error": str(e)}))
    except Exception as e:
        print(json.dumps({"error": "內部錯誤：%s: %s" % (type(e).__name__, e)}))
    return 1


if __name__ == "__main__":
    if len(sys.argv) == 7 and sys.argv[1] == "--worker":
        sys.exit(_worker_main(sys.argv[2:]))
    sys.exit("這個檔案是函式庫，請改用 main.py（命令列）或 app.py（網頁）。")

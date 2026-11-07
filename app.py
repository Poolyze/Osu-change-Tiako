# -*- coding: utf-8 -*-
"""Osu3tja 圖形介面（在你自己的電腦上執行，用瀏覽器操作）。

只用 Python 內建的函式庫，不需要 pip install 任何東西。

    python app.py                 # 啟動並自動開啟瀏覽器
    python app.py --no-browser    # 不自動開瀏覽器
    python app.py --port 9000
    python app.py --host 0.0.0.0  # 讓同一網路的其他裝置也能連（無登入保護，請小心）
"""
import argparse
import atexit
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import uuid
import webbrowser
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import quote, unquote, urlsplit

from converter import (COURSES, ConvertError, convert_isolated,
                       list_difficulties, sanitize_filename)

HERE = os.path.dirname(os.path.abspath(__file__))
MAX_UPLOAD_MB = int(os.environ.get("OSU3TJA_MAX_UPLOAD_MB", "500"))
MAX_JOBS = int(os.environ.get("OSU3TJA_MAX_JOBS", "2"))
CONVERT_TIMEOUT = 180
QUEUE_WAIT = 120
ALLOWED_ENCODINGS = {"utf-8-sig", "utf-8", "shift_jis"}
TOKEN_RE = re.compile(r"^[0-9a-f]{32}$")

def sweep_old_temp(max_age_hours=24):
    """直接按 X 關閉視窗時不會執行結束清理，所以啟動時順手刪掉舊的暫存資料夾。"""
    import time
    base = tempfile.gettempdir()
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            if (name.startswith("osu3tja_") and os.path.isdir(path)
                    and time.time() - os.path.getmtime(path) > max_age_hours * 3600):
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


sweep_old_temp()
# 每次啟動各用一個全新的暫存資料夾，結束時整個刪掉
WORK_ROOT = tempfile.mkdtemp(prefix="osu3tja_")
atexit.register(shutil.rmtree, WORK_ROOT, True)
slots = threading.BoundedSemaphore(MAX_JOBS)

# 記住上次選的輸出資料夾
CONFIG_PATH = os.path.join(os.path.expanduser("~"), ".osu3tja.json")


def load_settings():
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**kwargs):
    data = load_settings()
    data.update(kwargs)
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass  # 存不了設定不影響轉換


# 用獨立的子行程開資料夾選擇視窗（tkinter 是內建的，但某些 Linux 沒裝）
BROWSE_CODE = (
    "import sys\n"
    "import tkinter as tk\n"
    "from tkinter import filedialog\n"
    "r = tk.Tk(); r.withdraw(); r.attributes('-topmost', True)\n"
    "p = filedialog.askdirectory(title='選擇輸出資料夾（例如太鼓模擬器的歌曲資料夾）',"
    " initialdir=sys.argv[1] or None)\n"
    "sys.stdout.buffer.write((p or '').encode('utf-8'))\n"
)


def suggest_selection(charts):
    """預設把難度由高到低依序對到 Oni/Hard/Normal/Easy；優先選太鼓模式 (mode 1) 的譜面。"""
    pool = [c for c in charts if c.get("mode") == 1] or charts
    return {c: (pool[i]["index"] if i < len(pool) else -1)
            for i, c in enumerate(COURSES)}


def job_dir(token):
    if not TOKEN_RE.match(token or ""):
        raise ConvertError("無效的工作編號。")
    return os.path.join(WORK_ROOT, token)


class Handler(BaseHTTPRequestHandler):
    server_version = "Osu3tja"
    protocol_version = "HTTP/1.1"
    local_only = True  # 只有綁在本機時才允許直接寫入資料夾

    def log_message(self, fmt, *args):  # 不要把每個請求都印在黑色視窗
        pass

    # ---------------------------------------------------------- responses
    def send_json(self, obj, status=200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def fail(self, message, status=400):
        self.send_json({"error": message}, status)

    def send_file(self, path, content_type, download_name=None):
        size = os.path.getsize(path)
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(size))
        self.send_header("Cache-Control", "no-store")
        if download_name:
            ascii_name = re.sub(r"[^\x20-\x7e]", "_", download_name).replace('"', "_")
            self.send_header(
                "Content-Disposition",
                "attachment; filename=\"%s\"; filename*=UTF-8''%s"
                % (ascii_name, quote(download_name)))
        self.end_headers()
        with open(path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    # ---------------------------------------------------------------- GET
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/":
            self.send_file(os.path.join(HERE, "static", "index.html"),
                           "text/html; charset=utf-8")
        elif path == "/api/config":
            self.send_json({
                "max_upload_mb": MAX_UPLOAD_MB,
                "direct_save": self.local_only,
                "output_dir": load_settings().get("output_dir", ""),
            })
        elif path.startswith("/api/download/"):
            self.handle_download(path.rsplit("/", 1)[1])
        else:
            self.fail("找不到頁面。", 404)

    def handle_download(self, token):
        try:
            folder = job_dir(token)
        except ConvertError as e:
            return self.fail(str(e))
        zip_path = os.path.join(folder, "result.zip")
        out_dir = os.path.join(folder, "out")
        if not os.path.isfile(zip_path):
            return self.fail("檔案不存在，請重新轉換。", 410)
        names = os.listdir(out_dir) if os.path.isdir(out_dir) else []
        name = (names[0] if names else "Osu3tja") + ".zip"
        self.send_file(zip_path, "application/zip", name)

    # --------------------------------------------------------------- POST
    def do_POST(self):
        path = urlsplit(self.path).path
        try:
            if path == "/api/inspect":
                self.handle_inspect()
            elif path == "/api/convert":
                self.handle_convert()
            elif path == "/api/browse":
                self.handle_browse()
            else:
                self.fail("找不到頁面。", 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            import traceback
            traceback.print_exc()
            try:
                self.fail("伺服器發生未預期的錯誤。", 500)
            except Exception:
                pass

    def handle_inspect(self):
        """檔案以原始位元組直接放在 body（不用 multipart），檔名在 X-Filename。"""
        name = unquote(self.headers.get("X-Filename", ""))
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self.fail("缺少檔案內容。", 411)
        if not name.lower().endswith(".osz"):
            self.close_connection = True
            return self.fail("請選擇 .osz 檔案。")
        if length <= 0 or length > MAX_UPLOAD_MB * 1024 * 1024:
            self.close_connection = True
            return self.fail("檔案太大了，上限是 %d MB。" % MAX_UPLOAD_MB, 413)

        token = uuid.uuid4().hex
        folder = job_dir(token)
        os.makedirs(folder)
        osz = os.path.join(folder, "in.osz")
        try:
            remaining = length
            with open(osz, "wb") as f:
                while remaining > 0:
                    chunk = self.rfile.read(min(1 << 20, remaining))
                    if not chunk:
                        raise ConvertError("上傳中斷，請再試一次。")
                    f.write(chunk)
                    remaining -= len(chunk)
            meta = list_difficulties(osz)
        except ConvertError as e:
            shutil.rmtree(folder, ignore_errors=True)
            return self.fail(str(e))
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

        charts = [{k: c[k] for k in ("index", "version", "difficulty", "mode",
                                     "objects", "default_level")}
                  for c in meta["charts"]]
        self.send_json({
            "token": token, "title": meta["title"], "artist": meta["artist"],
            "charts": charts, "suggested": suggest_selection(charts),
        })

    def handle_browse(self):
        if not self.local_only:
            return self.fail("非本機連線不能使用這個功能。", 403)
        initial = load_settings().get("output_dir", "")
        try:
            proc = subprocess.run([sys.executable, "-c", BROWSE_CODE, initial],
                                  capture_output=True, timeout=300)
        except subprocess.TimeoutExpired:
            return self.send_json({"path": ""})
        if proc.returncode != 0:
            return self.fail("這台電腦無法開啟選擇視窗，請直接貼上資料夾路徑。", 501)
        self.send_json({"path": proc.stdout.decode("utf-8", "replace").strip()})

    def handle_convert(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(min(length, 65536)) or b"{}")
            folder = job_dir(body.get("token", ""))
        except (ValueError, ConvertError) as e:
            return self.fail(str(e) if isinstance(e, ConvertError) else "請求格式不正確。")
        osz = os.path.join(folder, "in.osz")
        if not os.path.isfile(osz):
            return self.fail("這個工作已經過期，請重新選擇檔案。", 410)

        selection = {}
        for course, idx in (body.get("selection") or {}).items():
            if course not in COURSES or not isinstance(idx, int) or isinstance(idx, bool):
                return self.fail("選擇的難度格式不正確。")
            selection[course] = idx
        encoding = body.get("encoding", "utf-8-sig")
        if encoding not in ALLOWED_ENCODINGS:
            return self.fail("不支援的編碼。")
        levels = body.get("levels") or {}
        if not isinstance(levels, dict):
            return self.fail("星數格式不正確。")

        direct = None
        if body.get("output_dir"):
            if not self.local_only:
                return self.fail("為了安全，非本機連線不能直接寫入資料夾。", 403)
            if not isinstance(body["output_dir"], str):
                return self.fail("資料夾路徑格式不正確。")
            direct = os.path.abspath(os.path.expanduser(body["output_dir"].strip()))
            if not os.path.isdir(direct):
                return self.fail("找不到這個資料夾：%s" % direct)
            if not os.access(direct, os.W_OK):
                return self.fail("這個資料夾沒有寫入權限：%s" % direct)

        if not slots.acquire(timeout=QUEUE_WAIT):
            return self.fail("目前轉換的人太多，請稍後再試。", 503)
        try:
            if direct:
                result = convert_isolated(osz, direct, selection, encoding,
                                          timeout=CONVERT_TIMEOUT, levels=levels)
                save_settings(output_dir=direct)
                with open(result["tja"], encoding=encoding, errors="replace") as f:
                    warnings = f.read().count("//[Warning]")
                return self.send_json({"saved_to": result["folder"],
                                       "warnings": warnings})
            out_dir = os.path.join(folder, "out")
            shutil.rmtree(out_dir, ignore_errors=True)
            zip_path = os.path.join(folder, "result.zip")
            if os.path.exists(zip_path):
                os.remove(zip_path)
            os.makedirs(out_dir)

            result = convert_isolated(osz, out_dir, selection, encoding,
                                      timeout=CONVERT_TIMEOUT, levels=levels)
            base = os.path.basename(result["folder"])
            with zipfile.ZipFile(zip_path, "w") as z:
                z.write(result["tja"], base + "/" + os.path.basename(result["tja"]),
                        compress_type=zipfile.ZIP_DEFLATED)
                # 音檔本來就是壓縮格式，不重複壓縮以節省時間
                z.write(result["audio"], base + "/" + os.path.basename(result["audio"]),
                        compress_type=zipfile.ZIP_STORED)
            with open(result["tja"], encoding=encoding, errors="replace") as f:
                warnings = f.read().count("//[Warning]")
        except ConvertError as e:
            return self.fail(str(e), 422)
        finally:
            slots.release()

        self.send_json({
            "download": "/api/download/" + os.path.basename(folder),
            "filename": sanitize_filename(base) + ".zip",
            "warnings": warnings,
        })


# ------------------------------------------------------------------- main
def pick_port(host, preferred):
    """preferred 被占用時往後找一個可用的 port。"""
    for port in range(preferred, preferred + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise SystemExit("找不到可用的連接埠，請用 --port 指定。")


def main():
    parser = argparse.ArgumentParser(description="Osu3tja 圖形介面")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    port = pick_port(args.host, args.port)
    Handler.local_only = args.host in ("127.0.0.1", "localhost", "::1")
    server = ThreadingHTTPServer((args.host, port), Handler)
    server.daemon_threads = True
    url = "http://%s:%d/" % ("localhost" if args.host in ("127.0.0.1", "0.0.0.0")
                              else args.host, port)
    print("=" * 52)
    print(" Osu3tja 已啟動")
    print(" 瀏覽器網址： " + url)
    print(" 用完後，直接關閉這個視窗（或按 Ctrl+C）即可結束。")
    print("=" * 52)
    if not args.no_browser:
        threading.Timer(0.6, webbrowser.open, [url]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已結束。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

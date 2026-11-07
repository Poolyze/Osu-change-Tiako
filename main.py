# -*- coding: utf-8 -*-
"""命令列介面。

互動式（與原版相同）:
    python main.py <來源.osz> <輸出資料夾>

非互動式（直接指定每個難度要用第幾個譜面，-1 或不指定代表不輸出）:
    python main.py song.osz out --oni 0 --hard 1
    python main.py song.osz out --oni 0 --oni-level 9      # 自訂星數
"""
import argparse
import sys

from converter import (COURSES, DEFAULT_ENCODING, ConvertError, convert_osz,
                       list_difficulties)


def ask_index(course: str, count: int) -> int:
    while True:
        raw = input("%s:" % course).strip()
        if raw == "":
            return -1
        try:
            value = int(raw)
        except ValueError:
            print("請輸入數字（不輸出請輸入 -1）。")
            continue
        if -1 <= value < count:
            return value
        print("編號必須介於 -1 到 %d。" % (count - 1))


def main() -> int:
    parser = argparse.ArgumentParser(description="osu! (.osz) -> TJA 轉換器")
    parser.add_argument("source", help="來源 .osz 檔")
    parser.add_argument("output", help="輸出資料夾")
    for course in COURSES:
        parser.add_argument("--" + course.lower(), type=int, default=None,
                            metavar="N", help="%s 對應的譜面編號" % course)
    for course in COURSES:
        parser.add_argument("--%s-level" % course.lower(), type=int,
                            default=None, metavar="N",
                            help="%s 的星數（1~10），不指定則用預設" % course)
    parser.add_argument("--encoding", default=DEFAULT_ENCODING,
                        help="TJA 檔案編碼，預設 %s（部分舊版播放器需要 "
                             "shift_jis）" % DEFAULT_ENCODING)
    args = parser.parse_args()

    try:
        meta = list_difficulties(args.source)
        charts = meta["charts"]
        print("====== Difficulties Selection ======")
        print("Index\tDifficulty\tObjects\tVersion")
        for c in charts:
            print("(%d):\t%s\t%d\t%s" % (c["index"], c["difficulty"],
                                         c["objects"], c["version"]))
        print()

        given = {c: getattr(args, c.lower()) for c in COURSES}
        if all(v is None for v in given.values()):
            print("Please select difficulties(-1 if not available):")
            given = {c: ask_index(c, len(charts)) for c in COURSES}

        levels = {c: getattr(args, c.lower() + "_level") for c in COURSES}
        result = convert_osz(args.source, args.output, given, args.encoding,
                             levels)
    except ConvertError as e:
        print("錯誤：%s" % e, file=sys.stderr)
        return 1

    print("Extract Audio Done!")
    print("Tja converted! ->", result["tja"])
    return 0


if __name__ == "__main__":
    sys.exit(main())

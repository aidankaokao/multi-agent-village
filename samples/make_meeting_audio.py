"""產生測試聽打員用的示範會議錄音（macOS 內建台灣中文語音，三個人輪流說話；需要 say 與 ffmpeg）。

內容和文件彙整師的範例（extra-agents/report-maker/samples：連鎖咖啡門市營運）互相關聯。
用法：uv run python samples/make_meeting_audio.py → samples/門市營運週會.mp3（約 2 分鐘）
"""

import subprocess
import tempfile
from pathlib import Path

OUT = Path(__file__).parent / "門市營運週會.mp3"
MANAGER, KAOHSIUNG, XINYI = "Meijia", "Eddy (中文（台灣）)", "Reed (中文（台灣）)"

LINES = [
    (MANAGER, "好，我們開始這週的門市營運週會。今天主要看三件事：高雄巨蛋店的客訴、台北信義店的人事成本，還有下個月的新品上市。"),
    (MANAGER, "先請高雄巨蛋店說明，上個月客訴有七件，超過五件的標準。"),
    (KAOHSIUNG, "是的。七件裡面有五件是尖峰時段等太久，主要是週末下午，排隊超過二十分鐘。另外兩件是外送的飲料灑出來。"),
    (KAOHSIUNG, "我們打算週末下午多排一位兼職，然後外送改用新的杯蓋，預計下個月中可以看到改善。"),
    (MANAGER, "多排一位兼職，人事成本率會不會超過百分之二十五？"),
    (KAOHSIUNG, "我算過，大概會從百分之二十二點五升到二十四，還在目標以內。"),
    (MANAGER, "好，那就先試一個月，月底回報客訴件數和 NPS 的變化。"),
    (MANAGER, "接下來是台北信義店，人事成本率連續兩個月超過百分之二十七。"),
    (XINYI, "主要是加班費。有兩位正職請長假，我們用加班撐過去。下週會有一位新同事報到，加班應該會減少一半。"),
    (MANAGER, "了解。也請你看一下排班，平日上午來客比較少，可以考慮少排一個人。"),
    (XINYI, "好，我週五前把新的排班表寄給你。"),
    (MANAGER, "最後是新品。桂花烏龍拿鐵預計十一月十五號上市，各店十一月十號前要完成員工試做。"),
    (MANAGER, "總結一下待辦：高雄巨蛋店週末加一位兼職、換新杯蓋，月底回報；信義店週五前交排班表；各店十一月十號前完成新品試做。"),
    (MANAGER, "今天就到這裡，謝謝大家。"),
]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        parts: list[Path] = []
        for i, (voice, text) in enumerate(LINES):
            aiff = Path(tmp) / f"{i:02d}.aiff"
            subprocess.run(["say", "-v", voice, "-r", "185", "-o", str(aiff), text], check=True)
            parts.append(aiff)
        listing = Path(tmp) / "list.txt"
        silence = Path(tmp) / "silence.aiff"
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "0.6", str(silence)], check=True)
        listing.write_text("".join(f"file '{p}'\nfile '{silence}'\n" for p in parts))
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
                        "-ac", "1", "-ar", "22050", "-c:a", "libmp3lame", "-b:a", "48k", str(OUT)], check=True)
    print(OUT)


if __name__ == "__main__":
    main()

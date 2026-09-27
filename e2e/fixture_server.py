from __future__ import annotations

import signal
import subprocess
import tempfile
import time
from pathlib import Path

import uvicorn
from PIL import Image

import engine.main as engine_main
from engine.main import Settings, create_app


def photo(path: Path, captured: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    exif[36867] = captured
    Image.new("RGB", (32, 32), "#7ca88a").save(path, exif=exif)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="llm-photo-e2e-") as directory:
        root = Path(directory)
        photo(root / "mixed" / "DJI_20000410100000_0001_D.JPG", "2000:04:10 10:00:00")
        photo(root / "mixed" / "nested" / "DJI_20000410110000_0002_D.JPG", "2000:04:10 11:00:00")
        photo(root / "mixed" / "DJI_20000411090000_0003_D.JPG", "2000:04:11 09:00:00")
        photo(root / "mixed" / "DJI_20000411100000_0005_D.TIFF", "2000:04:11 10:00:00")
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=#547a97:s=64x64:r=24",
            "-t", "2", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
            str(root / "mixed" / "DJI_20000412103000_0004_D.MP4"),
        ], check=True)
        photo(root / "outside" / "private.jpg", "2026:04:11 12:00:00")
        cancel_folder = root / "cancel"
        cancel_folder.mkdir()
        cancel_folder = cancel_folder.resolve()
        sample = (root / "mixed" / "DJI_20000410100000_0001_D.JPG").read_bytes()
        for index in range(250):
            (cancel_folder / f"photo-{index:04}.jpg").write_bytes(sample)

        scan_folder = engine_main.scan_folder

        def slow_scan(folder: Path, progress=None, year_correction=None, should_stop=None):
            if folder != cancel_folder:
                return scan_folder(folder, progress, year_correction, should_stop)

            def pause() -> bool:
                time.sleep(0.02)
                return should_stop() if should_stop else False

            return scan_folder(folder, progress, year_correction, pause)

        engine_main.scan_folder = slow_scan
        app = create_app(Settings(
            photo_root=root,
            home_settings=root / ".home.json",
            bind="127.0.0.1",
            port=8042,
            date_year_correction=("mixed", 2000, 2026),
            ui_origin="http://127.0.0.1:3002",
        ))

        def stop(_signum: int, _frame: object) -> None:
            raise SystemExit(0)

        signal.signal(signal.SIGTERM, stop)
        uvicorn.run(app, host="127.0.0.1", port=8042, log_level="warning")


if __name__ == "__main__":
    main()

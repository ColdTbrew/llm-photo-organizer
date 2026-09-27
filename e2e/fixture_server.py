from __future__ import annotations

import signal
import tempfile
from pathlib import Path

import uvicorn
from PIL import Image

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
        photo(root / "outside" / "private.jpg", "2026:04:11 12:00:00")
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

from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from PIL import Image, ImageOps
from pydantic import BaseModel

from engine.places import home_distance_km, nearby_place

ENGINE_DIR = Path(__file__).resolve().parent
FRAME_LIMIT = 32
DAY_SAMPLE_LIMIT = 6
MAX_IMAGE_BYTES = 25 * 1024 * 1024
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".tif", ".tiff"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
HIDDEN_FOLDERS = {"$recycle.bin", "system volume information", "lost+found"}
SUGGESTED_CATEGORIES = {"인물", "풍경", "여행", "음식", "동물", "문서", "차량", "행사", "일상", "기타"}
COUNTRY_NAMES = {"대한민국", "한국", "북한", "일본", "중국", "대만", "미국", "캐나다", "프랑스", "영국", "독일", "이탈리아", "스페인", "포르투갈", "네덜란드", "스위스", "오스트리아", "호주", "태국", "베트남", "인도", "인도네시아", "싱가포르", "말레이시아"}
EMPTY_PLACE_WORDS = {"빈문자열", "없음", "알수없음", "미상", "불명", "해당없음", "모름"}
LANDMARK_CITIES = {
    "eiffel tower": ("파리", "에펠탑"), "에펠탑": ("파리", "에펠탑"),
    "n서울타워": ("서울", "N서울타워"), "남산타워": ("서울", "남산타워"),
    "경복궁": ("서울", "경복궁"), "광안대교": ("부산", "광안대교"),
}
CLASSIFY_PROMPT = (
    "반드시 한국어로만 답하세요. JSON 키와 모든 문자열 값도 한국어로 쓰고, 중국어·영어 단어를 섞지 마세요. "
    "같은 날짜에 촬영된 대표 사진과 영상입니다. 날짜 전체에 적용할 카테고리 하나를 고르세요: "
    "인물, 풍경, 여행, 음식, 동물, 문서, 차량, 행사, 일상, 기타. "
    "고양이와 개를 포함한 동물은 동물, 사람만 인물입니다. "
    "집 밖에서 촬영했다는 이유만으로 여행으로 분류하지 마세요. "
    "여러 장면이 섞였으면 날짜의 주된 내용을 고르되, 불분명하면 기타를 고르세요. "
    "샘플 사진만 보고 그 날짜의 모든 파일에 같은 대상이나 장소가 있다고 단정하지 마세요. "
    "사진 내용과 위치를 함께 보고 여행이 분명할 때만 여행을 고르세요. "
    "제공된 GPS와 가까운 지명은 참고 정보이며 행정 경계를 확정하지 않습니다. "
    "GPS가 없어도 사진 속 읽을 수 있는 지명 표지나 고유한 랜드마크로 도시·군을 알아볼 수 있으면 그 도시·군 이름을 장소에 적으세요. "
    "국가 이름만 알면 장소에 null을 쓰세요. 장소와 근거는 한국어로만 쓰세요. "
    "흔한 바다, 산, 거리, 건물의 모습만으로 특정 지역을 추측하지 마세요. "
    "장소를 식별할 근거가 없으면 장소와 장소근거를 모두 null로 두세요. "
    "JSON만 답하세요. 장소와 장소근거는 둘 다 알 때만 한국어 문자열로, 아니면 둘 다 null로 쓰세요."
)
CLASSIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "대상": {"type": "string", "pattern": r"^[가-힣0-9 .,·()'-]{0,80}$"},
        "카테고리": {"type": "string", "enum": ["인물", "풍경", "여행", "음식", "동물", "문서", "차량", "행사", "일상", "기타"]},
        "장소": {"type": ["string", "null"], "pattern": r"^[가-힣]{2,20}$"},
        "장소근거": {"type": ["string", "null"], "pattern": r"^[가-힣0-9 .,·()'-]{4,120}$"},
    },
    "required": ["대상", "카테고리", "장소", "장소근거"],
    "additionalProperties": False,
}


def model_fields(raw: Any) -> tuple[str, str | None, str | None]:
    answer = str(raw).strip()
    if answer.startswith("```"):
        answer = re.sub(r"^```(?:json)?\s*|\s*```$", "", answer, flags=re.IGNORECASE)
    visual_place = None
    visual_evidence = None
    try:
        parsed = json.loads(answer)
        if isinstance(parsed, dict):
            answer = str(parsed.get("카테고리", parsed.get("category", "")))
            place = parsed.get("장소", "")
            evidence = parsed.get("장소근거", "")
            if (isinstance(place, str) and re.fullmatch(r"[가-힣]{2,20}", place.strip())
                    and place.strip() not in COUNTRY_NAMES
                    and re.sub(r"\s+", "", place) not in EMPTY_PLACE_WORDS
                    and isinstance(evidence, str)
                    and re.fullmatch(r"[가-힣0-9\s.,·()'\"-]{4,120}", evidence.strip())
                    and re.sub(r"\s+", "", evidence) not in EMPTY_PLACE_WORDS):
                visual_place = place.strip()
                visual_evidence = evidence.strip()
            if not visual_place:
                target = " ".join(value for value in (parsed.get("대상"), evidence) if isinstance(value, str)).casefold()
                for landmark, (city, name) in LANDMARK_CITIES.items():
                    if landmark in target:
                        visual_place = city
                        visual_evidence = f"AI가 {name}을 식별함"
                        break
    except json.JSONDecodeError:
        pass
    answer = answer.strip("` \n.。")
    category = answer if answer in SUGGESTED_CATEGORIES else "기타"
    return category, visual_place, visual_evidence


def model_suggestion(raw: Any, place: str | None, home_distance: float | None,
                     allow_visual_place: bool = True) -> str:
    category, visual_place, _ = model_fields(raw)
    if category == "여행" and place and (home_distance is None or home_distance > 30):
        return f"{place}여행"
    if (category == "여행" and not place and allow_visual_place and visual_place
            and (home_distance is None or home_distance > 30)):
        return f"{visual_place}여행"
    return category


def day_samples(files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    images = [file for file in files if file["kind"] == "image"]
    videos = [file for file in files if file["kind"] == "video"]
    image_limit = DAY_SAMPLE_LIMIT - bool(videos)
    if len(images) <= image_limit:
        chosen = images
    else:
        chosen = [images[round(index * (len(images) - 1) / (image_limit - 1))]
                  for index in range(image_limit)]
    if videos:
        chosen.append(videos[len(videos) // 2])
    return sorted(chosen, key=lambda file: (file["captured_at"], file["name"]))


@dataclass(frozen=True)
class Settings:
    photo_root: Path | None
    model_api: str = "http://127.0.0.1:8000/v1"
    model_name: str = "openbmb/MiniCPM-V-4.6"
    bind: str = "127.0.0.1"
    port: int = 8040
    home_settings: Path | None = None
    date_year_correction: tuple[str, int, int] | None = None
    ui_origin: str = "http://127.0.0.1:3000"

    @classmethod
    def from_env(cls) -> Settings:
        load_dotenv(ENGINE_DIR / ".env")
        root = os.getenv("PHOTO_ROOT")
        correction = os.getenv("DATE_YEAR_CORRECTION", "")
        match = re.fullmatch(r"([^:/]+):(\d{4}):(\d{4})", correction) if correction else None
        if correction and not match:
            raise ValueError("DATE_YEAR_CORRECTION must be folder:from_year:to_year")
        return cls(
            photo_root=Path(root).expanduser() if root else None,
            model_api=os.getenv("MODEL_API", cls.model_api).rstrip("/"),
            model_name=os.getenv("MODEL_NAME", cls.model_name),
            bind=os.getenv("ENGINE_BIND", cls.bind),
            port=int(os.getenv("ENGINE_PORT", str(cls.port))),
            home_settings=ENGINE_DIR / ".local-settings.json",
            date_year_correction=(match.group(1), int(match.group(2)), int(match.group(3))) if match else None,
        )


class ScanRequest(BaseModel):
    source: str


class DayRequest(BaseModel):
    day_id: int


class ApproveRequest(BaseModel):
    day_id: int
    category: str


class EditCategoryRequest(BaseModel):
    day_id: int
    category: str


class MoveRequest(BaseModel):
    day_id: int


class HomeRequest(BaseModel):
    location: str


class Review:
    def __init__(self) -> None:
        self.photo_root: Path | None = None
        self.picked_folder: Path | None = None
        self.scanned_folder: Path | None = None
        self.days: list[dict[str, Any]] = []
        self.by_id: dict[int, dict[str, Any]] = {}
        self.next_id = 1
        self.lock = threading.RLock()
        self.scan: dict[str, Any] = {"state": "idle", "source": None, "scanned_files": 0, "found_media": 0, "error": None}
        self.batch: dict[str, Any] = {"state": "idle", "total": 0, "done": 0, "failed": 0,
                                      "last_error": None, "failures": []}
        self.stop_batch = False
        self.home_location = ""

    def id(self) -> int:
        value = self.next_id
        self.next_id += 1
        return value


def job_status(job: dict[str, Any], count: int) -> dict[str, Any]:
    started = job.get("_started_at")
    finished = job.get("_finished_at")
    elapsed = max(0.0, (finished or time.monotonic()) - started) if started is not None else 0.0
    return {key: value for key, value in job.items() if not key.startswith("_")} | {
        "elapsed_seconds": elapsed,
        "rate_per_second": round(count / elapsed, 2) if elapsed > 0 else 0.0,
    }


def fail(message: str, status: int = 400) -> None:
    raise HTTPException(status_code=status, detail=message)


def root_path(photo_root: Path | None) -> Path:
    if photo_root is None:
        fail("Set PHOTO_ROOT before choosing a folder")
    try:
        root = photo_root.resolve(strict=True)
    except OSError:
        fail("설정된 폴더를 찾을 수 없습니다. 드라이브 연결과 PHOTO_ROOT를 확인하세요.")
    if not root.is_dir():
        fail("Media root is not a directory")
    return root


def visible(name: str) -> bool:
    return bool(name) and not name.startswith(".") and name.casefold() not in HIDDEN_FOLDERS


def chosen_folder(root: Path, source: str) -> Path:
    parts = source.split("/")
    if (not source or len(source) > 1024 or "\\" in source or
            any(part in {"", ".", ".."} or not visible(part) for part in parts)):
        fail("Select one listed folder")
    path = root
    for part in parts:
        path = path / part
        if path.is_symlink() or not path.is_dir():
            fail("Select a real folder")
    if not path.resolve(strict=True).is_relative_to(root):
        fail("Folder is outside the media root")
    return path


def image_date(path: Path) -> datetime | None:
    try:
        with Image.open(path) as image:
            exif = image.getexif()
            for tag in (36867, 36868, 306):
                value = exif.get(tag)
                if value:
                    try:
                        return datetime.strptime(str(value), "%Y:%m:%d %H:%M:%S")
                    except ValueError:
                        pass
    except (OSError, ValueError):
        pass
    return None


def image_gps(path: Path) -> dict[str, float] | None:
    try:
        with Image.open(path) as image:
            gps = image.getexif().get_ifd(34853)
        if not gps:
            return None

        def decimal(values: Any, ref: Any) -> float:
            degrees, minutes, seconds = (float(part) for part in values)
            value = degrees + minutes / 60 + seconds / 3600
            direction = ref.decode() if isinstance(ref, bytes) else str(ref)
            return -value if direction.upper() in {"S", "W"} else value

        latitude = decimal(gps[2], gps[1])
        longitude = decimal(gps[4], gps[3])
        if (not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or
                abs(latitude) < 0.000001 and abs(longitude) < 0.000001):
            return None
        return {"latitude": round(latitude, 6), "longitude": round(longitude, 6)}
    except (OSError, ValueError, TypeError, KeyError, ZeroDivisionError):
        return None


def video_gps(path: Path) -> dict[str, float] | None:
    raw = probe(path, "format_tags=location,location-eng,com.apple.quicktime.location.ISO6709")
    match = re.search(r"([+-]\d{1,2}(?:\.\d+)?)([+-]\d{1,3}(?:\.\d+)?)", raw or "")
    if not match:
        return None
    latitude, longitude = map(float, match.groups())
    if (not -90 <= latitude <= 90 or not -180 <= longitude <= 180 or
            abs(latitude) < 0.000001 and abs(longitude) < 0.000001):
        return None
    return {"latitude": round(latitude, 6), "longitude": round(longitude, 6)}


def probe(path: Path, fields: str) -> str | None:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", fields, "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
            capture_output=True, text=True, timeout=15, check=True,
        )
        return result.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def dji_filename_date(path: Path) -> datetime | None:
    match = re.fullmatch(r"DJI_(\d{14})_\d+_[A-Za-z0-9]+", path.stem, re.IGNORECASE)
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y%m%d%H%M%S")
    except ValueError:
        return None


def captured_at(path: Path, kind: str, mtime: float) -> datetime:
    filename_date = dji_filename_date(path)
    if kind == "image":
        date = image_date(path)
        if date:
            return date
        if filename_date:
            return filename_date
    else:
        raw = probe(path, "format_tags=creation_time")
        if raw:
            try:
                timestamp = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                local_date = timestamp.astimezone().replace(tzinfo=None)
                # Some DJI videos tag local clock time as UTC; the filename shows which clock matches.
                if filename_date and min(
                    abs((filename_date - local_date).total_seconds()),
                    abs((filename_date - timestamp.replace(tzinfo=None)).total_seconds()),
                ) <= 300:
                    return filename_date
                return local_date
            except ValueError:
                pass
        if filename_date:
            return filename_date
    return datetime.fromtimestamp(mtime)


def scan_folder(folder: Path, progress: Any = None,
                year_correction: tuple[int, int] | None = None) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    scanned = 0
    found = 0

    def read_error(error: OSError) -> None:
        fail(f"Could not read selected folder: {error.strerror}")

    for current, dirs, files in os.walk(folder, topdown=True, followlinks=False, onerror=read_error):
        parent = Path(current)
        dirs[:] = [name for name in dirs if visible(name) and not (parent / name).is_symlink()]
        for name in files:
            scanned += 1
            if progress and scanned % 100 == 0:
                progress(scanned, found)
            path = parent / name
            extension = path.suffix.lower()
            kind = "image" if extension in IMAGE_EXTENSIONS else "video" if extension in VIDEO_EXTENSIONS else None
            if not kind or not visible(name) or path.is_symlink():
                continue
            try:
                meta = path.stat()
            except OSError:
                fail("Could not read media metadata")
            date = captured_at(path, kind, meta.st_mtime)
            original_date = date if year_correction and date.year == year_correction[0] else None
            if original_date:
                try:
                    date = date.replace(year=year_correction[1])
                except ValueError:
                    original_date = None
            gps = image_gps(path) if kind == "image" else video_gps(path)
            gps_place = nearby_place(gps["latitude"], gps["longitude"]) if gps else None
            groups.setdefault(date.strftime("%Y-%m-%d"), []).append({
                "id": 0, "name": name, "relative_path": str(path.relative_to(folder)),
                "kind": kind, "captured_at": date.strftime("%Y-%m-%d %H:%M:%S"),
                "original_captured_at": original_date.strftime("%Y-%m-%d %H:%M:%S") if original_date else None,
                "size": meta.st_size, "gps": gps, "gps_place": gps_place,
                "sampled": False,
                "_path": path, "_mtime_ns": meta.st_mtime_ns,
            })
            found += 1
    if progress:
        progress(scanned, found)
    return [{
        "id": 0, "date": date, "files": sorted(files, key=lambda file: (file["captured_at"], file["name"])),
        "suggested_category": None, "edited_category": None, "sample_count": 0,
        "gps_place": None, "visual_place": None, "visual_evidence": None,
        "approved_category": None, "destination": None, "moved": False,
    } for date, files in sorted(groups.items(), reverse=True)]


def day_summary(day: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in day.items() if key != "files" and not key.startswith("_")} | {
        "file_count": len(day["files"]),
        "corrected_files": sum(bool(file.get("original_captured_at")) for file in day["files"])}


def needs_classification(day: dict[str, Any]) -> bool:
    return not (day["suggested_category"] or day["edited_category"] or
                day["approved_category"] or day["moved"])


def public_file(file: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in file.items() if not key.startswith("_")}


def read_home(path: Path | None) -> str:
    if path is None:
        return ""
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("home_location", "")
        return value if isinstance(value, str) and len(value) <= 80 else ""
    except (OSError, ValueError, AttributeError):
        return ""


def save_home(path: Path | None, location: str) -> None:
    if path is None:
        return
    fd, name = tempfile.mkstemp(prefix=".local-settings-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"home_location": location}, file, ensure_ascii=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def find_day(review: Review, day_id: int) -> dict[str, Any]:
    day = review.by_id.get(day_id)
    if day is None:
        fail("Day not found")
    return day


def find_file(day: dict[str, Any], file_id: int) -> dict[str, Any]:
    file = day["_files_by_id"].get(file_id)
    if file is None:
        fail("File not found")
    return file


def check_file(root: Path, file: dict[str, Any]) -> Path:
    path: Path = file["_path"]
    try:
        if path.is_symlink():
            fail("Original media changed")
        meta = path.stat()
        canonical = path.resolve(strict=True)
    except OSError:
        fail("Original media is missing")
    if not canonical.is_relative_to(root) or meta.st_size != file["size"] or meta.st_mtime_ns != file["_mtime_ns"]:
        fail("Original media changed since scan")
    return path


def image_data(path: Path) -> str:
    try:
        with Image.open(path) as source:
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1024, 1024))
            output = BytesIO()
            image.convert("RGB").save(output, format="JPEG", quality=85)
            data = output.getvalue()
    except (OSError, ValueError):
        try:
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=1024:1024:force_original_aspect_ratio=decrease",
                "-f", "image2pipe", "-vcodec", "mjpeg", "pipe:1",
            ], capture_output=True, timeout=30, check=True)
            data = result.stdout
        except (OSError, subprocess.SubprocessError):
            fail("이미지를 읽을 수 없습니다")
    if not data or len(data) > MAX_IMAGE_BYTES:
        fail("이미지를 25MB 이하로 줄일 수 없습니다")
    return f"data:image/jpeg;base64,{base64.b64encode(data).decode('ascii')}"


def video_data(path: Path) -> str:
    raw = probe(path, "format=duration")
    try:
        duration = float(raw) if raw else 0.0
        fps = min(1.0, FRAME_LIMIT / duration) if duration > 0 else 1.0
    except ValueError:
        fps = 1.0
    with tempfile.TemporaryDirectory(prefix="llm-photo-frames-") as directory:
        target = Path(directory)
        filter_arg = f"fps={fps}:start_time=0,scale=768:-2,format=yuvj420p"
        try:
            subprocess.run([
                "ffmpeg", "-v", "error", "-i", str(path), "-vf", filter_arg,
                "-frames:v", str(FRAME_LIMIT), "-c:v", "mjpeg", "-threads:v", "1",
                "-q:v", "4", "-y", str(target / "frame-%03d.jpg"),
            ], capture_output=True, timeout=120, check=True)
        except (OSError, subprocess.SubprocessError):
            fail("Could not extract video frames")
        frames = sorted(target.glob("frame-*.jpg"))[:FRAME_LIMIT]
        if not frames:
            fail("Video contained no readable frames")
        encoded = ",".join(base64.b64encode(frame.read_bytes()).decode("ascii") for frame in frames)
    return f"data:video/jpeg;base64,{encoded}"


def valid_category(value: str) -> bool:
    return bool(re.fullmatch(r"[\w-]{1,40}", value, flags=re.UNICODE)) and any(
        character.isalnum() for character in value
    )


def category_slug(raw: str) -> str:
    value = raw.strip()
    if not valid_category(value):
        fail("Category must be 1–40 letters, numbers, underscores or hyphens")
    return value


def destination(root: Path, date: str, category: str) -> Path:
    day = datetime.strptime(date, "%Y-%m-%d")
    return root / day.strftime("%Y") / day.strftime("%Y_%m") / f"{day:%y%m%d}_{category}"


def make_destination(root: Path, target: Path) -> None:
    path = root
    for part in target.relative_to(root).parts:
        path = path / part
        if path.is_symlink() or (path.exists() and not path.is_dir()):
            fail("Destination contains a link or file")
        path.mkdir(exist_ok=True)
    if not target.resolve(strict=True).is_relative_to(root):
        fail("Destination leaves media root")


def create_app(settings: Settings) -> FastAPI:
    app = FastAPI(title="LLM Photo Organizer Engine")
    review = Review()
    review.photo_root = settings.photo_root
    review.home_location = read_home(settings.home_settings)
    app.add_middleware(CORSMiddleware, allow_origins=[settings.ui_origin, "http://localhost:3000"],
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type"])

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {"photo_root": str(review.photo_root) if review.photo_root else "",
                "model_name": settings.model_name, "video_frame_limit": FRAME_LIMIT,
                "home_location": review.home_location}

    @app.post("/api/home")
    def set_home(request: HomeRequest) -> dict[str, str]:
        location = request.location.strip()
        if len(location) > 80 or any(ord(character) < 32 for character in location):
            fail("Home location must be at most 80 printable characters")
        with review.lock:
            if review.batch["state"] == "running":
                fail("Wait for AI classification to finish", 409)
            try:
                save_home(settings.home_settings, location)
            except OSError:
                fail("Could not save home location", 500)
            review.home_location = location
        return {"home_location": location}

    @app.get("/api/sources")
    def sources(parent: str = "") -> list[dict[str, str]]:
        root = root_path(review.photo_root)
        folder = chosen_folder(root, parent) if parent else root
        return [{"name": entry.name, "path": f"{parent}/{entry.name}" if parent else entry.name}
                for entry in sorted(folder.iterdir(), key=lambda path: path.name.casefold())
                if visible(entry.name) and entry.is_dir() and not entry.is_symlink()]

    @app.post("/api/pick-folder")
    def pick_folder() -> dict[str, str | None]:
        try:
            result = subprocess.run(
                ["osascript", "-e", 'POSIX path of (choose folder with prompt "스캔할 폴더를 선택하세요")'],
                capture_output=True, text=True, timeout=300,
            )
        except (OSError, subprocess.SubprocessError):
            fail("macOS 폴더 선택창을 열지 못했습니다", 500)
        if result.returncode != 0:
            if "-128" in result.stderr:
                return {"source": None, "path": None}
            fail("macOS 폴더 선택창을 열지 못했습니다", 500)
        selected = Path(result.stdout.strip())
        if not selected.is_absolute() or selected.is_symlink() or not selected.is_dir():
            fail("실제 폴더를 선택하세요")
        folder = selected.resolve(strict=True)
        if folder == folder.parent or not visible(folder.name):
            fail("하위 폴더를 선택하세요")
        with review.lock:
            review.picked_folder = folder
        return {"source": str(folder), "path": str(folder)}

    @app.post("/api/scan")
    def scan(request: ScanRequest) -> dict[str, Any]:
        with review.lock:
            if review.scan["state"] == "running":
                fail("A scan is already running", 409)
            if review.batch["state"] == "running":
                fail("Stop AI classification before scanning", 409)
            picked = review.picked_folder
            known = (picked is not None and request.source == str(picked) or
                     review.scanned_folder is not None and request.source == str(review.scanned_folder))
            if known:
                folder = Path(request.source)
                if folder.is_symlink() or not folder.is_dir() or folder.resolve(strict=True) != folder:
                    fail("선택한 폴더를 다시 확인하세요")
                try:
                    current_root = root_path(review.photo_root)
                except HTTPException:
                    current_root = None
                root = current_root if current_root and folder.is_relative_to(current_root) else folder
            else:
                root = root_path(review.photo_root)
                folder = chosen_folder(root, request.source)
            review.scan = {"state": "running", "source": request.source, "scanned_files": 0,
                           "found_media": 0, "error": None, "_started_at": time.monotonic(),
                           "_finished_at": None}

        def progress(scanned: int, found: int) -> None:
            with review.lock:
                review.scan["scanned_files"] = scanned
                review.scan["found_media"] = found

        def work() -> None:
            try:
                rule = settings.date_year_correction
                correction = (rule[1], rule[2]) if rule and folder == root / rule[0] else None
                days = scan_folder(folder, progress, correction)
                with review.lock:
                    review.photo_root = root
                    review.scanned_folder = folder
                    if review.picked_folder == picked:
                        review.picked_folder = None
                    for day in days:
                        day["id"] = review.id()
                        for file in day["files"]:
                            file["id"] = review.id()
                        day["_files_by_id"] = {file["id"]: file for file in day["files"]}
                    review.days = days
                    review.by_id = {day["id"]: day for day in days}
                    review.scan["state"] = "complete"
                    review.scan["_finished_at"] = time.monotonic()
            except Exception as error:
                with review.lock:
                    review.scan["state"] = "error"
                    review.scan["error"] = error.detail if isinstance(error, HTTPException) else "Scan failed"
                    review.scan["_finished_at"] = time.monotonic()

        threading.Thread(target=work, daemon=True).start()
        return {"state": "running", "source": request.source}

    @app.get("/api/scan/status")
    def scan_status() -> dict[str, Any]:
        with review.lock:
            return job_status(review.scan, review.scan["scanned_files"])

    @app.get("/api/days")
    def get_days(page: int = Query(1, ge=1), page_size: int = Query(30, ge=1, le=100),
                 status: str = Query("all", pattern="^(all|unclassified|pending|approved|moved)$"),
                 month: str = Query("", pattern="^$|^[0-9]{4}-[0-9]{2}$")) -> dict[str, Any]:
        with review.lock:
            all_days = review.days
            filtered = [day for day in all_days if (not month or day["date"].startswith(month)) and (
                status == "all" or status == "unclassified" and needs_classification(day) or
                status == "pending" and not day["approved_category"] or
                status == "approved" and bool(day["approved_category"]) and not day["moved"] or
                status == "moved" and day["moved"])]
            start = (page - 1) * page_size
            return {
                "items": [day_summary(day) for day in filtered[start:start + page_size]],
                "page": page, "page_size": page_size, "total": len(filtered),
                "total_days": len(all_days),
                "total_files": sum(len(day["files"]) for day in all_days),
                "classified_days": sum(bool(day["suggested_category"]) for day in all_days),
                "unclassified_days": sum(needs_classification(day) for day in all_days),
                "pending_days": sum(not day["approved_category"] for day in all_days),
                "moved_files": sum(len(day["files"]) for day in all_days if day["moved"]),
            }

    @app.get("/api/days/{day_id}/files")
    def get_files(day_id: int, page: int = Query(1, ge=1),
                  page_size: int = Query(24, ge=1, le=100)) -> dict[str, Any]:
        with review.lock:
            day = find_day(review, day_id)
            start = (page - 1) * page_size
            return {"day": day_summary(day), "items": [public_file(file) for file in day["files"][start:start + page_size]],
                    "page": page, "page_size": page_size, "total": len(day["files"])}

    @app.get("/api/days/{day_id}/folders")
    def date_folders(day_id: int) -> dict[str, list[dict[str, str]]]:
        with review.lock:
            date = find_day(review, day_id)["date"]
        day = datetime.strptime(date, "%Y-%m-%d")
        root = root_path(review.photo_root)
        year = root / day.strftime("%Y")
        month = year / day.strftime("%Y_%m")
        if year.is_symlink() or month.is_symlink():
            fail("Destination contains a link")
        if not month.is_dir():
            return {"items": []}
        prefix = day.strftime("%y%m%d_")
        items = []
        for entry in month.iterdir():
            if entry.is_symlink() or not entry.is_dir() or not entry.name.startswith(prefix):
                continue
            category = entry.name[len(prefix):]
            if valid_category(category):
                items.append({"category": category, "name": entry.name,
                              "path": str(entry.relative_to(root))})
        return {"items": sorted(items, key=lambda item: item["name"].casefold())}

    @app.get("/api/preview/{day_id}/{file_id}")
    def preview(day_id: int, file_id: int) -> Response:
        with review.lock:
            day = find_day(review, day_id)
            if day["moved"]:
                fail("Day has already moved")
            file = find_file(day, file_id).copy()
        path = check_file(root_path(review.photo_root), file)
        try:
            result = subprocess.run([
                "ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1",
                "-vf", "scale=320:-2,format=yuvj420p", "-f", "image2pipe", "-vcodec", "mjpeg",
                "-threads:v", "1", "pipe:1",
            ], capture_output=True, timeout=30, check=True)
        except (OSError, subprocess.SubprocessError):
            fail("Could not create preview")
        if not result.stdout or len(result.stdout) > 5_000_000:
            fail("Could not create preview")
        return Response(result.stdout, media_type="image/jpeg")

    def classify_day(day_id: int) -> dict[str, Any]:
        with review.lock:
            if review.scan["state"] == "running":
                fail("Wait for the scan to finish", 409)
            day = find_day(review, day_id)
            if day["moved"] or day["approved_category"]:
                fail("Reopen the day category before classifying", 409)
            samples = [file.copy() for file in day_samples(day["files"])]
            file_count = len(day["files"])
            home = review.home_location
        has_gps = any(file["gps"] for file in samples)
        content: list[dict[str, Any]] = [{"type": "text", "text": (
            f"{CLASSIFY_PROMPT}\n이 날짜에는 파일 {file_count}개가 있습니다. "
            f"시간대에 걸쳐 고른 대표 파일 {len(samples)}개를 봅니다."
            + (f"\n사용자가 입력한 집 지역: {home}." if home and has_gps else ""))}]
        places = {file["gps_place"] for file in samples if file["gps_place"]}
        gps_place = next(iter(places)) if len(places) == 1 else None
        home_distances: list[float] = []
        for index, file in enumerate(samples, 1):
            path = check_file(root_path(review.photo_root), file)
            hint = f"대표 파일 {index}: {file['kind']}."
            if file["gps"]:
                hint += (f" 촬영 GPS 위도 {file['gps']['latitude']}, "
                         f"경도 {file['gps']['longitude']}.")
                if file["gps_place"]:
                    hint += f" 가까운 지역 {file['gps_place']} (행정 경계는 아님)."
                if home:
                    distance = home_distance_km(file["gps"]["latitude"], file["gps"]["longitude"], home)
                    if distance is not None:
                        home_distances.append(distance)
                        hint += f" 집 지역 중심에서 약 {distance:.0f} km."
            content.append({"type": "text", "text": hint})
            content.append({"type": "image_url", "image_url": {"url": image_data(path)}} if file["kind"] == "image"
                           else {"type": "video_url", "video_url": {"url": video_data(path)}})
        body = {
            "model": settings.model_name,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 180,
            "temperature": 0,
            "response_format": {"type": "json_schema", "json_schema": {
                "name": "day_classification", "schema": CLASSIFY_SCHEMA, "strict": True,
            }},
        }
        try:
            with httpx.Client(timeout=180) as client:
                response = client.post(f"{settings.model_api}/chat/completions", json=body)
                response.raise_for_status()
                raw = str(response.json()["choices"][0]["message"]["content"] or "")
        except httpx.TimeoutException:
            fail("모델 응답 시간이 초과됐습니다", 502)
        except httpx.HTTPStatusError as error:
            fail(f"모델 서버 HTTP {error.response.status_code}", 502)
        except httpx.RequestError:
            fail("모델 서버에 연결할 수 없습니다", 502)
        except (KeyError, IndexError, ValueError):
            fail("모델 응답 형식이 올바르지 않습니다", 502)
        base_category, visual_place, visual_evidence = model_fields(raw)
        if gps_place or len(places) > 1:
            visual_place = visual_evidence = None
        if not places and home and visual_place and visual_place in re.sub(r"\s+", "", home):
            visual_place = visual_evidence = None
        category = (base_category if len(places) > 1 else
                    model_suggestion(raw, gps_place, min(home_distances) if home_distances else None,
                                     allow_visual_place=visual_place is not None))
        with review.lock:
            if review.scan["state"] == "running":
                fail("Wait for the scan to finish", 409)
            day = find_day(review, day_id)
            if day["moved"] or day["approved_category"]:
                fail("Reopen the day category before classifying", 409)
            sample_ids = {file["id"] for file in samples}
            for file in day["files"]:
                file["sampled"] = file["id"] in sample_ids
            day["sample_count"] = len(samples)
            day["suggested_category"] = category
            day["gps_place"] = gps_place
            day["visual_place"] = visual_place
            day["visual_evidence"] = visual_evidence
            return day_summary(day)

    @app.post("/api/classify")
    def classify(request: DayRequest) -> dict[str, Any]:
        return classify_day(request.day_id)

    @app.get("/api/classify/batch/status")
    def batch_status() -> dict[str, Any]:
        with review.lock:
            return job_status(review.batch, review.batch["done"] - review.batch["failed"])

    @app.post("/api/classify/batch")
    def start_batch() -> dict[str, Any]:
        with review.lock:
            if review.scan["state"] == "running" or review.batch["state"] == "running":
                fail("Wait for the current job to finish", 409)
            queue = [day["id"] for day in review.days if needs_classification(day)]
            if not queue:
                fail("No unclassified days remain")
            review.batch = {"state": "running", "total": len(queue), "done": 0, "failed": 0,
                            "last_error": None, "failures": [],
                            "_started_at": time.monotonic(), "_finished_at": None}
            review.stop_batch = False

        def work() -> None:
            for day_id in queue:
                with review.lock:
                    if review.stop_batch:
                        review.batch["state"] = "stopped"
                        review.batch["_finished_at"] = time.monotonic()
                        return
                try:
                    classify_day(day_id)
                except Exception as error:
                    with review.lock:
                        review.batch["failed"] += 1
                        reason = error.detail if isinstance(error, HTTPException) else "분류 중 오류가 발생했습니다"
                        review.batch["last_error"] = reason
                        day = review.by_id.get(day_id)
                        review.batch["failures"].append({"date": day["date"] if day else None,
                                                         "reason": reason})
                finally:
                    with review.lock:
                        review.batch["done"] += 1
            with review.lock:
                review.batch["state"] = "complete"
                review.batch["_finished_at"] = time.monotonic()

        threading.Thread(target=work, daemon=True).start()
        return {"state": "running", "total": len(queue)}

    @app.post("/api/classify/batch/stop")
    def stop_batch() -> dict[str, Any]:
        with review.lock:
            if review.batch["state"] != "running":
                fail("No classification job is running", 409)
            review.stop_batch = True
            return {"state": "stopping"}

    @app.post("/api/category")
    def edit_category(request: EditCategoryRequest) -> dict[str, Any]:
        category = category_slug(request.category)
        with review.lock:
            if review.scan["state"] == "running" or review.batch["state"] == "running":
                fail("Wait for the current job to finish", 409)
            day = find_day(review, request.day_id)
            if day["moved"]:
                fail("Day has already moved")
            day["edited_category"] = category
            if day["approved_category"] != category:
                day["approved_category"] = None
                day["destination"] = None
            return day_summary(day)

    @app.post("/api/approve")
    def approve(request: ApproveRequest) -> dict[str, Any]:
        category = category_slug(request.category)
        with review.lock:
            if review.scan["state"] == "running":
                fail("Wait for the scan to finish", 409)
            day = find_day(review, request.day_id)
            if day["moved"]:
                fail("Day has already moved")
            day["edited_category"] = category if category != day["suggested_category"] else None
            day["approved_category"] = category
            day["destination"] = str(destination(root_path(review.photo_root), day["date"], category))
            return day_summary(day)

    @app.post("/api/move")
    def move(request: MoveRequest) -> dict[str, Any]:
        with review.lock:
            if review.scan["state"] == "running":
                fail("Wait for the scan to finish", 409)
            if review.batch["state"] == "running":
                fail("Wait for AI classification to finish", 409)
            day = find_day(review, request.day_id)
            if day["moved"]:
                fail("Day has already moved")
            category = day["approved_category"]
            if not category:
                fail("Approve a category before moving")
            root = root_path(review.photo_root)
            target = destination(root, day["date"], category)
            names: set[str] = set()
            for file in day["files"]:
                check_file(root, file)
                if file["name"] in names or os.path.lexists(target / file["name"]):
                    fail("Destination filename collision")
                names.add(file["name"])
            make_destination(root, target)
            moved: list[tuple[Path, Path]] = []
            try:
                for file in day["files"]:
                    source = file["_path"]
                    output = target / file["name"]
                    source.rename(output)
                    moved.append((source, output))
            except OSError:
                rolled_back = True
                for source, output in reversed(moved):
                    try:
                        output.rename(source)
                    except OSError:
                        rolled_back = False
                fail(f"Move stopped; rollback {'succeeded' if rolled_back else 'failed'}. Inspect the folders before retrying", 500)
            day["moved"] = True
            day["destination"] = str(target)
            return day_summary(day)

    return app


settings = Settings.from_env()
app = create_app(settings)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host=settings.bind, port=settings.port)

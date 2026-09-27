from __future__ import annotations

import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from PIL import Image

from engine.main import DAY_SAMPLE_LIMIT, FRAME_LIMIT, Settings, create_app, day_samples, image_gps, model_suggestion, video_data, video_gps
from engine.places import home_distance_km, nearby_place


class EngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="llm-photo-test-")
        self.root = Path(self.temp.name).resolve()
        (self.root / "chosen").mkdir()
        (self.root / "other").mkdir()
        Image.new("RGB", (16, 16), "red").save(self.root / "chosen" / "sample.jpg")
        Image.new("RGB", (16, 16), "blue").save(self.root / "other" / "private.jpg")
        self.client = TestClient(create_app(Settings(photo_root=self.root, home_settings=self.root / ".home.json")))

    def tearDown(self) -> None:
        self.client.close()
        self.temp.cleanup()

    def scan(self, source: str = "chosen") -> dict:
        response = self.client.post("/api/scan", json={"source": source})
        self.assertEqual(response.status_code, 200)
        for _ in range(200):
            status = self.client.get("/api/scan/status").json()
            if status["state"] != "running":
                self.assertEqual(status["state"], "complete", status)
                return status
            time.sleep(0.01)
        self.fail("scan did not finish")

    def test_browse_and_scan_only_selected_nested_folder(self) -> None:
        nested = self.root / "chosen" / "trip"
        nested.mkdir()
        Image.new("RGB", (16, 16), "green").save(nested / "nested.jpg")
        (self.root / "chosen" / "linked").symlink_to(self.root / "other", target_is_directory=True)

        top = self.client.get("/api/sources").json()
        self.assertIn({"name": "chosen", "path": "chosen"}, top)
        children = self.client.get("/api/sources", params={"parent": "chosen"}).json()
        self.assertEqual(children, [{"name": "trip", "path": "chosen/trip"}])
        for path in ("../other", "chosen/../other", "chosen/linked", "/other", "chosen//trip"):
            self.assertEqual(self.client.get("/api/sources", params={"parent": path}).status_code, 400)
            self.assertEqual(self.client.post("/api/scan", json={"source": path}).status_code, 400)

        self.scan("chosen/trip")
        days = self.client.get("/api/days").json()
        self.assertEqual(days["total_files"], 1)
        files = self.client.get(f"/api/days/{days['items'][0]['id']}/files").json()["items"]
        self.assertEqual([file["name"] for file in files], ["nested.jpg"])
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())
        self.assertTrue((self.root / "other" / "private.jpg").exists())

    def test_scan_selected_folder_includes_nested_media(self) -> None:
        nested = self.root / "chosen" / "level-one" / "level-two"
        nested.mkdir(parents=True)
        Image.new("RGB", (16, 16), "green").save(nested / "deep.jpg")
        self.scan("chosen")
        days = self.client.get("/api/days").json()
        self.assertEqual(days["total_files"], 2)
        files = self.client.get(f"/api/days/{days['items'][0]['id']}/files").json()["items"]
        self.assertEqual({file["relative_path"] for file in files}, {"sample.jpg", "level-one/level-two/deep.jpg"})
        self.assertTrue((self.root / "other" / "private.jpg").exists())

    def test_mixed_dji_dates_and_source_scoped_year_correction(self) -> None:
        (self.root / "chosen" / "sample.jpg").unlink()
        for date, sequence in (("2000:04:10 23:31:55", "0001"),
                               ("2000:04:11 09:12:00", "0002")):
            exif = Image.Exif()
            exif[36867] = date
            filename = f"DJI_{date.replace(':', '').replace(' ', '')}_{sequence}_D.JPG"
            Image.new("RGB", (16, 16), "red").save(self.root / "chosen" / filename, exif=exif)
        (self.root / "chosen" / "DJI_20000410233230_0004_D.MP4").write_bytes(b"temporary video")
        (self.root / "chosen" / "DJI_20000411091300_0005_D.MP4").write_bytes(b"temporary video")
        self.client.close()
        self.client = TestClient(create_app(Settings(
            photo_root=self.root,
            home_settings=self.root / ".home.json",
            date_year_correction=("chosen", 2000, 2026),
        )))

        def fake_probe(path: Path, fields: str) -> str | None:
            if fields == "format_tags=creation_time":
                return ("2000-04-10T23:32:31Z" if "20000410" in path.name
                        else "2000-04-11T09:13:01Z")
            return None

        with patch("engine.main.probe", side_effect=fake_probe):
            self.scan()
        days = self.client.get("/api/days").json()
        self.assertEqual([(day["date"], day["file_count"], day["corrected_files"])
                          for day in days["items"]],
                         [("2026-04-11", 2, 2), ("2026-04-10", 2, 2)])
        files = self.client.get(f"/api/days/{days['items'][1]['id']}/files").json()["items"]
        self.assertEqual({file["kind"] for file in files}, {"image", "video"})
        self.assertTrue(all(file["captured_at"].startswith("2026-04-10") for file in files))
        self.assertTrue(all(file["original_captured_at"].startswith("2000-04-10") for file in files))
        self.assertEqual(days["pending_days"], 2)
        self.assertEqual(self.client.get("/api/days", params={"status": "unclassified"}).json()["total"], 2)
        self.client.post("/api/category", json={"day_id": days["items"][1]["id"], "category": "일상"})
        self.assertEqual(self.client.get("/api/days", params={"status": "unclassified"}).json()["total"], 1)
        self.assertTrue((self.root / "chosen" / "DJI_20000410233230_0004_D.MP4").exists())

        exif = Image.Exif()
        exif[36867] = "2000:04:10 10:00:00"
        Image.new("RGB", (16, 16), "blue").save(
            self.root / "other" / "DJI_20000410100000_0001_D.JPG", exif=exif)
        self.scan("other")
        other_dates = {day["date"] for day in self.client.get("/api/days").json()["items"]}
        self.assertIn("2000-04-10", other_dates)
        self.assertNotIn("2026-04-10", other_dates)

    def test_native_folder_pick_does_not_scan_until_requested(self) -> None:
        with tempfile.TemporaryDirectory(prefix="llm-photo-picked-") as directory:
            folder = Path(directory) / "picked"
            folder.mkdir()
            Image.new("RGB", (16, 16), "green").save(folder / "picked.jpg")
            result = subprocess.CompletedProcess([], 0, stdout=str(folder) + "\n", stderr="")
            with patch("engine.main.subprocess.run", return_value=result) as dialog:
                picked = self.client.post("/api/pick-folder")
            self.assertEqual(picked.status_code, 200)
            self.assertEqual(picked.json()["source"], str(folder.resolve()))
            self.assertEqual(dialog.call_args.args[0][0], "osascript")
            self.assertEqual(self.client.get("/api/days").json()["total_files"], 0)
            self.assertTrue((folder / "picked.jpg").exists())

            self.scan(picked.json()["source"])
            self.assertEqual(self.client.get("/api/days").json()["total_files"], 1)
            self.assertEqual(self.client.get("/api/config").json()["photo_root"], str(folder.resolve()))
            self.assertTrue((folder / "picked.jpg").exists())
            self.assertTrue((self.root / "chosen" / "sample.jpg").exists())

    def test_cancel_native_folder_pick_keeps_review(self) -> None:
        result = subprocess.CompletedProcess([], 1, stdout="", stderr="User canceled. (-128)")
        with patch("engine.main.subprocess.run", return_value=result):
            picked = self.client.post("/api/pick-folder")
        self.assertEqual(picked.json(), {"source": None, "path": None})
        self.assertEqual(self.client.get("/api/config").json()["photo_root"], str(self.root))

    def test_native_pick_inside_root_keeps_destination_root(self) -> None:
        folder = self.root / "chosen"
        result = subprocess.CompletedProcess([], 0, stdout=str(folder) + "\n", stderr="")
        with patch("engine.main.subprocess.run", return_value=result):
            picked = self.client.post("/api/pick-folder").json()
        self.scan(picked["source"])
        self.assertEqual(self.client.get("/api/config").json()["photo_root"], str(self.root))
        day = self.client.get("/api/days").json()["items"][0]
        approved = self.client.post("/api/approve", json={"day_id": day["id"], "category": "일상"}).json()
        self.assertTrue(Path(approved["destination"]).is_relative_to(self.root))
        self.assertTrue((folder / "sample.jpg").exists())

    def test_selected_folder_and_separate_move(self) -> None:
        config = self.client.get("/api/config").json()
        self.assertNotIn("model_api", config)
        scan = self.scan()
        self.assertGreater(scan["rate_per_second"], 0)
        self.assertGreater(scan["elapsed_seconds"], 0)
        time.sleep(0.02)
        self.assertEqual(self.client.get("/api/scan/status").json()["elapsed_seconds"],
                         scan["elapsed_seconds"])
        days = self.client.get("/api/days").json()["items"]
        self.assertEqual(len(days), 1)
        files = self.client.get(f"/api/days/{days[0]['id']}/files").json()["items"]
        self.assertEqual([file["name"] for file in files], ["sample.jpg"])
        self.assertEqual(self.client.post("/api/scan", json={"source": "../other"}).status_code, 400)

        day_id = days[0]["id"]
        self.assertEqual(self.client.post("/api/move", json={"day_id": day_id}).status_code, 400)
        approved = self.client.post("/api/approve", json={"day_id": day_id, "category": "sample"})
        self.assertEqual(approved.status_code, 200)
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())
        destination = Path(approved.json()["destination"])
        self.assertEqual(destination.relative_to(self.root).parts[-1][-7:], "_sample")
        self.assertEqual(self.client.post("/api/move", json={"day_id": day_id}).status_code, 200)
        self.assertTrue((destination / "sample.jpg").exists())
        self.assertFalse((self.root / "chosen" / "sample.jpg").exists())
        self.assertTrue((self.root / "other" / "private.jpg").exists())

    def test_edit_day_category_does_not_approve_and_clears_old_approval(self) -> None:
        self.scan()
        day_id = self.client.get("/api/days").json()["items"][0]["id"]
        saved = self.client.post("/api/category", json={"day_id": day_id, "category": "가족여행"})
        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["edited_category"], "가족여행")
        self.assertIsNone(saved.json()["approved_category"])
        self.assertEqual(self.client.post("/api/move", json={"day_id": day_id}).status_code, 400)
        approved = self.client.post("/api/approve", json={"day_id": day_id, "category": "가족여행"})
        self.assertEqual(approved.json()["approved_category"], "가족여행")
        changed = self.client.post("/api/category", json={"day_id": day_id, "category": "일상"})
        self.assertIsNone(changed.json()["approved_category"])
        self.assertIsNone(changed.json()["destination"])
        self.assertEqual(self.client.post("/api/move", json={"day_id": day_id}).status_code, 400)
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())

    def test_choose_existing_date_folder_without_moving(self) -> None:
        self.scan()
        day = self.client.get("/api/days").json()["items"][0]
        year, month, date = day["date"].split("-")
        parent = self.root / year / f"{year}_{month}"
        parent.mkdir(parents=True)
        name = f"{year[2:]}{month}{date}_가족모임"
        (parent / name).mkdir()
        (parent / f"{year[2:]}{month}{date}_나쁜 이름").mkdir()
        (parent / f"{year[2:]}{month}01_다른날").mkdir(exist_ok=True)
        (parent / f"{year[2:]}{month}{date}_연결").symlink_to(self.root / "other")

        choices = self.client.get(f"/api/days/{day['id']}/folders")
        self.assertEqual(choices.status_code, 200)
        self.assertEqual(choices.json()["items"], [{
            "category": "가족모임", "name": name,
            "path": f"{year}/{year}_{month}/{name}",
        }])
        saved = self.client.post("/api/category", json={"day_id": day["id"], "category": "가족모임"})
        self.assertEqual(saved.status_code, 200)
        self.assertIsNone(saved.json()["approved_category"])
        approved = self.client.post("/api/approve", json={"day_id": day["id"], "category": "가족모임"})
        self.assertEqual(Path(approved.json()["destination"]), parent / name)
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())

    def test_large_day_is_paged_and_bounded(self) -> None:
        sample = (self.root / "chosen" / "sample.jpg").read_bytes()
        for index in range(250):
            (self.root / "chosen" / f"photo-{index:04}.jpg").write_bytes(sample)
        self.scan()
        summary = self.client.get("/api/days").json()
        self.assertEqual(summary["total_files"], 251)
        self.assertEqual(summary["total_days"], 1)
        day_id = summary["items"][0]["id"]
        self.assertNotIn("files", summary["items"][0])
        first = self.client.get(f"/api/days/{day_id}/files").json()
        last = self.client.get(f"/api/days/{day_id}/files?page=11").json()
        self.assertEqual(len(first["items"]), 24)
        self.assertEqual(len(last["items"]), 11)
        self.assertEqual(first["total"], 251)
        self.assertEqual(self.client.get(f"/api/days/{day_id}/files?page_size=101").status_code, 422)
        self.assertEqual(self.client.get("/api/days?status=pending").json()["total"], 1)
        self.assertEqual(self.client.get("/api/days?status=moved").json()["total"], 0)
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": '{"카테고리":"풍경"}'}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            classified = self.client.post("/api/classify", json={"day_id": day_id})
        self.assertEqual(classified.json()["sample_count"], DAY_SAMPLE_LIMIT)
        content = client.__enter__.return_value.post.call_args.kwargs["json"]["messages"][0]["content"]
        self.assertEqual(len([item for item in content if item["type"] == "image_url"]), DAY_SAMPLE_LIMIT)
        self.assertEqual(client.__enter__.return_value.post.call_count, 1)

    def test_day_samples_include_at_most_one_video(self) -> None:
        files = [{"kind": "image" if index < 20 else "video", "captured_at": f"2025-01-01 12:{index:02d}:00",
                  "name": str(index)} for index in range(30)]
        chosen = day_samples(files)
        self.assertEqual(len(chosen), DAY_SAMPLE_LIMIT)
        self.assertEqual(sum(file["kind"] == "video" for file in chosen), 1)

    def test_video_frames_use_video_url_and_stay_within_limit(self) -> None:
        video = self.root / "chosen" / "sample.mp4"
        subprocess.run([
            "ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x96:rate=1",
            "-t", "2", "-c:v", "mpeg4", str(video),
        ], check=True)
        url = video_data(video)
        self.assertTrue(url.startswith("data:video/jpeg;base64,"))
        self.assertGreaterEqual(len(url.split(",")) - 1, 1)
        self.assertLessEqual(len(url.split(",")) - 1, FRAME_LIMIT)

    def test_batch_classification_requires_explicit_start_and_never_moves(self) -> None:
        Image.new("RGB", (16, 16), "green").save(self.root / "chosen" / "second.jpg")
        self.scan()
        day = self.client.get("/api/days").json()["items"][0]
        self.assertEqual(day["sample_count"], 0)
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": "풍경"}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            started = self.client.post("/api/classify/batch", json={})
            self.assertEqual(started.status_code, 200)
            for _ in range(200):
                status = self.client.get("/api/classify/batch/status").json()
                if status["state"] != "running":
                    break
                time.sleep(0.01)
        self.assertEqual(status["state"], "complete")
        self.assertEqual((status["done"], status["failed"]), (1, 0))
        self.assertGreater(status["rate_per_second"], 0)
        self.assertGreater(status["elapsed_seconds"], 0)
        self.assertEqual(client.__enter__.return_value.post.call_count, 1)
        updated = self.client.get("/api/days").json()["items"][0]
        self.assertEqual(updated["sample_count"], 2)
        self.assertEqual(self.client.get("/api/days").json()["classified_days"], 1)
        self.assertIsNone(updated["approved_category"])
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())
        self.assertEqual(self.client.post("/api/classify/batch", json={}).status_code, 400)

    def test_classification_reads_json_category_without_approving(self) -> None:
        self.assertEqual(model_suggestion('{"대상":"고양이","카테고리":"동물"}', None, None), "동물")
        self.assertEqual(model_suggestion("풍경", None, None), "풍경")
        self.assertEqual(model_suggestion('{"카테고리":"알 수 없음"}', None, None), "기타")
        self.assertEqual(model_suggestion('{"장소":"강Ning","카테고리":"여행"}',
                                          None, None), "여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"파리","장소근거":"에펠탑이 보임"}',
                                          None, None), "파리여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"강릉","장소근거":""}',
                                          None, None), "여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"빈문자열",'
                                          '"장소근거":"빈문자열"}', None, None), "여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":null,'
                                          '"장소근거":null}', None, None), "여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"프랑스","장소근거":"에펠탑이 보임"}',
                                          None, None), "파리여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"프랑스","장소근거":"거리와 나무가 보임"}',
                                          None, None), "여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행","장소":"파리","장소근거":"Paris tower"}',
                                          None, None), "여행")
        self.assertEqual(model_suggestion('{"대상":"프랑스의 Eiffel Tower","카테고리":"여행",'
                                          '"장소":"프랑스","장소근거":"혼합된文字"}',
                                          None, None), "파리여행")
        self.assertEqual(model_suggestion('{"대상":"프랑스","카테고리":"여행",'
                                          '"장소":"프랑스","장소근거":"Eiffel Tower(에IFFEL TOOPI)"}',
                                          None, None), "파리여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행"}', "강릉", 177), "강릉여행")
        self.assertEqual(model_suggestion('{"카테고리":"여행"}', "서울", 5), "여행")
        self.scan()
        day = self.client.get("/api/days").json()["items"][0]
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": '{"대상":"고양이","카테고리":"동물"}'}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day["id"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["suggested_category"], "동물")
        self.assertIsNone(result.json()["approved_category"])
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())

    def test_visual_place_without_gps_is_labeled_and_needs_approval(self) -> None:
        self.scan()
        day = self.client.get("/api/days").json()["items"][0]
        file = self.client.get(f"/api/days/{day['id']}/files").json()["items"][0]
        self.assertIsNone(file["gps"])
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"대상":"에펠탑","카테고리":"여행","장소":"파리","장소근거":"에펠탑이 보임"}'
        )}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day["id"]})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["suggested_category"], "파리여행")
        prompt = client.__enter__.return_value.post.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]
        self.assertIn("고유한 랜드마크", prompt)
        self.assertEqual(result.json()["visual_place"], "파리")
        self.assertEqual(result.json()["visual_evidence"], "에펠탑이 보임")
        updated = self.client.get(f"/api/days/{day['id']}/files").json()["items"][0]
        self.assertTrue(updated["sampled"])
        self.assertIsNone(result.json()["approved_category"])
        self.assertTrue((self.root / "chosen" / "sample.jpg").exists())

    def test_model_request_requires_korean_json_and_rejects_foreign_evidence(self) -> None:
        self.scan()
        day_id = self.client.get("/api/days").json()["items"][0]["id"]
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"카테고리":"여행","장소":"서울특별시","장소근거":"节日市场的装饰和人群活动"}'
        )}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day_id})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["suggested_category"], "여행")
        self.assertIsNone(result.json()["visual_place"])
        self.assertEqual(client.__enter__.return_value.post.call_count, 1)
        body = client.__enter__.return_value.post.call_args.kwargs["json"]
        self.assertIn("한국어로만", body["messages"][0]["content"][0]["text"])
        schema = body["response_format"]["json_schema"]["schema"]
        self.assertEqual(schema["properties"]["카테고리"]["enum"][0], "인물")
        self.assertEqual(schema["properties"]["장소"]["type"], ["string", "null"])
        self.assertIn("가-힣", schema["properties"]["장소근거"]["pattern"])

    def test_home_context_without_gps_is_not_visual_place(self) -> None:
        self.client.post("/api/home", json={"location": "서울특별시"})
        self.scan()
        day_id = self.client.get("/api/days").json()["items"][0]["id"]
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"대상":"행사","카테고리":"여행","장소":"서울특별시",'
            '"장소근거":"화상과 거리로 보이는 활동적 환경"}'
        )}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day_id})
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["suggested_category"], "여행")
        self.assertIsNone(result.json()["visual_place"])
        self.assertIsNone(result.json()["visual_evidence"])
        prompt = client.__enter__.return_value.post.call_args.kwargs["json"]["messages"][0]["content"][0]["text"]
        self.assertNotIn("서울특별시", prompt)

    def test_gps_and_home_context_suggest_place_without_approving(self) -> None:
        exif = Image.Exif()
        exif[34853] = {1: "N", 2: (37, 45, 0), 3: "E", 4: (128, 54, 0)}
        photo = self.root / "chosen" / "gps.jpg"
        Image.new("RGB", (16, 16), "green").save(photo, exif=exif)
        Image.new("RGB", (16, 16), "green").save(self.root / "chosen" / "third.jpg", exif=exif)
        self.assertEqual(image_gps(photo), {"latitude": 37.75, "longitude": 128.9})
        zero_exif = Image.Exif()
        zero_exif[34853] = {1: "N", 2: (0, 0, 0), 3: "E", 4: (0, 0, 0)}
        zero_photo = self.root / "other" / "zero.jpg"
        Image.new("RGB", (16, 16), "blue").save(zero_photo, exif=zero_exif)
        self.assertIsNone(image_gps(zero_photo))
        home = self.client.post("/api/home", json={"location": "서울특별시"})
        self.assertEqual(home.status_code, 200)
        self.assertEqual(self.client.get("/api/config").json()["home_location"], "서울특별시")
        self.assertTrue((self.root / ".home.json").exists())
        with TestClient(create_app(Settings(photo_root=self.root, home_settings=self.root / ".home.json"))) as reopened:
            self.assertEqual(reopened.get("/api/config").json()["home_location"], "서울특별시")
        self.scan()
        day = self.client.get("/api/days").json()["items"][0]
        files = self.client.get(f"/api/days/{day['id']}/files").json()["items"]
        gps_file = next(file for file in files if file["name"] == "gps.jpg")
        plain_file = next(file for file in files if file["name"] == "sample.jpg")
        self.assertIsNone(plain_file["gps"])
        self.assertEqual(gps_file["gps"], {"latitude": 37.75, "longitude": 128.9})
        self.assertEqual(gps_file["gps_place"], "강릉")

        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": '{"대상":"바다","카테고리":"여행"}'}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day["id"]})
        self.assertEqual(result.json()["suggested_category"], "강릉여행")
        content = client.__enter__.return_value.post.call_args.kwargs["json"]["messages"][0]["content"]
        prompt = " ".join(item["text"] for item in content if item["type"] == "text")
        self.assertIn("서울특별시", prompt)
        self.assertIn("강릉", prompt)
        self.assertIn("37.75", prompt)
        self.assertIn("128.9", prompt)
        self.assertEqual(len([item for item in content if item["type"] == "image_url"]), 3)
        self.assertEqual(result.json()["sample_count"], 3)
        self.assertIsNone(result.json()["approved_category"])
        response.json.return_value = {"choices": [{"message": {"content": '{"대상":"집","장소":"","카테고리":"일상"}'}}]}
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day["id"]})
        self.assertEqual(result.json()["suggested_category"], "일상")
        self.assertIsNone(result.json()["approved_category"])
        self.assertTrue(photo.exists())

    def test_mixed_gps_places_do_not_name_the_whole_date(self) -> None:
        for name, latitude, longitude in (("seoul.jpg", (37, 33, 0), (126, 59, 0)),
                                           ("gangneung.jpg", (37, 45, 0), (128, 54, 0))):
            exif = Image.Exif()
            exif[34853] = {1: "N", 2: latitude, 3: "E", 4: longitude}
            Image.new("RGB", (16, 16), "green").save(self.root / "chosen" / name, exif=exif)
        self.scan()
        day_id = self.client.get("/api/days").json()["items"][0]["id"]
        response = MagicMock()
        response.json.return_value = {"choices": [{"message": {"content": (
            '{"카테고리":"여행","장소":"파리","장소근거":"에펠탑이 보임"}'
        )}}]}
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        with patch("engine.main.httpx.Client", return_value=client):
            result = self.client.post("/api/classify", json={"day_id": day_id})
        self.assertEqual(result.json()["suggested_category"], "여행")
        self.assertIsNone(result.json()["gps_place"])
        self.assertIsNone(result.json()["visual_place"])

    def test_video_gps_reads_iso6709(self) -> None:
        with patch("engine.main.probe", return_value="+37.7500+128.9000+0.000/"):
            self.assertEqual(video_gps(self.root / "chosen" / "sample.mp4"),
                             {"latitude": 37.75, "longitude": 128.9})

    def test_local_places_are_hints_and_home_has_distance(self) -> None:
        self.assertEqual(nearby_place(37.75, 128.9), "강릉")
        self.assertEqual(nearby_place(37.56, 126.98), "서울")
        self.assertGreater(home_distance_km(37.75, 128.9, "서울특별시") or 0, 100)


if __name__ == "__main__":
    unittest.main()

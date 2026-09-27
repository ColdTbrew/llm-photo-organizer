import { existsSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "@playwright/test";

test("선택한 폴더를 날짜별로 검토하고 승인 후에만 이동한다", async ({ page, request }) => {
  const configResponse = await request.get("http://127.0.0.1:8042/api/config");
  expect(configResponse.ok()).toBeTruthy();
  const { photo_root: root } = await configResponse.json();
  const original = join(root, "mixed", "DJI_20000410100000_0001_D.JPG");
  const nested = join(root, "mixed", "nested", "DJI_20000410110000_0002_D.JPG");
  const outside = join(root, "outside", "private.jpg");

  await page.goto("/#review");
  await expect(page.getByText("엔진 연결됨", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "mixed", exact: true }).click();
  await expect(page.getByRole("heading", { name: "아직 검토할 파일이 없습니다" })).toBeVisible();
  expect(existsSync(original)).toBeTruthy();

  await page.getByRole("button", { name: "선택한 폴더 스캔" }).click();
  await expect(page.locator(".date-item")).toHaveCount(3);
  await expect(page.locator(".date-item").filter({ hasText: "2026년 4월 10일" })).toContainText("2개");
  await expect(page.locator(".date-item").filter({ hasText: "2026년 4월 11일" })).toContainText("2개");
  await expect(page.getByText("private.jpg")).toHaveCount(0);

  await page.locator(".date-item").filter({ hasText: "2026년 4월 10일" }).click();
  await expect(page.getByText("원본 시각 2000-04-10 10:00:00")).toBeVisible();
  await expect(page.getByText("DJI_20000410110000_0002_D.JPG", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "DJI_20000410100000_0001_D.JPG 보기" }).click();
  const viewer = page.getByRole("dialog", { name: "미디어 보기" });
  await expect(viewer).toBeVisible();
  await expect.poll(() => viewer.locator("img").evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBe(32);
  const imageSource = await viewer.locator("img").getAttribute("src");
  await page.keyboard.press("Escape");
  await expect(viewer).toBeHidden();

  await page.locator(".date-item").filter({ hasText: "2026년 4월 11일" }).click();
  await page.getByRole("button", { name: "DJI_20000411100000_0005_D.TIFF 보기" }).click();
  await expect(viewer).toBeVisible();
  await expect.poll(() => viewer.locator("img").evaluate((image) => (image as HTMLImageElement).naturalWidth)).toBe(1600);
  await page.getByRole("button", { name: "미디어 닫기" }).click();
  await expect(viewer).toBeHidden();

  await page.locator(".date-item").filter({ hasText: "2026년 4월 12일" }).click();
  await page.getByRole("button", { name: "DJI_20000412103000_0004_D.MP4 재생" }).click();
  await expect(viewer).toBeVisible();
  const video = viewer.locator("video");
  await expect.poll(() => video.evaluate((player) => (player as HTMLVideoElement).videoWidth)).toBe(64);
  await video.evaluate((player) => (player as HTMLVideoElement).play());
  await expect.poll(() => video.evaluate((player) => (player as HTMLVideoElement).currentTime)).toBeGreaterThan(0);
  const source = await video.getAttribute("src");
  const range = await request.get(source!, { headers: { Range: "bytes=0-99" } });
  expect(range.status()).toBe(206);
  expect(range.headers()["content-range"]).toMatch(/^bytes 0-99\//);
  await page.getByRole("button", { name: "미디어 닫기" }).click();
  await expect(viewer).toBeHidden();

  await page.locator(".date-item").filter({ hasText: "2026년 4월 10일" }).click();
  await page.getByLabel("이 날짜의 카테고리").fill("여행");
  await page.getByRole("button", { name: "카테고리 저장" }).click();
  await expect(page.getByText("2026/2026_04/260410_여행/")).toBeVisible();
  expect(existsSync(original)).toBeTruthy();
  expect(existsSync(nested)).toBeTruthy();

  await page.getByRole("button", { name: "경로 승인" }).click();
  await expect(page.getByText("승인된 경로")).toBeVisible();
  expect(existsSync(original)).toBeTruthy();
  expect(existsSync(nested)).toBeTruthy();

  page.once("dialog", (dialog) => dialog.accept());
  await page.getByRole("button", { name: "2개 파일 이동 실행" }).click();
  await expect(page.getByRole("region", { name: "선택한 날짜의 파일" }).getByRole("button", { name: "이동 완료" })).toBeVisible();
  expect(existsSync(join(root, "2026", "2026_04", "260410_여행", "DJI_20000410100000_0001_D.JPG"))).toBeTruthy();
  expect(existsSync(join(root, "2026", "2026_04", "260410_여행", "DJI_20000410110000_0002_D.JPG"))).toBeTruthy();
  expect(existsSync(outside)).toBeTruthy();
  expect(existsSync(original)).toBeFalsy();
  expect((await request.get(imageSource!)).status()).toBe(400);
});

import { createFileRoute } from "@tanstack/react-router";
import { useEffect, useRef, useState } from "react";

type Config = {
  photo_root: string;
  model_name: string;
  video_frame_limit: number;
  home_location: string;
};
type Source = { name: string; path: string };
type FolderOption = { category: string; name: string; path: string };
type Media = {
  id: number;
  name: string;
  relative_path: string;
  kind: "image" | "video";
  captured_at: string;
  original_captured_at: string | null;
  size: number;
  gps: { latitude: number; longitude: number } | null;
  gps_place: string | null;
  sampled: boolean;
};
type Day = {
  id: number;
  date: string;
  file_count: number;
  corrected_files: number;
  sample_count: number;
  suggested_category: string | null;
  edited_category: string | null;
  gps_place: string | null;
  visual_place: string | null;
  visual_evidence: string | null;
  approved_category: string | null;
  destination: string | null;
  moved: boolean;
};
type DayPage = {
  items: Day[];
  page: number;
  total: number;
  total_days: number;
  total_files: number;
  classified_days: number;
  unclassified_days: number;
  pending_days: number;
  moved_files: number;
};
type FilePage = { day: Day; items: Media[]; page: number; total: number };
type Scan = {
  state: "idle" | "running" | "complete" | "error";
  source: string | null;
  scanned_files: number;
  found_media: number;
  error: string | null;
  elapsed_seconds: number;
  rate_per_second: number;
};
type Batch = {
  state: "idle" | "running" | "stopped" | "complete";
  total: number;
  done: number;
  failed: number;
  last_error: string | null;
  failures?: { date: string | null; reason: string }[];
  elapsed_seconds: number;
  rate_per_second: number;
};
type Filter = "all" | "unclassified" | "pending" | "approved" | "moved";

const ENGINE_ORIGIN =
  import.meta.env.VITE_ENGINE_ORIGIN ?? "http://127.0.0.1:8040";
const DAY_SIZE = 30;
const FILE_SIZE = 24;
export const Route = createFileRoute("/")({ component: Home });

async function api<T>(path: string, body?: object): Promise<T> {
  const response = await fetch(
    `${ENGINE_ORIGIN}${path}`,
    body
      ? {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        }
      : undefined,
  );
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(
      typeof error?.detail === "string"
        ? error.detail
        : `요청 실패 (HTTP ${response.status})`,
    );
  }
  return response.json() as Promise<T>;
}

function pathFor(date: string, category: string): string {
  const [year, month, day] = date.split("-");
  return `${year}/${year}_${month}/${year.slice(2)}${month}${day}_${category || "카테고리"}/`;
}

function folderNameFor(date: string, category: string): string {
  return pathFor(date, category || "…").split("/")[2];
}

function dayCategory(day: Day): string {
  return day.approved_category ?? day.edited_category ?? day.suggested_category ?? "";
}

function dateLabel(date: string): string {
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "long",
    day: "numeric",
    weekday: "short",
  }).format(new Date(`${date}T00:00:00`));
}

function Home() {
  const [config, setConfig] = useState<Config | null>(null);
  const [sources, setSources] = useState<Source[]>([]);
  const [sourcesLoading, setSourcesLoading] = useState(false);
  const [sourcesError, setSourcesError] = useState("");
  const [selectedSource, setSelectedSource] = useState("");
  const [list, setList] = useState<DayPage | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [files, setFiles] = useState<FilePage | null>(null);
  const [dayPage, setDayPage] = useState(1);
  const [filePage, setFilePage] = useState(1);
  const [filter, setFilter] = useState<Filter>("all");
  const [month, setMonth] = useState("");
  const [monthPickerOpen, setMonthPickerOpen] = useState(false);
  const [pickerYear, setPickerYear] = useState(new Date().getFullYear());
  const monthPickerRef = useRef<HTMLDivElement>(null);
  const monthTriggerRef = useRef<HTMLButtonElement>(null);
  const [draft, setDraft] = useState("");
  const [draftEdited, setDraftEdited] = useState(false);
  const [editingFolderId, setEditingFolderId] = useState<number | null>(null);
  const [folderDraft, setFolderDraft] = useState("");
  const [folderOptions, setFolderOptions] = useState<{ dayId: number; items: FolderOption[] } | null>(null);
  const [folderLoadingId, setFolderLoadingId] = useState<number | null>(null);
  const [folderLoadError, setFolderLoadError] = useState("");
  const [homeDraft, setHomeDraft] = useState("");
  const [scan, setScan] = useState<Scan | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [engineState, setEngineState] = useState<
    "connecting" | "ready" | "offline"
  >("connecting");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [viewer, setViewer] = useState<{ dayId: number; file: Media } | null>(null);
  const viewerRef = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    const dialog = viewerRef.current;
    if (viewer && dialog && !dialog.open) dialog.showModal();
    if (!viewer && dialog?.open) dialog.close();
  }, [viewer]);

  useEffect(() => {
    if (!monthPickerOpen) return;
    function closeOnOutside(event: PointerEvent) {
      if (!monthPickerRef.current?.contains(event.target as Node)) {
        setMonthPickerOpen(false);
      }
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setMonthPickerOpen(false);
        monthTriggerRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", closeOnOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [monthPickerOpen]);

  function chooseMonth(value: string) {
    setMonth(value);
    setDayPage(1);
    setFilePage(1);
    setMonthPickerOpen(false);
    monthTriggerRef.current?.focus();
  }

  async function loadDays(
    page = dayPage,
    status = filter,
    selectedMonth = month,
  ) {
    const next = await api<DayPage>(
      `/api/days?page=${page}&page_size=${DAY_SIZE}&status=${status}&month=${selectedMonth}`,
    );
    setList(next);
    setSelectedId((current) =>
      next.items.some((day) => day.id === current)
        ? current
        : (next.items[0]?.id ?? null),
    );
    return next;
  }

  useEffect(() => {
    let active = true;
    Promise.all([
      api<Config>("/api/config"),
      api<Scan>("/api/scan/status"),
      api<Batch>("/api/classify/batch/status"),
    ])
      .then(([nextConfig, nextScan, nextBatch]) => {
        if (!active) return;
        setConfig(nextConfig);
        setHomeDraft(nextConfig.home_location ?? "");
        setSelectedSource(nextScan.source ?? "");
        setScan(nextScan);
        setBatch(nextBatch);
        setEngineState("ready");
      })
      .catch(() => {
        if (active) setEngineState("offline");
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (engineState !== "ready") return;
    let active = true;
    api<DayPage>(
      `/api/days?page=${dayPage}&page_size=${DAY_SIZE}&status=${filter}&month=${month}`,
    )
      .then((next) => {
        if (!active) return;
        setList(next);
        setSelectedId((current) =>
          next.items.some((day) => day.id === current)
            ? current
            : (next.items[0]?.id ?? null),
        );
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [engineState, dayPage, filter, month]);

  useEffect(() => {
    if (engineState === "ready") void loadSources();
  }, [engineState]);

  useEffect(() => {
    if (selectedId === null) {
      setFiles(null);
      return;
    }
    let active = true;
    setFiles(null);
    api<FilePage>(
      `/api/days/${selectedId}/files?page=${filePage}&page_size=${FILE_SIZE}`,
    )
      .then((next) => {
        if (active) setFiles(next);
      })
      .catch((cause) => {
        if (active) setError(cause.message);
      });
    return () => {
      active = false;
    };
  }, [selectedId, filePage]);

  useEffect(() => {
    if (scan?.state !== "running") return;
    const timer = window.setInterval(() => {
      void api<Scan>("/api/scan/status")
        .then((next) => {
          setScan(next);
          if (next.state === "complete") {
            void api<Config>("/api/config").then(setConfig);
            void loadSources();
            setDayPage(1);
            setFilePage(1);
            setFilter("all");
            setMonth("");
            setSelectedId(null);
            setFiles(null);
            void loadDays(1, "all", "").then((result) =>
              setNotice(
                `${next.source} 폴더에서 날짜 ${result.total_days.toLocaleString()}개, 파일 ${result.total_files.toLocaleString()}개를 찾았습니다.`,
              ),
            );
          }
          if (next.state === "error")
            setError(next.error ?? "스캔에 실패했습니다.");
        })
        .catch((cause) => setError(cause.message));
    }, 900);
    return () => window.clearInterval(timer);
  }, [scan?.state]);

  useEffect(() => {
    if (batch?.state !== "running") return;
    const timer = window.setInterval(() => {
      void api<Batch>("/api/classify/batch/status")
        .then((next) => {
          setBatch(next);
          void loadDays().catch((cause) => setError(cause.message));
          if (selectedId !== null)
            void api<FilePage>(
              `/api/days/${selectedId}/files?page=${filePage}&page_size=${FILE_SIZE}`,
            )
              .then(setFiles)
              .catch((cause) => setError(cause.message));
          if (next.state === "complete" || next.state === "stopped")
            setNotice(
              `날짜 AI 분류 ${num(next.done - next.failed)}개 완료 · 실패 ${num(next.failed)}개${next.state === "stopped" ? " · 중단됨" : ""}`,
            );
        })
        .catch((cause) => setError(cause.message));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [batch?.state, selectedId, filePage, dayPage, filter, month]);

  useEffect(() => {
    setDraftEdited(false);
  }, [selectedId]);

  useEffect(() => {
    const day = files?.day;
    if (day && !draftEdited)
      setDraft(day.approved_category ?? day.edited_category ?? day.suggested_category ?? "");
  }, [files?.day.id, files?.day.approved_category, files?.day.edited_category, files?.day.suggested_category, draftEdited]);

  async function refresh() {
    await loadDays();
    if (selectedId !== null)
      setFiles(
        await api<FilePage>(
          `/api/days/${selectedId}/files?page=${filePage}&page_size=${FILE_SIZE}`,
        ),
      );
  }

  async function loadSources() {
    setSourcesLoading(true);
    setSourcesError("");
    try {
      setSources(await api<Source[]>("/api/sources"));
    } catch (cause) {
      setSources([]);
      setSourcesError(cause instanceof Error ? cause.message : "폴더 목록을 불러오지 못했습니다.");
    } finally {
      setSourcesLoading(false);
    }
  }

  async function pickSource() {
    await run("pick-folder", async () => {
      const picked = await api<{ source: string | null; path: string | null }>("/api/pick-folder", {});
      if (picked.source) setSelectedSource(picked.source);
    });
  }

  async function editFolder(item: Day) {
    setSelectedId(item.id);
    setFilePage(1);
    setEditingFolderId(item.id);
    setFolderDraft(dayCategory(item));
    setFolderOptions(null);
    setFolderLoadingId(item.id);
    setFolderLoadError("");
    try {
      const choices = await api<{ items: FolderOption[] }>(`/api/days/${item.id}/folders`);
      setFolderOptions({ dayId: item.id, items: choices.items });
    } catch (cause) {
      setFolderLoadError(cause instanceof Error && cause.message.includes("404")
        ? "기존 폴더 목록은 엔진을 다시 시작한 뒤 사용할 수 있습니다. 현재 이름은 직접 편집할 수 있습니다."
        : "기존 폴더 목록을 불러오지 못했습니다. 이름을 직접 입력할 수 있습니다.");
    } finally {
      setFolderLoadingId((current) => current === item.id ? null : current);
    }
  }

  function saveFolder(item: Day) {
    void run("save-folder", async () => {
      const saved = await api<Day>("/api/category", {
        day_id: item.id,
        category: folderDraft,
      });
      const next = await loadDays();
      if (next.items.some((day) => day.id === item.id)) {
        setSelectedId(item.id);
        setFilePage(1);
        setFiles(await api<FilePage>(
          `/api/days/${item.id}/files?page=1&page_size=${FILE_SIZE}`,
        ));
        setDraft(dayCategory(saved));
      } else {
        setFiles(null);
      }
      setDraftEdited(false);
      setEditingFolderId(null);
      setNotice(`${item.date} 폴더명을 저장했습니다. 경로는 아직 승인되지 않았습니다.`);
    });
  }

  async function run(label: string, action: () => Promise<void>) {
    setBusy(label);
    setError("");
    setNotice("");
    try {
      await action();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "요청에 실패했습니다.");
    } finally {
      setBusy("");
    }
  }

  function startScan() {
    if (
      (list?.total_days ?? 0) > 0 &&
      !window.confirm(
        "새로 스캔하면 현재 검토 목록과 승인 상태가 교체됩니다. 계속할까요?",
      )
    )
      return;
    void run("scan", async () => {
      await api("/api/scan", { source: selectedSource });
      setScan({
        state: "running",
        source: selectedSource,
        scanned_files: 0,
        found_media: 0,
        error: null,
        elapsed_seconds: 0,
        rate_per_second: 0,
      });
    });
  }

  function startBatch() {
    const count = list?.unclassified_days ?? 0;
    if (
      !window.confirm(
        `미분류 날짜 ${num(count)}개를 설정된 MiniCPM-V-4.6 API로 순서대로 분류할까요? 날짜마다 대표 파일 최대 6개를 전송합니다. 중간에 멈출 수 있습니다.`,
      )
    )
      return;
    void run("batch", async () => {
      const next = await api<{ state: "running"; total: number }>(
        "/api/classify/batch",
        {},
      );
      setBatch({
        state: "running",
        total: next.total,
        done: 0,
        failed: 0,
        last_error: null,
        elapsed_seconds: 0,
        rate_per_second: 0,
      });
    });
  }

  const shownFiles =
    files?.day.id === selectedId && files.page === filePage ? files : null;
  const day =
    shownFiles?.day ?? list?.items.find((item) => item.id === selectedId);
  const draftMatchesApproval = draft === day?.approved_category;
  const draftMatchesSaved = draft === (day?.edited_category ?? day?.suggested_category ?? "");
  const dayPages = Math.max(1, Math.ceil((list?.total ?? 0) / DAY_SIZE));
  const filePages = Math.max(
    1,
    Math.ceil((shownFiles?.total ?? 0) / FILE_SIZE),
  );
  const num = (value: number) => value.toLocaleString("ko-KR");
  const rate = (value: number) => value.toLocaleString("ko-KR", { maximumFractionDigits: 2 });

  return (
    <main className="workspace">
      <a className="skip-link" href="#review-list">
        날짜 목록으로 건너뛰기
      </a>
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="LLM Photo Organizer 홈">
          <span className="brand-mark" aria-hidden="true">
            ◈
          </span>
          <span className="brand-copy">
            <b>LLM Photos</b>
            <small>PHOTO ORGANIZER</small>
          </span>
        </a>
        <div className="drive-tile">
          <span className="drive-glyph">▤</span>
          <span className="drive-copy">
            <b>드라이브 설정</b>
            <small title={config?.photo_root}>
              {config?.photo_root || "PHOTO_ROOT 설정 필요"}
            </small>
          </span>
        </div>
        <p className="nav-heading">워크스페이스</p>
        <a className="nav-link active" href="#review">
          <span>▦</span>
          <span>분류 검토</span>
          <b>{num(list?.total_days ?? 0)}</b>
        </a>
        <div className="sidebar-folders">
          <div className="sidebar-folder-head">
            <strong>원본 폴더</strong>
            <button type="button" onClick={() => void loadSources()} disabled={engineState !== "ready" || sourcesLoading}
              aria-label="폴더 목록 새로고침">↻</button>
          </div>
          {sourcesError ? <p className="sidebar-folder-message" role="status">{sourcesError}</p>
            : sourcesLoading ? <p className="sidebar-folder-message">폴더 확인 중…</p>
            : sources.length === 0 ? <p className="sidebar-folder-message">표시할 폴더가 없습니다.</p>
            : <div className="sidebar-folder-list" aria-label="원본 폴더 목록">
                {sources.map((source) => (
                  <button key={source.path} type="button"
                    className={selectedSource === source.path ? "selected" : ""}
                    aria-pressed={selectedSource === source.path}
                    title={source.path}
                    onClick={() => setSelectedSource((current) => current === source.path ? "" : source.path)}>
                    <span aria-hidden="true">▰</span><span>{source.name}</span>
                  </button>
                ))}
              </div>}
          <button type="button" className="sidebar-scan" onClick={startScan}
            disabled={!selectedSource || !!busy || scan?.state === "running" || batch?.state === "running"}>
            {scan?.state === "running" ? "탐색 중…" : "하위 폴더까지 탐색"}
          </button>
        </div>
        <div className="sidebar-note">
          <span className="note-icon">✳</span>
          <small>로컬 분류 엔진</small>
          <b>Python · FastAPI</b>
          <span>
            {engineState === "ready" ? "엔진 연결됨" : "엔진 연결 대기"}
          </span>
        </div>
      </aside>
      <section className="main-panel" id="review">
        <header className="topbar">
          <span>
            라이브러리 <i>/</i> <b>분류 검토</b>
          </span>
          <span className="engine-status">
            <i className={engineState === "ready" ? "dot online" : "dot"} />
            {engineState === "ready"
              ? "로컬 엔진 연결됨"
              : engineState === "connecting"
                ? "엔진 연결 중"
                : "엔진 연결 대기"}
          </span>
        </header>
        <div className="page-content">
          <header className="intro">
            <h1>사진 정리</h1>
          </header>
          <details className="source-picker" open>
            <summary className="source-summary">
              <strong>
                {(list?.total_days ?? 0) > 0
                  ? `스캔한 폴더: ${scan?.source ?? "선택한 폴더"}`
                  : "정리할 폴더 선택"}
              </strong>
              <span>
                {scan?.state === "complete"
                  ? `스캔 ${rate(scan.rate_per_second ?? 0)}파일/초`
                  : (list?.total_days ?? 0) > 0 ? "다른 폴더 스캔" : ""}
              </span>
            </summary>
            <div className="source-body">
              <div className="source-controls">
                <span className="source-label">스캔할 폴더</span>
                <button
                  type="button"
                  className="selected-source"
                  disabled={engineState !== "ready" || !!busy}
                  onClick={() => void pickSource()}
                  title="macOS 폴더 선택창 열기"
                >
                  {busy === "pick-folder" ? "macOS 폴더 선택창 대기 중…" : selectedSource || "폴더를 선택하세요"}
                </button>
                <button
                  className="button button-primary"
                  disabled={
                    !selectedSource ||
                    !!busy ||
                    scan?.state === "running" ||
                    batch?.state === "running"
                  }
                  onClick={startScan}
                >
                  {scan?.state === "running" ? "스캔 중…" : "선택한 폴더 스캔"}
                </button>
              </div>
              {scan?.state === "running" && (
                <div className="scan-progress" role="status">
                  <span className="scan-spinner" />
                  <span>
                    <b>{scan.source} 스캔 중</b>
                    <small>
                      확인한 파일 {num(scan.scanned_files)}개 · 찾은 미디어{" "}
                      {num(scan.found_media)}개 · 평균 {rate(scan.rate_per_second ?? 0)}파일/초
                      {scan.elapsed_seconds > 0 ? ` · ${scan.elapsed_seconds.toFixed(1)}초 경과` : ""}
                    </small>
                  </span>
                </div>
              )}
            </div>
          </details>
          <details className="home-settings">
            <summary className="source-summary">
              <strong>집 기준 지역</strong>
              <span>{config?.home_location || "설정되지 않음"}</span>
            </summary>
            <div className="source-body">
              <div className="home-controls">
                <label htmlFor="home-location">집 지역</label>
                <input
                  id="home-location"
                  value={homeDraft}
                  onChange={(event) => setHomeDraft(event.target.value)}
                  placeholder="예: 서울특별시"
                  maxLength={80}
                  disabled={engineState !== "ready" || batch?.state === "running"}
                />
                <button
                  className="button button-secondary"
                  disabled={engineState !== "ready" || !!busy || batch?.state === "running" || homeDraft.trim() === config?.home_location}
                  onClick={() => void run("home", async () => {
                    const saved = await api<{ home_location: string }>("/api/home", { location: homeDraft });
                    setConfig((current) => current ? { ...current, home_location: saved.home_location } : current);
                    setHomeDraft(saved.home_location);
                    setNotice("집 기준 지역을 저장했습니다. 기존 AI 제안은 자동으로 다시 분류하지 않습니다.");
                  })}
                >
                  {busy === "home" ? "저장 중…" : "지역 저장"}
                </button>
              </div>
            </div>
          </details>
          <div className="feedback" aria-live="polite">
            {error && (
              <p className="error-message" role="alert">
                {error}
              </p>
            )}
            {notice && <p className="success-message">{notice}</p>}
          </div>
          <section className="summary-grid" aria-label="검토 현황">
            <article className="summary-card">
              <small>전체 파일</small>
              <strong>{num(list?.total_files ?? 0)}</strong>
              <span>날짜 {num(list?.total_days ?? 0)}개</span>
            </article>
            <article className="summary-card">
              <small>AI 분류 완료</small>
              <strong>{num(list?.classified_days ?? 0)}</strong>
            </article>
            <article className="summary-card">
              <small>승인 대기</small>
              <strong>{num(list?.pending_days ?? 0)}</strong>
            </article>
            <article className="summary-card">
              <small>이동 완료</small>
              <strong>{num(list?.moved_files ?? 0)}</strong>
            </article>
          </section>
          <div className="library-head" id="review-list">
            <div>
              <h2>날짜별 미디어</h2>
            </div>
          </div>
          <div className="review-toolbar">
            <div className="filter-group" aria-label="상태 필터">
              {(
                [
                  ["all", "전체"],
                  ["unclassified", "AI 미분류"],
                  ["pending", "승인 대기"],
                  ["approved", "승인됨"],
                  ["moved", "이동 완료"],
                ] as const
              ).map(([value, label]) => (
                <button
                  key={value}
                  className={filter === value ? "filter active" : "filter"}
                  aria-pressed={filter === value}
                  onClick={() => {
                    setFilter(value);
                    setDayPage(1);
                    setFilePage(1);
                  }}
                >
                  {label}
                </button>
              ))}
            </div>
            <div className="month-filter" ref={monthPickerRef}>
              <span className="month-filter-label">기간</span>
              <button
                ref={monthTriggerRef}
                type="button"
                className="month-trigger"
                aria-label="월로 날짜 좁히기"
                aria-expanded={monthPickerOpen}
                aria-controls="month-picker"
                onClick={() => {
                  setPickerYear(Number(month.slice(0, 4)) || new Date().getFullYear());
                  setMonthPickerOpen((open) => !open);
                }}
              >
                <span>{month ? `${Number(month.slice(0, 4))}년 ${Number(month.slice(5))}월` : "전체 기간"}</span>
                <svg aria-hidden="true" viewBox="0 0 20 20" fill="none"><path d="M4 5.5h12v11H4zM6.5 3.5v4m7-4v4M4 9h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>
              </button>
              {monthPickerOpen && (
                <div className="month-popover" id="month-picker" role="group" aria-label="월 선택">
                  <div className="month-popover-head">
                    <button type="button" aria-label="이전 연도" onClick={() => setPickerYear((year) => year - 1)}>‹</button>
                    <strong>{pickerYear}년</strong>
                    <button type="button" aria-label="다음 연도" onClick={() => setPickerYear((year) => year + 1)}>›</button>
                  </div>
                  <div className="month-grid">
                    {Array.from({ length: 12 }, (_, index) => {
                      const value = `${pickerYear}-${String(index + 1).padStart(2, "0")}`;
                      return (
                        <button
                          key={value}
                          type="button"
                          className={month === value ? "selected" : ""}
                          aria-pressed={month === value}
                          onClick={() => chooseMonth(value)}
                        >
                          {index + 1}월
                        </button>
                      );
                    })}
                  </div>
                  <button type="button" className="month-all" onClick={() => chooseMonth("")}>
                    전체 기간 보기
                  </button>
                </div>
              )}
            </div>
          </div>
          {(list?.total_files ?? 0) > 0 && (
            <div className="batch-bar">
              <div>
                <b>날짜별 AI 분류</b>
                {(batch?.state === "complete" || batch?.state === "stopped") && (
                  <span>최근 {rate(batch.rate_per_second ?? 0)}날짜/초 · {(batch.elapsed_seconds ?? 0).toFixed(1)}초</span>
                )}
              </div>
              <button
                className="button button-secondary"
                disabled={
                  !!busy ||
                  batch?.state === "running" ||
                  scan?.state === "running" ||
                  (list?.unclassified_days ?? 0) === 0
                }
                onClick={startBatch}
              >
                {busy === "batch" ? "시작 중…" : "미분류 날짜 AI 분류"}
              </button>
            </div>
          )}
          {batch?.state === "running" && (
            <div className="batch-progress" role="status">
              <div>
                <b>
                  날짜 AI 분류 진행 중 · {num(batch.done)} / {num(batch.total)}
                </b>
                <span>
                  실패 {num(batch.failed)}개 · 평균 {rate(batch.rate_per_second ?? 0)}날짜/초
                  {batch.elapsed_seconds > 0 ? ` · ${batch.elapsed_seconds.toFixed(1)}초 경과` : ""}
                </span>
              </div>
              <progress value={batch.done} max={batch.total} />
              <button
                className="button button-secondary"
                onClick={() =>
                  void run("stop", async () => {
                    await api("/api/classify/batch/stop", {});
                    setNotice("분류 중단을 요청했습니다.");
                  })
                }
                disabled={busy === "stop"}
              >
                분류 중단
              </button>
            </div>
          )}
          {batch && batch.failed > 0 && (
            <div className="batch-errors" aria-live="polite">
              <b>분류 실패 {num(batch.failed)}건</b>
              {batch.failures?.length ? (
                <ul>
                  {batch.failures.map((failure, index) => (
                    <li key={`${failure.date ?? "unknown"}-${index}`}>
                      {failure.date && <strong>{failure.date}</strong>}
                      <span>{failure.reason}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p>{batch.last_error ?? "오류 상세 정보가 없습니다."}</p>
              )}
            </div>
          )}
          {(list?.total_days ?? 0) === 0 ? (
            <section className="empty-card">
              <h3>
                {engineState === "offline"
                  ? "Python 엔진 연결을 확인해 주세요"
                  : "아직 검토할 파일이 없습니다"}
              </h3>
              <p>
                {engineState === "offline"
                  ? "README의 로컬 실행 안내를 확인하세요."
                  : "폴더를 선택해 스캔하거나 필터를 바꿔 주세요."}
              </p>
            </section>
          ) : (
            <div className="review-layout">
              <section className="date-rail" aria-label="날짜 목록">
                <div className="rail-heading">
                  <b>날짜 목록</b>
                  <small>결과 {num(list?.total ?? 0)}개</small>
                </div>
                <div className="date-items">
                  {list?.items.map((item) => (
                    <div className={`date-row ${selectedId === item.id ? "active" : ""}`} key={item.id}>
                      <button
                        className="date-item"
                        aria-pressed={selectedId === item.id}
                        onClick={() => {
                          setSelectedId(item.id);
                          setFilePage(1);
                          if (editingFolderId !== item.id) setEditingFolderId(null);
                        }}
                      >
                        <span>
                          <b>{dateLabel(item.date)}</b>
                          <small>
                            {num(item.file_count)}개 · {item.suggested_category
                              ? "AI 완료"
                              : item.edited_category ? "직접 입력" : "AI 미분류"}
                            {item.corrected_files > 0 && " · 날짜 보정"}
                          </small>
                          <code className="date-folder-name" title={dayCategory(item)
                            ? pathFor(item.date, dayCategory(item))
                            : "카테고리를 입력하면 경로가 정해집니다."}>
                            {folderNameFor(item.date, dayCategory(item))}
                          </code>
                        </span>
                        <em className={item.moved ? "done" : item.approved_category ? "ok" : ""}>
                          {item.moved ? "이동 완료" : item.approved_category ? "승인됨" : "대기"}
                        </em>
                      </button>
                      {!item.moved && (
                        <button
                          className="date-edit-button"
                          aria-label={`${dateLabel(item.date)} 폴더 선택 또는 편집`}
                          aria-expanded={editingFolderId === item.id}
                          disabled={!!busy || scan?.state === "running" || batch?.state === "running"}
                          onClick={() => editingFolderId === item.id
                            ? setEditingFolderId(null)
                            : void editFolder(item)}
                        >
                          폴더 선택
                        </button>
                      )}
                      {editingFolderId === item.id && (
                        <form className="date-folder-editor" onSubmit={(event) => {
                          event.preventDefault();
                          saveFolder(item);
                        }}>
                          <label htmlFor={`folder-choice-${item.id}`}>기존 날짜 폴더 선택</label>
                          <select
                            id={`folder-choice-${item.id}`}
                            value={folderOptions?.dayId === item.id && folderOptions.items.some(
                              (option) => option.category === folderDraft
                            ) ? folderDraft : ""}
                            onChange={(event) => {
                              if (event.target.value) setFolderDraft(event.target.value);
                            }}
                            disabled={folderLoadingId === item.id || !!busy}
                          >
                            <option value="">{folderLoadingId === item.id
                              ? "기존 폴더 확인 중…"
                              : folderOptions?.dayId === item.id && folderOptions.items.length === 0
                                ? "기존 폴더 없음 · 직접 입력"
                                : "직접 입력 또는 AI 제안 사용"}</option>
                            {folderOptions?.dayId === item.id && folderOptions.items.map((option) => (
                              <option key={option.path} value={option.category}>{option.name}</option>
                            ))}
                          </select>
                          {folderLoadError && <small role="status">{folderLoadError}</small>}
                          <label htmlFor={`folder-category-${item.id}`}>폴더명</label>
                          <div className="date-folder-input">
                            <span>{item.date.slice(2).replaceAll("-", "")}_</span>
                            <input
                              id={`folder-category-${item.id}`}
                              value={folderDraft}
                              onChange={(event) => setFolderDraft(event.target.value)}
                              onKeyDown={(event) => {
                                if (event.key === "Escape") setEditingFolderId(null);
                              }}
                              maxLength={40}
                              placeholder="카테고리 입력"
                              autoFocus
                            />
                          </div>
                          {folderDraft.trim() && (
                            <small className="date-folder-preview">
                              {folderOptions?.dayId === item.id && folderOptions.items.some(
                                (option) => option.category === folderDraft.trim()
                              ) ? "기존 폴더" : "새 폴더 제안"} · {pathFor(item.date, folderDraft.trim())}
                            </small>
                          )}
                          {item.approved_category && folderDraft !== item.approved_category && (
                            <small>이름을 바꾸면 기존 경로 승인이 해제됩니다.</small>
                          )}
                          <div className="date-folder-actions">
                            <button type="button" onClick={() => setEditingFolderId(null)}>취소</button>
                            <button type="submit" disabled={!!busy || !folderDraft.trim() || folderDraft.trim() === dayCategory(item)}>
                              {busy === "save-folder" ? "저장 중…" : "저장"}
                            </button>
                          </div>
                        </form>
                      )}
                    </div>
                  ))}
                </div>
                <div className="pager">
                  <button
                    disabled={dayPage <= 1}
                    onClick={() => {
                      setDayPage(dayPage - 1);
                      setFilePage(1);
                    }}
                  >
                    이전
                  </button>
                  <span>
                    {dayPage} / {dayPages}
                  </span>
                  <button
                    disabled={dayPage >= dayPages}
                    onClick={() => {
                      setDayPage(dayPage + 1);
                      setFilePage(1);
                    }}
                  >
                    다음
                  </button>
                </div>
              </section>
              <section className="day-detail" aria-label="선택한 날짜의 파일">
                {day ? (
                  <>
                    <header className="detail-heading">
                      <div>
                        <h3>{dateLabel(day.date)}</h3>
                        <p>
                          전체 {num(day.file_count)}개 · {day.sample_count > 0
                            ? `AI 대표 ${num(day.sample_count)}개 사용`
                            : "날짜 카테고리 미분류"}
                          {day.corrected_files > 0 && ` · 날짜 보정 ${num(day.corrected_files)}개`}
                        </p>
                      </div>
                      <span
                        className={`status-badge ${day.moved ? "moved" : day.approved_category ? "approved" : ""}`}
                      >
                        {day.moved
                          ? "이동 완료"
                          : day.approved_category
                            ? "경로 승인됨"
                            : "검토 중"}
                      </span>
                    </header>
                    <div className="detail-body">
                      <div className="detail-tools">
                        <div className="step-heading">
                          <span className="step-number">1</span>
                          <div>
                            <h4>날짜 확인</h4>
                          </div>
                        </div>
                        <button
                          className="button button-secondary"
                          disabled={
                            !!busy ||
                            day.moved ||
                            !!day.approved_category ||
                            batch?.state === "running" ||
                            scan?.state === "running"
                          }
                          onClick={() => void run("classify-day", async () => {
                            await api("/api/classify", { day_id: day.id });
                            await refresh();
                            setNotice(`${day.date} 날짜의 AI 카테고리 제안을 받았습니다. 경로는 승인되지 않았습니다.`);
                          })}
                        >
                          {busy === "classify-day" ? "분류 중…" : day.suggested_category ? "날짜 다시 AI 분류" : "이 날짜 AI 분류"}
                        </button>
                      </div>
                      <ul className="media-list">
                        {shownFiles?.items.map((file) => (
                            <li className="media-card" key={file.id}>
                              {day.moved ? (
                                <span
                                  className="media-preview moved-preview"
                                  aria-label="이동 완료"
                                >
                                  ✓
                                </span>
                              ) : (
                                <button type="button" className="media-open"
                                  aria-label={`${file.name} ${file.kind === "video" ? "재생" : "보기"}`}
                                  onClick={() => setViewer({ dayId: day.id, file })}>
                                  <span className="media-thumb">
                                    <img className="media-preview"
                                      src={`${ENGINE_ORIGIN}/api/preview/${day.id}/${file.id}`}
                                      alt="" loading="lazy" />
                                    {file.kind === "video" && <span className="media-play-icon" aria-hidden="true">▶</span>}
                                  </span>
                                  <MediaDetails file={file} />
                                </button>
                              )}
                              {day.moved && <MediaDetails file={file} />}
                              {file.sampled && <div className="media-action"><span className="category-chip has-category">AI 대표</span></div>}
                            </li>
                        ))}
                      </ul>
                      {shownFiles && (
                        <div className="pager file-pager">
                          <button
                            disabled={filePage <= 1}
                            onClick={() => setFilePage(filePage - 1)}
                          >
                            이전 파일
                          </button>
                          <span>
                            {filePage} / {filePages} 페이지 · 총{" "}
                            {num(shownFiles.total)}개
                          </span>
                          <button
                            disabled={filePage >= filePages}
                            onClick={() => setFilePage(filePage + 1)}
                          >
                            다음 파일
                          </button>
                        </div>
                      )}
                    </div>
                    <div className="action-row">
                      <section className="approval-panel">
                        <div className="step-heading">
                          <span className="step-number">2</span>
                          <div>
                            <h4>날짜 카테고리 편집</h4>
                          </div>
                        </div>
                        {day.suggested_category && !day.approved_category && (
                          <p className="recent-suggestion">
                            AI 제안: <b>{day.suggested_category}</b> · 대표 파일 {num(day.sample_count)}개 검토
                          </p>
                        )}
                        {day.gps_place && (
                          <p className="recent-suggestion">대표 파일 GPS 가까운 지역: {day.gps_place}</p>
                        )}
                        {day.visual_place && (
                          <p className="recent-suggestion" title={day.visual_evidence || undefined}>
                            AI 추정 장소: {day.visual_place}
                            {day.visual_evidence ? ` · 근거: ${day.visual_evidence}` : ""}
                          </p>
                        )}
                        <label htmlFor="category">이 날짜의 카테고리</label>
                        <div className="approval-controls">
                          <input
                            id="category"
                            value={draft}
                            onChange={(event) => { setDraft(event.target.value); setDraftEdited(true); }}
                            disabled={day.moved}
                            placeholder="예: 여행"
                          />
                          <button
                            className="button button-secondary"
                            disabled={
                              !!busy ||
                              day.moved ||
                              !draft.trim() ||
                              draftMatchesSaved ||
                              scan?.state === "running" ||
                              batch?.state === "running"
                            }
                            onClick={() =>
                              void run("save-category", async () => {
                                await api("/api/category", {
                                  day_id: day.id,
                                  category: draft,
                                });
                                setDraftEdited(false);
                                await refresh();
                                setNotice(`${day.date} 카테고리를 저장했습니다. 경로는 아직 승인되지 않았습니다.`);
                              })
                            }
                          >
                            {busy === "save-category" ? "저장 중…" : "카테고리 저장"}
                          </button>
                        </div>
                        <div className="path-preview">
                          <small>
                            {draftMatchesApproval && day.destination
                              ? "승인된 경로"
                              : "제안 경로"}
                          </small>
                          <code>{pathFor(day.date, draft)}</code>
                        </div>
                        <button
                          className="button button-primary approval-submit"
                          disabled={!!busy || day.moved || !draft.trim() || !draftMatchesSaved ||
                            draftMatchesApproval || scan?.state === "running" || batch?.state === "running"}
                          onClick={() => void run("approve", async () => {
                            await api("/api/approve", { day_id: day.id, category: draft });
                            setDraftEdited(false);
                            await refresh();
                            setNotice(`${day.date}의 경로를 승인했습니다. 파일은 아직 이동하지 않았습니다.`);
                          })}
                        >
                          {busy === "approve" ? "승인 중…" : "경로 승인"}
                        </button>
                      </section>
                      <section className="move-panel">
                        <div className="step-heading">
                          <span className="step-number">3</span>
                          <div>
                            <h4>파일 이동</h4>
                          </div>
                        </div>
                        <button
                          className="button button-move"
                          disabled={
                            !!busy ||
                            !day.approved_category ||
                            day.moved ||
                            !draftMatchesApproval ||
                            scan?.state === "running" ||
                            batch?.state === "running"
                          }
                          onClick={() => {
                            if (
                              !window.confirm(
                                `${num(day.file_count)}개 파일을 ${day.destination}로 이동할까요?`,
                              )
                            )
                              return;
                            void run("move", async () => {
                              await api("/api/move", { day_id: day.id });
                              await refresh();
                              setNotice(
                                `${day.date}의 파일 ${num(day.file_count)}개를 이동했습니다.`,
                              );
                            });
                          }}
                        >
                          {busy === "move"
                            ? "이동 중…"
                            : day.moved
                              ? "이동 완료"
                              : `${num(day.file_count)}개 파일 이동 실행`}
                        </button>
                      </section>
                    </div>
                  </>
                ) : (
                  <p className="detail-placeholder">
                    왼쪽에서 날짜를 선택해 주세요.
                  </p>
                )}
              </section>
            </div>
          )}
        </div>
      </section>
      <dialog ref={viewerRef} className="media-dialog" aria-label="미디어 보기"
        onClose={() => setViewer(null)}
        onClick={(event) => { if (event.target === event.currentTarget) event.currentTarget.close(); }}>
        {viewer && (
          <div className="media-dialog-content">
            <header className="media-dialog-header">
              <div>
                <strong title={viewer.file.name}>{viewer.file.name}</strong>
                <small>{viewer.file.kind === "video" ? "영상" : "이미지"} · {viewer.file.captured_at}</small>
              </div>
              <button type="button" className="media-dialog-close" aria-label="미디어 닫기"
                onClick={() => viewerRef.current?.close()}>×</button>
            </header>
            <div className="media-dialog-stage">
              {viewer.file.kind === "video" ? (
                <video key={`${viewer.dayId}-${viewer.file.id}`} controls playsInline preload="metadata"
                  poster={`${ENGINE_ORIGIN}/api/preview/${viewer.dayId}/${viewer.file.id}`}
                  src={`${ENGINE_ORIGIN}/api/media/${viewer.dayId}/${viewer.file.id}`} />
              ) : (
                <img src={viewableImage(viewer.file.name)
                  ? `${ENGINE_ORIGIN}/api/media/${viewer.dayId}/${viewer.file.id}`
                  : `${ENGINE_ORIGIN}/api/preview/${viewer.dayId}/${viewer.file.id}?large=true`}
                  alt={viewer.file.name} />
              )}
            </div>
            {viewer.file.relative_path !== viewer.file.name && (
              <p className="media-dialog-path">{viewer.file.relative_path}</p>
            )}
          </div>
        )}
      </dialog>
    </main>
  );
}

function viewableImage(name: string): boolean {
  return /\.(jpe?g|png|webp)$/i.test(name);
}

function MediaDetails({ file }: { file: Media }) {
  return <span className="media-details">
    <b title={file.name}>{file.name}</b>
    {file.relative_path !== file.name && <small title={file.relative_path}>{file.relative_path}</small>}
    <span>{file.kind === "video" ? "영상" : "이미지"} · {file.captured_at}</span>
    {file.original_captured_at && <span>원본 시각 {file.original_captured_at}</span>}
    {file.gps && <span>GPS {file.gps.latitude.toFixed(4)}, {file.gps.longitude.toFixed(4)}
      {file.gps_place ? ` · GPS 가까운 지역: ${file.gps_place}` : ""}</span>}
  </span>;
}

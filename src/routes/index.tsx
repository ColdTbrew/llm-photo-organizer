import { createFileRoute } from '@tanstack/react-router'
import { useEffect, useState } from 'react'

type EngineConfig = {
  photo_root: string
  model_api: string
  model_name: string
  video_frame_limit: number
}

type Source = { name: string; path: string; is_directory: boolean }
const ENGINE_ORIGIN = import.meta.env.VITE_ENGINE_ORIGIN ?? 'http://127.0.0.1:8040'

export const Route = createFileRoute('/')({ component: Home })

function Home() {
  const [config, setConfig] = useState<EngineConfig | null>(null)
  const [sources, setSources] = useState<Source[]>([])
  const [engineState, setEngineState] = useState<'connecting' | 'ready' | 'offline'>('connecting')

  useEffect(() => {
    let active = true
    Promise.all([
      fetch(`${ENGINE_ORIGIN}/api/config`).then((response) => {
        if (!response.ok) throw new Error('engine unavailable')
        return response.json() as Promise<EngineConfig>
      }),
      fetch(`${ENGINE_ORIGIN}/api/sources`).then((response) => {
        if (!response.ok) throw new Error('engine unavailable')
        return response.json() as Promise<Source[]>
      }),
    ])
      .then(([nextConfig, nextSources]) => {
        if (!active) return
        setConfig(nextConfig)
        setSources(nextSources)
        setEngineState('ready')
      })
      .catch(() => active && setEngineState('offline'))
    return () => { active = false }
  }, [])

  const directories = sources.filter((source) => source.is_directory)

  return (
    <main className="workspace">
      <aside className="sidebar">
        <a className="brand" href="/" aria-label="LLM Photo Organizer 홈">
          <span className="brand-mark">x</span>
          <span><b>LLM Photos</b><small>PHOTO ORGANIZER</small></span>
        </a>
        <div className="drive-tile">
          <span className="drive-glyph">▤</span>
          <span><b>X31 라이브러리</b><small>{config?.photo_root ?? '/Volumes/X31'}</small></span>
          <i className={engineState === 'ready' ? 'dot online' : 'dot'} />
        </div>
        <p className="nav-heading">워크스페이스</p>
        <a className="nav-link active" href="#review"><span>▦</span> 분류 검토 <b>01</b></a>
        <div className="sidebar-note">
          <span className="note-icon">✳</span>
          <small>로컬 분류 엔진</small>
          <b>Rust · Axum</b>
          <span>{engineState === 'ready' ? '엔진 연결됨' : engineState === 'offline' ? '엔진 시작 대기' : '연결 확인 중'}</span>
        </div>
      </aside>

      <section className="main-panel" id="review">
        <header className="topbar"><span>라이브러리 <i>/</i> <b>분류 검토</b></span><span className="engine-status"><i className={engineState === 'ready' ? 'dot online' : 'dot'} /> {engineState === 'ready' ? '로컬 엔진 연결됨' : engineState === 'offline' ? '엔진 연결 대기' : '연결 중'}</span></header>
        <div className="page-content">
          <section className="intro">
            <div><label>YOUR PERSONAL ARCHIVE</label><h1>분류 검토</h1><p>날짜별 미디어를 확인하고 정리 경로를 승인합니다.</p></div>
            <button className="primary-button" disabled={engineState !== 'ready'}><span>＋</span> X31 폴더 선택</button>
          </section>

          <div className="privacy-banner"><span>i</span><p><b>원본은 이동을 승인할 때까지 그대로 보관됩니다.</b><br />폴더 스캔은 선택 후 시작하며, 날짜별 경로를 승인한 다음 이동을 직접 실행합니다.</p></div>

          <section className="summary-grid">
            <article className="summary-card"><small>미디어 폴더</small><strong>{directories.length || '—'}</strong><span>선택할 수 있는 폴더</span></article>
            <article className="summary-card"><small>AI 분류</small><strong>준비 중</strong><span>{config?.model_name ?? 'MiniCPM-V-4.6'}</span></article>
            <article className="summary-card"><small>영상 프레임</small><strong>{config?.video_frame_limit ?? 32}</strong><span>요청당 최대 프레임</span></article>
            <article className="summary-card summary-accent"><small>엔진 상태</small><strong>{engineState === 'ready' ? '연결됨' : engineState === 'offline' ? '대기 중' : '확인 중'}</strong><span>Rust API · 127.0.0.1:8040</span></article>
          </section>

          <section className="library-head"><div><h2>날짜별 미디어</h2><p>분류 결과와 목적지 경로를 함께 검토합니다.</p></div><div className="filter-button">검토 대기 <span>⌄</span></div></section>
          <section className="empty-card">
            <div className="stacked-photos"><span>▧</span><span>▧</span><span>✳</span></div>
            <h3>{engineState === 'offline' ? 'Rust 엔진을 시작해 주세요' : '검토할 미디어가 아직 없습니다'}</h3>
            <p>{engineState === 'offline' ? '프로젝트 README의 로컬 실행 안내를 확인하세요.' : 'X31에서 폴더를 선택하면 원본을 옮기지 않고 날짜별로 살펴봅니다.'}</p>
            {directories.length > 0 && <div className="folder-chips">{directories.slice(0, 5).map((source) => <span key={source.path}>▰ {source.name}</span>)}</div>}
          </section>
          <footer className="footer-note"><span>◈</span> 사진 미리보기는 Mac에서 제공하고, 선택한 이미지와 영상 프레임만 Spark vLLM API로 전송합니다.</footer>
        </div>
      </section>
    </main>
  )
}

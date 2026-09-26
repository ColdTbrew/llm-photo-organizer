use std::{env, net::SocketAddr, path::PathBuf, sync::Arc};

use axum::{extract::State, http::{header, HeaderValue, Method}, routing::get, Json, Router};
use serde::Serialize;
use tokio::net::TcpListener;
use tower_http::{cors::CorsLayer, trace::TraceLayer};

#[derive(Clone)]
struct AppState {
    settings: Arc<Settings>,
}

struct Settings {
    photo_root: PathBuf,
    model_api: String,
    model_name: String,
    bind_addr: SocketAddr,
}

#[derive(Serialize)]
struct PublicConfig {
    photo_root: String,
    model_api: String,
    model_name: String,
    video_frame_limit: u8,
}

#[derive(Serialize)]
struct SourceEntry {
    name: String,
    path: String,
    is_directory: bool,
}

fn settings_from_env() -> Result<Settings, Box<dyn std::error::Error>> {
    let port = env::var("ENGINE_PORT")
        .unwrap_or_else(|_| "8040".to_owned())
        .parse::<u16>()?;
    let bind_ip = env::var("ENGINE_BIND").unwrap_or_else(|_| "127.0.0.1".to_owned());
    Ok(Settings {
        photo_root: env::var_os("PHOTO_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/Volumes/X31")),
        model_api: env::var("MODEL_API")
            .unwrap_or_else(|_| "http://127.0.0.1:8000/v1".to_owned()),
        model_name: env::var("MODEL_NAME")
            .unwrap_or_else(|_| "openbmb/MiniCPM-V-4.6".to_owned()),
        bind_addr: format!("{bind_ip}:{port}").parse()?,
    })
}

async fn health() -> &'static str {
    "ok"
}

async fn config(State(state): State<AppState>) -> Json<PublicConfig> {
    Json(PublicConfig {
        photo_root: state.settings.photo_root.to_string_lossy().into_owned(),
        model_api: state.settings.model_api.clone(),
        model_name: state.settings.model_name.clone(),
        video_frame_limit: 32,
    })
}

async fn sources(State(state): State<AppState>) -> Json<Vec<SourceEntry>> {
    let mut result = Vec::new();
    let Ok(mut entries) = tokio::fs::read_dir(&state.settings.photo_root).await else {
        return Json(result);
    };

    while let Ok(Some(entry)) = entries.next_entry().await {
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with('.') || is_system_directory(&name) {
            continue;
        }
        let Ok(file_type) = entry.file_type().await else {
            continue;
        };
        result.push(SourceEntry {
            name,
            path: entry.path().to_string_lossy().into_owned(),
            is_directory: file_type.is_dir(),
        });
    }

    result.sort_by(|a, b| {
        b.is_directory
            .cmp(&a.is_directory)
            .then_with(|| a.name.to_lowercase().cmp(&b.name.to_lowercase()))
    });
    Json(result)
}

fn is_system_directory(name: &str) -> bool {
    matches!(
        name.to_lowercase().as_str(),
        "$recycle.bin" | ".spotlight-v100" | ".trashes" | ".fseventsd" | "system volume information" | "lost+found"
    )
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    tracing_subscriber::fmt()
        .with_env_filter(env::var("RUST_LOG").unwrap_or_else(|_| "llm_photo_organizer_engine=info,tower_http=info".into()))
        .init();

    let settings = Arc::new(settings_from_env()?);
    let bind_addr = settings.bind_addr;
    let state = AppState { settings };
    let cors = CorsLayer::new()
        .allow_origin([
            HeaderValue::from_static("http://127.0.0.1:3000"),
            HeaderValue::from_static("http://localhost:3000"),
        ])
        .allow_methods([Method::GET, Method::POST, Method::PATCH])
        .allow_headers([header::CONTENT_TYPE]);
    let app = Router::new()
        .route("/health", get(health))
        .route("/api/config", get(config))
        .route("/api/sources", get(sources))
        .with_state(state)
        .layer(cors)
        .layer(TraceLayer::new_for_http());

    let listener = TcpListener::bind(bind_addr).await?;
    tracing::info!(%bind_addr, "X31 Rust engine listening");
    axum::serve(listener, app).await?;
    Ok(())
}

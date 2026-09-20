use std::io::{BufRead, BufReader};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::thread;
use std::time::{Duration, Instant};

use tauri::Manager;

struct BackendChild(Mutex<Option<Child>>);

fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x08000000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    #[cfg(not(windows))]
    {
        let _ = cmd;
    }
}

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(|p| p.to_path_buf())
        .unwrap_or_else(|| PathBuf::from("."))
}

fn spawn_dev_backend() -> std::io::Result<Child> {
    let backend = repo_root().join("backend");
    let mut cmd = Command::new("poetry");
    cmd.current_dir(&backend)
        .args([
            "run",
            "python",
            "-m",
            "uvicorn",
            "main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000",
        ])
        .env("ADA_PACKAGED", "0")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut cmd);
    cmd.spawn()
}

fn sidecar_exe(app: &tauri::AppHandle) -> Option<PathBuf> {
    if let Ok(res) = app.path().resource_dir() {
        let candidates = [
            res.join("jarvis-backend.exe"),
            res.join("binaries").join("jarvis-backend.exe"),
            res.join("jarvis-backend"),
            res.join("ada-backend.exe"),
            res.join("binaries").join("ada-backend.exe"),
            res.join("ada-backend"),
        ];
        for c in candidates {
            if c.exists() {
                return Some(c);
            }
        }
    }
    if let Ok(exe) = app.path().executable_dir() {
        for name in ["jarvis-backend.exe", "ada-backend.exe"] {
            let c = exe.join(name);
            if c.exists() {
                return Some(c);
            }
        }
    }
    None
}

fn spawn_packaged_backend(app: &tauri::AppHandle) -> std::io::Result<Child> {
    let exe = sidecar_exe(app).ok_or_else(|| {
        std::io::Error::new(std::io::ErrorKind::NotFound, "jarvis-backend sidecar not found")
    })?;
    let mut cmd = Command::new(exe);
    cmd.env("ADA_PACKAGED", "1")
        .env("PORT", "8000")
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    hide_console(&mut cmd);
    cmd.spawn()
}

fn wait_for_health(timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if let Ok(resp) = std::net::TcpStream::connect_timeout(
            &"127.0.0.1:8000".parse().unwrap(),
            Duration::from_millis(250),
        ) {
            drop(resp);
            return true;
        }
        thread::sleep(Duration::from_millis(200));
    }
    false
}

fn pipe_child_logs(child: &mut Child) {
    if let Some(out) = child.stdout.take() {
        thread::spawn(move || {
            let reader = BufReader::new(out);
            for line in reader.lines().flatten() {
                eprintln!("[jarvis-backend] {line}");
            }
        });
    }
    if let Some(err) = child.stderr.take() {
        thread::spawn(move || {
            let reader = BufReader::new(err);
            for line in reader.lines().flatten() {
                eprintln!("[jarvis-backend] {line}");
            }
        });
    }
}

#[tauri::command]
fn pick_workspace_folder() -> Option<String> {
    rfd::FileDialog::new()
        .set_title("Open project folder")
        .pick_folder()
        .map(|p| p.to_string_lossy().to_string())
}

fn api_token_path(packaged: bool) -> PathBuf {
    if packaged {
        let appdata = std::env::var("APPDATA")
            .or_else(|_| std::env::var("HOME"))
            .unwrap_or_else(|_| ".".into());
        let mut p = PathBuf::from(appdata);
        if cfg!(target_os = "windows") {
            p.push("Jarvis");
        } else if cfg!(target_os = "macos") {
            p.push("Library");
            p.push("Application Support");
            p.push("Jarvis");
        } else {
            p.push(".local");
            p.push("share");
            p.push("Jarvis");
        }
        p.push(".secrets");
        p.push("jarvis-api-token");
        p
    } else {
        repo_root().join(".secrets").join("jarvis-api-token")
    }
}

fn read_api_token() -> String {
    let packaged = !cfg!(debug_assertions);
    let primary = api_token_path(packaged);
    if let Ok(t) = std::fs::read_to_string(&primary) {
        let t = t.trim().to_string();
        if !t.is_empty() {
            return t;
        }
    }
    let legacy = primary.with_file_name("ada-api-token");
    std::fs::read_to_string(legacy)
        .unwrap_or_default()
        .trim()
        .to_string()
}

#[tauri::command]
fn backend_health_hint() -> String {
    "http://127.0.0.1:8000/health".into()
}

#[tauri::command]
fn api_token() -> String {
    read_api_token()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(BackendChild(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            pick_workspace_folder,
            backend_health_hint,
            api_token
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            let child_state = app.state::<BackendChild>();
            let spawned = if cfg!(debug_assertions) {
                spawn_dev_backend()
            } else {
                spawn_packaged_backend(&handle)
            };
            match spawned {
                Ok(mut child) => {
                    pipe_child_logs(&mut child);
                    *child_state.0.lock().unwrap() = Some(child);
                    let _ = wait_for_health(Duration::from_secs(45));
                }
                Err(e) => {
                    eprintln!("Failed to start Jarvis backend: {e}");
                }
            }
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building Jarvis")
        .run(|app, event| {
            if let tauri::RunEvent::Exit = event {
                if let Some(state) = app.try_state::<BackendChild>() {
                    if let Ok(mut guard) = state.0.lock() {
                        if let Some(mut child) = guard.take() {
                            let _ = child.kill();
                            let _ = child.wait();
                        }
                    }
                }
            }
        });
}

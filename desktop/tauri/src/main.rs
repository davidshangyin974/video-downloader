#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use std::{
    io::{BufRead, BufReader},
    process::{Child, Command, Stdio},
    sync::{mpsc, Mutex},
    thread,
    time::Duration,
};

use tauri::{Manager, RunEvent, State};

struct BackendProcess(Mutex<Option<Child>>);
struct BackendUrl(String);

#[tauri::command]
fn backend_url(backend_url: State<'_, BackendUrl>) -> String {
    backend_url.0.clone()
}

fn bundled_sidecar_path(app: &tauri::App) -> std::path::PathBuf {
    let executable = if cfg!(target_os = "windows") {
        "video-downloader-server.exe"
    } else {
        "video-downloader-server"
    };
    app.path()
        .resource_dir()
        .expect("could not locate application resources")
        .join("sidecar")
        .join(executable)
}

fn start_backend(app: &tauri::App) -> Result<(Child, String), Box<dyn std::error::Error>> {
    let mut child = Command::new(bundled_sidecar_path(app))
        .env("VIDEO_DOWNLOADER_PORT", "0")
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let stdout = child.stdout.take().ok_or("could not read local download service output")?;
    let (sender, receiver) = mpsc::sync_channel(1);

    thread::spawn(move || {
        for line in BufReader::new(stdout).lines() {
            match line {
                Ok(line) => {
                    if let Some(url) = line.strip_prefix("VIDEO_DOWNLOADER_API_BASE=") {
                        let _ = sender.send(Ok(url.to_owned()));
                        return;
                    }
                }
                Err(error) => {
                    let _ = sender.send(Err(error));
                    return;
                }
            }
        }
        let _ = sender.send(Err(std::io::Error::other("local download service stopped before it was ready")));
    });

    match receiver.recv_timeout(Duration::from_secs(15)) {
        Ok(Ok(url)) => Ok((child, url)),
        Ok(Err(error)) => {
            let _ = child.kill();
            Err(Box::new(error))
        }
        Err(error) => {
            let _ = child.kill();
            Err(Box::new(error))
        }
    }
}

fn main() {
    let application = tauri::Builder::default()
        .setup(|app| {
            let (child, url) = start_backend(app)?;
            app.manage(BackendProcess(Mutex::new(Some(child))));
            app.manage(BackendUrl(url));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![backend_url])
        .build(tauri::generate_context!())
        .expect("error while building Video Downloader");

    application.run(|app, event| {
        if matches!(event, RunEvent::Exit | RunEvent::ExitRequested { .. }) {
            if let Some(process) = app.try_state::<BackendProcess>() {
                if let Some(mut child) = process.0.lock().expect("backend child lock poisoned").take() {
                    let _ = child.kill();
                }
            }
        }
    });
}

import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { invoke } from '@tauri-apps/api/core'
import './styles.css'

async function start() {
  if ('__TAURI_INTERNALS__' in window) {
    window.__VIDEO_DOWNLOADER_API_BASE__ = await invoke<string>('backend_url')
  }
  const { default: App } = await import('./App')
  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
}

void start()

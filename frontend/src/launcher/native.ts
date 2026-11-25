// Commands of the desktop shell (feat/desktop). In a plain browser they fall back to the API, or say they need the desktop app.
import { api } from '../api'

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
// shortcut: Tauri's internal invoke, so the frontend needs no @tauri-apps/api; switch to that package if the desktop app adds it.
const invoke = (window as unknown as { __TAURI_INTERNALS__?: { invoke: Invoke } }).__TAURI_INTERNALS__?.invoke
const desktopOnly = () => Promise.reject(new Error('this works in the desktop app only'))

export const native = {
  openFile: (file_id: number, path: string, page: number) => (invoke ? invoke('open_file', { path, page }) : api.open(file_id, page)),
  reveal: (path: string) => (invoke ? invoke('reveal', { path }) : desktopOnly()),
  hide: () => (invoke ? invoke('hide_window') : Promise.resolve()),
  setAutostart: (enabled: boolean) => (invoke ? invoke('set_autostart', { enabled }) : desktopOnly()),
  setHotkey: (hotkey: string) => (invoke ? invoke('set_hotkey', { hotkey }) : desktopOnly()),
}

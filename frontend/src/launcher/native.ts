// Commands of the desktop shell (desktop/src-tauri/src/main.rs). In a plain browser they fall back to the API,
// to a download, or say they need the desktop app.
import { api, BASE, MOCK } from '../api'

type Invoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>
// shortcut: Tauri's internal invoke, so the frontend needs no @tauri-apps/api; switch to that package if it is ever added.
const invoke = (window as unknown as { __TAURI_INTERNALS__?: { invoke: Invoke } }).__TAURI_INTERNALS__?.invoke
export const inDesktop = !!invoke
const desktopOnly = () => Promise.reject(new Error('this works in the desktop app only'))

export const native = {
  openFile: (file_id: number, path: string, page: number) => (invoke ? invoke('open_file', { path, page }) : api.open(file_id, page)),
  reveal: (path: string) => (invoke ? invoke('reveal', { path }) : desktopOnly()),
  hide: () => (invoke ? invoke('hide_window') : Promise.resolve()),
  autostart: () => (invoke ? (invoke('autostart_enabled') as Promise<boolean>) : Promise.resolve(false)),
  setAutostart: (enabled: boolean) => (invoke ? invoke('set_autostart', { enabled }) : desktopOnly()),
  hotkey: () => (invoke ? (invoke('get_hotkey') as Promise<string>) : Promise.reject(new Error('desktop only'))),
  setHotkey: (hotkey: string) => (invoke ? invoke('set_hotkey', { hotkey }) : desktopOnly()),
  // why the global shortcut may not work here (a Wayland session), or null
  hotkeyNote: () => (invoke ? (invoke('hotkey_note') as Promise<string | null>) : Promise.resolve(null)),
  // The desktop app saves the .ics and opens it in the default calendar app; a browser downloads it.
  openIcs: (path: string) =>
    invoke ? invoke('open_ics', { url: path })
    : MOCK ? Promise.reject(new Error('there is no back end in mock mode'))
    : Promise.resolve(window.open(BASE + path, '_blank')),
}

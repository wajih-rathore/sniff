import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './launcher/launcher.css'
import Launcher from './launcher/Launcher'
import SetupGate from './views/Setup'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SetupGate>
      <Launcher />
    </SetupGate>
  </StrictMode>,
)

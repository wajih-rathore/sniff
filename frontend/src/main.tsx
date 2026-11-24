import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App'
import SetupGate from './views/Setup'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <SetupGate>
      <App />
    </SetupGate>
  </StrictMode>,
)

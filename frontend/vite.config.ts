import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Both UIs build until the launcher (launcher.html) replaces the old one.
  build: { rolldownOptions: { input: ['index.html', 'launcher.html'] } },
})

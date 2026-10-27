import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Détection de la cible API : dans Docker, le service se nomme 'api'
const apiTarget = process.env.VITE_API_TARGET || 'http://api:8000';

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 5173,
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        secure: false,
      },
      // Canal WebSocket des résultats Monte Carlo : même origine que la page, relayé vers l'API
      '/ws': {
        target: apiTarget,
        ws: true,
        changeOrigin: true,
      },
    }
  }
})

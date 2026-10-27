import { useState, useRef, useCallback } from 'react';

export function useRiskSimulation() {
  const [status, setStatus] = useState('idle');
  const [result, setResult] = useState(null);
  const [errorMessage, setErrorMessage] = useState(null);
  const ws = useRef(null);

  const startSimulation = useCallback((requestData) => {
    setResult(null);
    setErrorMessage(null);
    setStatus('loading');

    if (ws.current) {
      try {
        ws.current.close();
      } catch {
        // fermeture d'une connexion déjà close : rien à faire
      }
    }

    const currentClientId = typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `client-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;

    try {
      // Même origine que la page : le serveur Vite (ou un reverse proxy) relaie /ws vers l'API
      const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      ws.current = new WebSocket(`${wsProto}//${window.location.host}/ws/${currentClientId}`);

      ws.current.onopen = async () => {
        console.log("[WebSocket] Tunnel réseau établi avec succès. Prêt à écouter.");

        try {
          console.log("[HTTP POST] Expédition de la matrice d'actifs au cluster...");
          const response = await fetch('/api/v1/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...requestData,
              client_id: currentClientId
            }),
          });

          if (!response.ok) {
            const err = await response.json().catch(() => ({}));
            throw new Error(err.detail || `Rejet du serveur HTTP. Code : ${response.status}`);
          }
          console.log("[HTTP POST] Calcul en cours dans Celery...");
        } catch (error) {
          console.error("[HTTP POST] Faille de transmission :", error);
          setErrorMessage(error.message);
          setStatus('error');
          if (ws.current) ws.current.close();
        }
      };

      ws.current.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.status === 'success') {
            setResult(payload.data || payload.result || payload);
            setStatus('success');
            if (ws.current) ws.current.close();
          } else if (payload.status === 'error') {
            console.error("[WebSocket] Erreur d'exécution Celery :", payload.message);
            setErrorMessage(payload.message || "Erreur d'exécution du calcul.");
            setStatus('error');
            if (ws.current) ws.current.close();
          }
        } catch (error) {
          console.error("[WebSocket] Erreur critique de décodage JSON :", error);
        }
      };

      ws.current.onerror = (error) => {
        console.error("[WebSocket] Panne du canal asynchrone :", error);
        setErrorMessage("Connexion WebSocket impossible avec l'API.");
        setStatus('error');
      };

      ws.current.onclose = (event) => {
        console.log(`[WebSocket] Canal déconnecté. Code : ${event.code}`);
      };

    } catch (error) {
      console.error("[SYSTEM] Rupture de la chaîne :", error);
      setStatus('error');
      if (ws.current) ws.current.close();
    }
  }, []);

  return { startSimulation, status, result, errorMessage };
}
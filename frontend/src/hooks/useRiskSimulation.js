import { useState, useRef } from 'react';

export function useRiskSimulation() {
  const [status, setStatus] = useState('idle');
  const [result, setResult] = useState(null);
  const ws = useRef(null);

  const startSimulation = (requestData) => {
    setResult(null);
    setStatus('loading');

    if (ws.current) {
      ws.current.close();
    }

    const currentClientId = typeof crypto.randomUUID === 'function'
      ? crypto.randomUUID()
      : `client-${Date.now()}-${Math.random().toString(36).substring(2, 11)}`;

    try {
      ws.current = new WebSocket(`ws://localhost:8000/ws/${currentClientId}`);

      // LA CORRECTION EST ICI : L'événement devient asynchrone et gère le lancement
      ws.current.onopen = async () => {
        console.log("[WebSocket] Tunnel réseau établi avec succès. Prêt à écouter.");

        try {
          console.log("[HTTP POST] Expédition de la matrice d'actifs au cluster...");
          const response = await fetch('http://localhost:8000/api/v1/simulate', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              ...requestData,
              client_id: currentClientId
            }),
          });

          if (!response.ok) {
            throw new Error(`Rejet du serveur HTTP. Code : ${response.status}`);
          }
          console.log("[HTTP POST] Calcul en cours dans Celery...");
        } catch (error) {
          console.error("[HTTP POST] Faille de transmission :", error);
          setStatus('error');
          if (ws.current) ws.current.close();
        }
      };

      ws.current.onmessage = (event) => {
        console.log("[WebSocket] Flux brut reçu du serveur.");
        try {
          const payload = JSON.parse(event.data);
          if (payload.status === 'success') {
            setResult(payload.data || payload.result || payload);
            setStatus('success');
            if (ws.current) ws.current.close();
          } else if (payload.status === 'error') {
            console.error("[WebSocket] Erreur d'exécution Celery :", payload.message);
            setStatus('error');
            if (ws.current) ws.current.close();
          }
        } catch (error) {
          console.error("[WebSocket] Erreur critique de décodage JSON :", error);
        }
      };

      ws.current.onerror = (error) => {
        console.error("[WebSocket] Panne du canal asynchrone :", error);
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
  };

  return { startSimulation, status, result };
}
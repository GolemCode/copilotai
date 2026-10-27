import { useState, useEffect } from 'react';

const STORAGE_KEY = 'copilot_chat_history_v1';

const loadSavedMessages = () => {
    try {
        const saved = sessionStorage.getItem(STORAGE_KEY);
        if (saved) {
            const parsed = JSON.parse(saved);
            if (Array.isArray(parsed)) return parsed;
        }
    } catch (e) {
        console.warn("Impossible de charger l'historique de chat :", e);
    }
    return [];
};

export const useRagChat = () => {
    const [messages, setMessages] = useState(loadSavedMessages);
    const [isLoading, setIsLoading] = useState(false);
    const [error, setError] = useState(null);

    // Synchronisation automatique dans sessionStorage
    useEffect(() => {
        try {
            sessionStorage.setItem(STORAGE_KEY, JSON.stringify(messages));
        } catch (e) {
            console.warn("Impossible de sauvegarder l'historique de chat :", e);
        }
    }, [messages]);

    const sendMessage = async (query, portfolioContext = null) => {
        if (!query || !query.trim()) return;

        const userMessage = { role: 'user', content: query.trim() };
        setMessages((prev) => [...prev, userMessage]);

        setIsLoading(true);
        setError(null);

        try {
            const historyPayload = messages.slice(-6).map((m) => ({
                role: m.role,
                content: m.content
            }));

            const payload = {
                query: query.trim(),
                portfolio_context: portfolioContext,
                chat_history: historyPayload
            };

            const response = await fetch('/api/rag/ask', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errData = await response.json().catch(() => ({}));
                throw new Error(errData.detail || `Erreur serveur (${response.status})`);
            }

            const data = await response.json();

            const assistantMessage = {
                role: 'assistant',
                content: data.answer,
                sources: data.sources || [],
                suggested_questions: data.suggested_questions || [],
                latency_ms: data.latency_ms || null
            };
            setMessages((prev) => [...prev, assistantMessage]);

        } catch (err) {
            setError(err.message);
            setMessages((prev) => [
                ...prev,
                {
                    role: 'assistant',
                    content: `Désolé, une erreur est survenue lors de l'interrogation de l'assistant : ${err.message}`,
                    isError: true
                }
            ]);
        } finally {
            setIsLoading(false);
        }
    };

    const clearChat = () => {
        setMessages([]);
        setError(null);
        try {
            sessionStorage.removeItem(STORAGE_KEY);
        } catch {
            // stockage de session indisponible (navigation privée) : rien à nettoyer
        }
    };

    return { messages, isLoading, error, sendMessage, clearChat };
};
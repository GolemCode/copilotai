import { ChatInterface } from './ChatInterface';
import { useRagChat } from '../hooks/useRagChat';

export const FloatingCopilot = ({
  isOpen = false,
  setIsOpen,
  isExpanded = false,
  setIsExpanded,
  portfolioContext = null
}) => {
  const chatState = useRagChat();
  // 1. Mode Bulle Réduite (Bouton d'action flottant en bas à droite)
  if (!isOpen) {
    return (
      <aside className="copilot-floating-wrapper" aria-label="Copilot IA Assistant">
        <button
          type="button"
          className="copilot-fab-button"
          onClick={() => setIsOpen(true)}
          title="Ouvrir le Copilot AI (Assistant RAG & Analyse)"
          aria-label="Ouvrir le Copilot AI"
        >
          <div className="fab-glow-ring"></div>
          <span className="fab-icon">✨</span>
          <span className="fab-text">Copilot IA</span>
          <span className="fab-status-dot" title="Connecté"></span>
        </button>
      </aside>
    );
  }

  // 2. Mode Fenêtre Modale Détachée (Grande fenêtre centrée pour lecture confortable)
  if (isExpanded) {
    return (
      <div className="copilot-modal-backdrop" onClick={() => setIsExpanded(false)}>
        <div
          className="copilot-modal-dialog"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="Terminal Copilot AI"
        >
          <ChatInterface
            mode="window"
            isExpanded={true}
            onToggleExpand={() => setIsExpanded(false)}
            onClose={() => setIsOpen(false)}
            portfolioContext={portfolioContext}
            chatState={chatState}
          />
        </div>
      </div>
    );
  }

  // 3. Mode Widget Flottant en bas à droite (440px × 620px)
  return (
    <aside className="copilot-widget-container" aria-label="Copilot AI Fenêtre Flottante">
      <div className="copilot-widget-card">
        <ChatInterface
          mode="widget"
          isExpanded={false}
          onToggleExpand={() => setIsExpanded(true)}
          onClose={() => setIsOpen(false)}
          portfolioContext={portfolioContext}
          chatState={chatState}
        />
      </div>
    </aside>
  );
};

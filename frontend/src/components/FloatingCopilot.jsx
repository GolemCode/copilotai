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
  // Fermé : rien à afficher, l'assistant s'ouvre depuis le bouton de l'en-tête
  if (!isOpen) {
    return null;
  }

  // Mode agrandi : fenêtre centrée
  if (isExpanded) {
    return (
      <div className="copilot-modal-backdrop" onClick={() => setIsExpanded(false)}>
        <div
          className="copilot-modal-dialog"
          onClick={(e) => e.stopPropagation()}
          role="dialog"
          aria-modal="true"
          aria-label="Assistant documentaire"
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

  // Panneau latéral ancré à droite, sur toute la hauteur
  return (
    <aside className="copilot-widget-container" aria-label="Assistant documentaire">
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

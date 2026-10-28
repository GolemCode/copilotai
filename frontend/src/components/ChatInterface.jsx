import { useState, useRef, useEffect } from 'react';
import { useRagChat } from '../hooks/useRagChat';
import { MarkdownContent } from './MarkdownContent';
import { renderInline } from '../utils/renderInline';
import { Icon } from './Icon';

const QUICK_PROMPTS = [
  "Diagnostic : quels sont les risques et points faibles majeurs de mon portefeuille ?",
  "Ai-je des doublons majeurs entre mes positions (ex: S&P 500, MSCI World, Nasdaq) ?",
  "Quelle est mon exposition réelle cumulée à la Tech et aux États-Unis ?",
  "Quel ETF ou actif ajouter pour diversifier mon portefeuille sans créer de chevauchement ?"
];

export const ChatInterface = ({
  mode = 'widget',
  isExpanded = false,
  onToggleExpand,
  onClose,
  portfolioContext = null,
  chatState: externalChatState = null
}) => {
  const internalChatState = useRagChat();
  const { messages, isLoading, error, sendMessage, clearChat } = externalChatState || internalChatState;
  const [inputValue, setInputValue] = useState("");
  const [expandedSources, setExpandedSources] = useState({});
  const messagesEndRef = useRef(null);

  const scrollToBottom = () => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  };

  useEffect(() => {
    scrollToBottom();
  }, [messages, isLoading]);

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;
    sendMessage(inputValue, portfolioContext);
    setInputValue("");
  };

  const handlePromptClick = (promptText) => {
    if (isLoading) return;
    sendMessage(promptText, portfolioContext);
  };

  const toggleSources = (msgIdx) => {
    setExpandedSources(prev => ({
      ...prev,
      [msgIdx]: !prev[msgIdx]
    }));
  };

  return (
    <div className={`rag-chat-container mode-${mode}`}>
      <div className="chat-top-bar">
        <div className="chat-status-indicator">
          <span className="status-dot-online"></span>
          <div className="chat-header-text">
            <strong>Assistant Copilot IA</strong>
            <span className="chat-header-sub">RAG & Analyse Multi-Actifs</span>
          </div>
        </div>

        <div className="chat-header-actions">
          {messages.length > 0 && (
            <button className="btn-chat-action btn-clear-chat" onClick={clearChat} title="Effacer l'historique">
              Effacer
            </button>
          )}

          {/* En mode widget (non détaché) : bouton pour détacher en grand. Supprimé en mode détaché */}
          {!isExpanded && onToggleExpand && (
            <button
              type="button"
              className="btn-chat-action btn-expand-chat"
              onClick={onToggleExpand}
              title="Agrandir en fenêtre (Détacher)"
              aria-label="Agrandir en fenêtre"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <polyline points="15 3 21 3 21 9"></polyline>
                <polyline points="9 21 3 21 3 15"></polyline>
                <line x1="21" y1="3" x2="14" y2="10"></line>
                <line x1="3" y1="21" x2="10" y2="14"></line>
              </svg>
            </button>
          )}

          {/* En mode détaché : UNIQUEMENT la croix (pour réduire en mode widget). En mode normal : ferme le copilot */}
          {isExpanded ? (
            <button
              type="button"
              className="btn-chat-action btn-close-chat"
              onClick={onToggleExpand || onClose}
              title="Mettre en mode réduit"
              aria-label="Mettre en mode réduit"
            >
              <Icon name="close" />
            </button>
          ) : (
            onClose && (
              <button
                type="button"
                className="btn-chat-action btn-close-chat"
                onClick={onClose}
                title="Fermer l'assistant"
                aria-label="Fermer l'assistant"
              >
                <Icon name="close" />
              </button>
            )
          )}
        </div>
      </div>

      <div className="chat-messages-area">
        {messages.length === 0 && (
          <div className="chat-welcome">
            <div className="welcome-icon"><Icon name="assistant" size={28} /></div>
            <h4>Assistant documentaire</h4>
            <p>Posez une question sur vos ETF : les réponses s'appuient sur les fiches collectées, et les chiffres non retrouvés dans les sources sont signalés.</p>

            <div className="quick-prompts-section">
              <span className="prompts-label">Suggestions rapides :</span>
              <div className="prompts-grid">
                {QUICK_PROMPTS.map((prompt, idx) => (
                  <button
                    key={idx}
                    className="prompt-chip"
                    onClick={() => handlePromptClick(prompt)}
                    disabled={isLoading}
                  >
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {messages.map((msg, index) => {
          const isUser = msg.role === 'user';
          const hasSources = !isUser && msg.sources && msg.sources.length > 0;
          const isSourcesOpen = Boolean(expandedSources[index]);

          return (
            <div key={index} className={`chat-bubble-row ${isUser ? 'row-user' : 'row-assistant'}`}>
              <div className={`chat-bubble ${isUser ? 'bubble-user' : 'bubble-assistant'}`}>
                <div className="bubble-author">
                  {isUser ? (
                    <span className="user-author-tag"><Icon name="user" size={13} /> Vous</span>
                  ) : (
                    <div className="assistant-author-row">
                      <div className="assistant-badge">
                        <span className="assistant-pulse"></span>
                        <strong>Assistant</strong>
                        {hasSources && (
                          <span className="assistant-tag">Sourcé</span>
                        )}
                      </div>
                      {msg.latency_ms && (
                        <span className="bubble-latency"><Icon name="clock" size={12} /> {Math.round(msg.latency_ms)} ms</span>
                      )}
                    </div>
                  )}
                </div>

                <div className="bubble-content">
                  {isUser ? (
                    <p className="user-msg-text">{msg.content}</p>
                  ) : (
                    <MarkdownContent content={msg.content} />
                  )}
                </div>

                {hasSources && (
                  <div className="rag-sources-section">
                    <button
                      className="sources-toggle-btn"
                      onClick={() => toggleSources(index)}
                      type="button"
                    >
                      <span className="sources-count-label">
                        {msg.sources.length} source{msg.sources.length > 1 ? 's' : ''} documentaire{msg.sources.length > 1 ? 's' : ''}
                      </span>
                      <span className="sources-chevron">
                        <Icon name={isSourcesOpen ? 'chevronUp' : 'chevronDown'} size={14} /> {isSourcesOpen ? 'Masquer' : 'Afficher'}
                      </span>
                    </button>

                    {isSourcesOpen && (
                      <div className="sources-list">
                        {msg.sources.map((source, sIdx) => {
                          // Source directe (récupérée par ticker exact) : pas de score de similarité
                          const isDirect = source.match_type === 'direct' || source.similarity_score_pct == null;
                          const score = source.similarity_score_pct || 0;
                          // Paliers exprimés en similarité cosinus réelle (x100), seuil de rétention backend : 30
                          const scoreTier = isDirect ? 'high' : score >= 50 ? 'high' : score >= 40 ? 'med' : 'low';

                          return (
                            <div key={sIdx} className="source-card">
                              <div className="source-card-top">
                                <span className="source-ticker-pill">{source.ticker}</span>
                                <span className="source-name-title" title={source.name}>
                                  {source.name}
                                </span>
                                <span
                                  className={`source-score-pill score-${scoreTier}`}
                                  title={isDirect ? 'Fiche récupérée par ticker exact (pas de recherche sémantique)' : 'Similarité cosinus entre la question et la fiche (100 % = sens identique)'}
                                >
                                  {isDirect ? 'Source directe' : `${Math.round(score)} % de similarité`}
                                </span>
                              </div>

                              <div className="source-meta-row">
                                <span className="source-meta-tag">{source.category || 'Fonds'}</span>
                                {source.provider && (
                                  <span className="source-meta-tag">Source : {source.provider}</span>
                                )}
                                {source.aum && (
                                  <span className="source-meta-tag">Encours : {source.aum}</span>
                                )}
                                {source.paris_aligned && (
                                  <span className="source-meta-tag source-tag-paris">Indice aligné Accords de Paris (PAB)</span>
                                )}
                              </div>

                              {source.holdings && source.holdings.length > 0 && (
                                <div className="source-holdings-block">
                                  <span className="source-block-label">Principales positions :</span>
                                  <div className="source-holdings-chips">
                                    {source.holdings.slice(0, 6).map((h, hIdx) => (
                                      <span key={hIdx} className="source-holding-chip">{h}</span>
                                    ))}
                                    {source.holdings.length > 6 && (
                                      <span className="source-holding-chip chip-more">+{source.holdings.length - 6} autres</span>
                                    )}
                                  </div>
                                </div>
                              )}

                              {source.strategy && (
                                <div className="source-strategy-block">
                                  <span className="source-strategy-label">Stratégie :</span>
                                  <span className="source-strategy-text">{source.strategy}</span>
                                </div>
                              )}

                              <div className="source-actions-row">
                                {source.source_url && (
                                  <a
                                    href={source.source_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="source-action-btn btn-yahoo"
                                  >
                                    Cotation Yahoo Finance ({source.ticker}) <Icon name="external" size={12} />
                                  </a>
                                )}
                                {source.pdf_url && (
                                  <a
                                    href={source.pdf_url}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="source-action-btn btn-kid"
                                  >
                                    <Icon name="document" size={12} /> Fiche du fonds <Icon name="external" size={12} />
                                  </a>
                                )}
                              </div>

                              {source.snippet && !source.holdings?.length && (
                                <details className="source-snippet-details">
                                  <summary className="snippet-summary">Extrait de la fiche</summary>
                                  <div className="snippet-body-text">{source.snippet}</div>
                                </details>
                              )}
                            </div>
                          );
                        })}
                      </div>
                    )}
                  </div>
                )}

                {!isUser && msg.suggested_questions && msg.suggested_questions.length > 0 && (
                  <div className="chat-suggested-questions">
                    <div className="suggested-questions-header">
                      <span className="suggested-questions-title">Questions suggérées</span>
                    </div>
                    <div className="suggested-questions-list">
                      {msg.suggested_questions.map((qText, qIdx) => (
                        <button
                          key={qIdx}
                          type="button"
                          className="suggested-question-btn"
                          onClick={() => handlePromptClick(qText.replace(/\*\*/g, '').replace(/`/g, ''))}
                          disabled={isLoading}
                        >
                          <span className="suggested-question-arrow"><Icon name="arrowRight" size={13} /></span>
                          <span className="suggested-question-text">{renderInline(qText)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          );
        })}

        {isLoading && (
          <div className="chat-bubble-row row-assistant">
            <div className="chat-bubble bubble-assistant bubble-loading">
              <div className="typing-dots">
                <span></span><span></span><span></span>
              </div>
              <span className="loading-label">Recherche dans les fiches et rédaction de la réponse...</span>
            </div>
          </div>
        )}

        {error && (
          <div className="chat-alert-error">
            <Icon name="alert" size={14} /> {error}
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      <form onSubmit={handleSubmit} className="chat-input-row">
        <input
          type="text"
          className="chat-text-input"
          placeholder="Posez une question sur un actif, un ETF ou le risque..."
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          disabled={isLoading}
        />
        <button type="submit" className="chat-send-btn" disabled={isLoading || !inputValue.trim()}>
          {isLoading ? '...' : 'Envoyer'}
        </button>
      </form>
    </div>
  );
};
import { useState, useRef, useEffect } from 'react';
import { MarkdownContent } from './MarkdownContent';
import { renderInline } from '../utils/renderInline';
import { getDisplayTicker } from '../utils/format';
import { Icon } from './Icon';

const MAX_INPUT_HEIGHT = 140;

// Suggestions de départ : formulées à partir des lignes détenues quand le portefeuille est connu
const buildStarterPrompts = (portfolioContext) => {
  const tickers = (portfolioContext?.positions || []).map(p => getDisplayTicker(p.ticker));
  if (tickers.length === 0) {
    return [
      'Que contient un ETF MSCI World ?',
      'Quelle différence entre un ETF MSCI World et un ETF S&P 500 ?',
      'Quels ETF sont éligibles au PEA ?'
    ];
  }
  return [
    `Que contient l'ETF ${tickers[0]} ?`,
    'Quels sont les frais de mes ETF ?',
    tickers.length > 1
      ? `${tickers.slice(0, 3).join(', ')} : mes ETF se chevauchent-ils ?`
      : 'Quelle est mon exposition aux États-Unis ?',
    'Quels sont les points faibles de mon portefeuille ?'
  ];
};

const SourceCard = ({ source }) => {
  // Source directe (récupérée par ticker exact) : pas de score de similarité
  const isDirect = source.match_type === 'direct' || source.similarity_score_pct == null;
  const score = source.similarity_score_pct || 0;
  // Paliers exprimés en similarité cosinus réelle (x100), seuil de rétention backend : 30
  const scoreTier = isDirect ? 'high' : score >= 50 ? 'high' : score >= 40 ? 'med' : 'low';

  return (
    <div className="source-card">
      <div className="source-card-top">
        <span className="source-ticker-pill">{source.ticker}</span>
        <span className="source-name-title" title={source.name}>{source.name}</span>
        <span
          className={`source-score-pill score-${scoreTier}`}
          title={isDirect ? 'Fiche récupérée par ticker exact (pas de recherche sémantique)' : 'Similarité cosinus entre la question et la fiche (100 % = sens identique)'}
        >
          {isDirect ? 'Source directe' : `${Math.round(score)} % de similarité`}
        </span>
      </div>

      <div className="source-meta-row">
        <span className="source-meta-tag">{source.category || 'Fonds'}</span>
        {source.provider && <span className="source-meta-tag">Source : {source.provider}</span>}
      </div>

      <div className="source-actions-row">
        {source.source_url && (
          <a href={source.source_url} target="_blank" rel="noopener noreferrer" className="source-action-btn">
            Yahoo Finance <Icon name="external" size={12} />
          </a>
        )}
        {source.pdf_url && (
          <a href={source.pdf_url} target="_blank" rel="noopener noreferrer" className="source-action-btn">
            <Icon name="document" size={12} /> Fiche du fonds <Icon name="external" size={12} />
          </a>
        )}
      </div>

      {source.snippet && (
        <details className="source-snippet-details">
          <summary className="snippet-summary">Extrait utilisé</summary>
          <div className="snippet-body-text">{source.snippet}</div>
        </details>
      )}
    </div>
  );
};

export const ChatInterface = ({
  mode = 'widget',
  isExpanded = false,
  onToggleExpand,
  onClose,
  portfolioContext = null,
  chatState
}) => {
  const { messages, isLoading, error, sendMessage, clearChat } = chatState;
  const [inputValue, setInputValue] = useState('');
  const [expandedSources, setExpandedSources] = useState({});
  const areaRef = useRef(null);
  const lastMessageRef = useRef(null);
  const inputRef = useRef(null);

  // Nouvelle réponse : on montre son début (pas la fin), pour la lire dans l'ordre ;
  // question envoyée ou attente : on descend en bas de la conversation
  const lastRole = messages[messages.length - 1]?.role;
  useEffect(() => {
    if (lastRole === 'assistant' && !isLoading) {
      lastMessageRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else if (areaRef.current) {
      areaRef.current.scrollTo({ top: areaRef.current.scrollHeight, behavior: 'smooth' });
    }
  }, [messages.length, lastRole, isLoading]);

  // Échap : réduit la fenêtre agrandie, ou ferme le panneau
  useEffect(() => {
    const onKeyDown = (e) => {
      if (e.key !== 'Escape') return;
      if (isExpanded && onToggleExpand) onToggleExpand();
      else if (onClose) onClose();
    };
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, [isExpanded, onToggleExpand, onClose]);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  const resizeInput = (el) => {
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, MAX_INPUT_HEIGHT)}px`;
  };

  const send = (text) => {
    if (!text.trim() || isLoading) return;
    sendMessage(text, portfolioContext);
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!inputValue.trim() || isLoading) return;
    send(inputValue);
    setInputValue('');
    if (inputRef.current) inputRef.current.style.height = 'auto';
  };

  // Entrée envoie, Maj + Entrée passe à la ligne
  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
      handleSubmit(e);
    }
  };

  const toggleSources = (msgIdx) => {
    setExpandedSources(prev => ({ ...prev, [msgIdx]: !prev[msgIdx] }));
  };

  return (
    <div className={`rag-chat-container mode-${mode}`}>
      <div className="chat-top-bar">
        <div className="chat-header-text">
          <strong>Assistant</strong>
          <span className="chat-header-sub">Réponses tirées des fiches d'ETF collectées</span>
        </div>

        <div className="chat-header-actions">
          {messages.length > 0 && (
            <button type="button" className="btn-chat-action" onClick={clearChat} title="Nouvelle conversation" aria-label="Nouvelle conversation">
              <Icon name="refresh" size={15} />
            </button>
          )}
          {onToggleExpand && (
            <button
              type="button"
              className="btn-chat-action"
              onClick={onToggleExpand}
              title={isExpanded ? 'Réduire' : 'Agrandir'}
              aria-label={isExpanded ? 'Réduire' : 'Agrandir'}
            >
              <Icon name={isExpanded ? 'collapse' : 'expand'} size={15} />
            </button>
          )}
          {onClose && (
            <button type="button" className="btn-chat-action" onClick={onClose} title="Fermer (Échap)" aria-label="Fermer l'assistant">
              <Icon name="close" size={15} />
            </button>
          )}
        </div>
      </div>

      <div className="chat-messages-area" ref={areaRef}>
        {messages.length === 0 && (
          <div className="chat-welcome">
            <p className="chat-welcome-text">
              Posez une question sur vos ETF : frais, composition, exposition, différences entre fonds.
              Les chiffres absents des fiches sont signalés.
            </p>
            <div className="chat-chip-list">
              {buildStarterPrompts(portfolioContext).map(prompt => (
                <button key={prompt} type="button" className="chat-chip" onClick={() => send(prompt)} disabled={isLoading}>
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}

        {messages.map((msg, index) => {
          const isUser = msg.role === 'user';
          const isLast = index === messages.length - 1;
          const hasSources = !isUser && msg.sources?.length > 0;
          const isSourcesOpen = Boolean(expandedSources[index]);

          if (isUser) {
            return (
              <div key={index} className="chat-msg chat-msg-user" ref={isLast ? lastMessageRef : undefined}>
                {msg.content}
              </div>
            );
          }

          return (
            <div key={index} className="chat-msg chat-msg-assistant" ref={isLast ? lastMessageRef : undefined}>
              <MarkdownContent content={msg.content} />

              {hasSources && (
                <div className="chat-sources">
                  <button type="button" className="chat-sources-toggle" onClick={() => toggleSources(index)} aria-expanded={isSourcesOpen}>
                    <Icon name="document" size={13} />
                    {msg.sources.length} source{msg.sources.length > 1 ? 's' : ''}
                    <Icon name={isSourcesOpen ? 'chevronUp' : 'chevronDown'} size={13} />
                  </button>
                  {isSourcesOpen && (
                    <div className="sources-list">
                      {msg.sources.map((source, sIdx) => <SourceCard key={sIdx} source={source} />)}
                    </div>
                  )}
                </div>
              )}

              {isLast && msg.suggested_questions?.length > 0 && (
                <div className="chat-chip-list chat-followups">
                  {msg.suggested_questions.map((qText, qIdx) => (
                    <button
                      key={qIdx}
                      type="button"
                      className="chat-chip"
                      onClick={() => send(qText.replace(/\*\*/g, '').replace(/`/g, ''))}
                      disabled={isLoading}
                    >
                      {renderInline(qText)}
                    </button>
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {isLoading && (
          <div className="chat-msg chat-msg-assistant chat-loading">
            <span className="typing-dots"><span></span><span></span><span></span></span>
            Recherche dans les fiches…
          </div>
        )}

        {error && (
          <div className="chat-alert-error">
            <Icon name="alert" size={14} /> {error}
          </div>
        )}
      </div>

      <form onSubmit={handleSubmit} className="chat-input-row">
        <textarea
          ref={inputRef}
          rows={1}
          className="chat-text-input"
          placeholder="Posez votre question…"
          value={inputValue}
          onChange={(e) => { setInputValue(e.target.value); resizeInput(e.target); }}
          onKeyDown={handleKeyDown}
        />
        <button
          type="submit"
          className="chat-send-btn"
          disabled={isLoading || !inputValue.trim()}
          title="Envoyer (Entrée)"
          aria-label="Envoyer"
        >
          <Icon name="send" size={16} />
        </button>
      </form>
    </div>
  );
};

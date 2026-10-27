import { renderInline } from '../utils/renderInline';

function isTableRow(line) {
  const trimmed = line.trim();
  return trimmed.startsWith('|') && trimmed.endsWith('|') && trimmed.length > 2;
}

function isTableDivider(line) {
  const trimmed = line.trim();
  return /^\|(\s*:?-+:?\s*\|)+$/.test(trimmed);
}

function parseTableAlignments(sepLine) {
  const cells = sepLine.split('|').slice(1, -1);
  return cells.map(cell => {
    const t = cell.trim();
    const startColon = t.startsWith(':');
    const endColon = t.endsWith(':');
    if (startColon && endColon) return 'center';
    if (endColon) return 'right';
    return 'left';
  });
}

export const MarkdownContent = ({ content }) => {
  if (!content) return null;

  const lines = content.split('\n');
  const elements = [];
  let currentList = null; // { type: 'ul' | 'ol', items: [] }

  const flushList = () => {
    if (currentList) {
      if (currentList.type === 'ul') {
        elements.push(
          <ul key={`ul-${elements.length}`} className="md-list">
            {currentList.items.map((item, idx) => (
              <li key={idx} className={item.isNested ? 'md-li-nested' : 'md-li'}>
                {renderInline(item.text)}
              </li>
            ))}
          </ul>
        );
      } else {
        elements.push(
          <ol key={`ol-${elements.length}`} className="md-ordered-list">
            {currentList.items.map((item, idx) => (
              <li key={idx} className="md-li">
                {renderInline(item.text)}
              </li>
            ))}
          </ol>
        );
      }
      currentList = null;
    }
  };

  for (let i = 0; i < lines.length; i++) {
    const rawLine = lines[i];
    const trimmed = rawLine.trim();

    if (!trimmed) {
      flushList();
      continue;
    }

    // Détection d'un tableau Markdown (| Col 1 | Col 2 | ...)
    if (isTableRow(rawLine) && i + 1 < lines.length && isTableDivider(lines[i + 1])) {
      flushList();
      const headerRow = rawLine.split('|').slice(1, -1).map(c => c.trim());
      const alignments = parseTableAlignments(lines[i + 1]);
      i += 2; // Avance après le header et la ligne de séparation

      const bodyRows = [];
      while (i < lines.length && isTableRow(lines[i])) {
        const cells = lines[i].split('|').slice(1, -1).map(c => c.trim());
        bodyRows.push(cells);
        i++;
      }
      i--; // Réajuste pour le for loop

      elements.push(
        <div key={`table-wrap-${i}`} className="md-table-wrapper">
          <table className="md-table">
            <thead>
              <tr>
                {headerRow.map((cellText, cIdx) => (
                  <th key={cIdx} style={{ textAlign: alignments[cIdx] || 'left' }}>
                    {renderInline(cellText)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((rowCells, rIdx) => (
                <tr key={rIdx}>
                  {rowCells.map((cellText, cIdx) => (
                    <td key={cIdx} style={{ textAlign: alignments[cIdx] || 'left' }}>
                      {renderInline(cellText)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    // Titres Markdown conventionnels (#, ##, ###, ####)
    if (trimmed.startsWith('#### ')) {
      flushList();
      elements.push(<h5 key={`h4-${i}`} className="md-h4">{renderInline(trimmed.slice(5))}</h5>);
      continue;
    }
    if (trimmed.startsWith('### ')) {
      flushList();
      elements.push(<h4 key={`h3-${i}`} className="md-h3">{renderInline(trimmed.slice(4))}</h4>);
      continue;
    }
    if (trimmed.startsWith('## ')) {
      flushList();
      elements.push(<h3 key={`h2-${i}`} className="md-h2">{renderInline(trimmed.slice(3))}</h3>);
      continue;
    }
    if (trimmed.startsWith('# ')) {
      flushList();
      elements.push(<h2 key={`h1-${i}`} className="md-h1">{renderInline(trimmed.slice(2))}</h2>);
      continue;
    }

    // Puces non ordonnées (- ou *)
    const bulletMatch = rawLine.match(/^(\s*)(?:[-*]|\u2022)\s+(.*)$/);
    if (bulletMatch) {
      const isNested = bulletMatch[1].length >= 2;
      if (!currentList || currentList.type !== 'ul') {
        flushList();
        currentList = { type: 'ul', items: [] };
      }
      currentList.items.push({ text: bulletMatch[2], isNested });
      continue;
    }

    // Puces numérotées (ex: 1. 2.)
    const numberMatch = trimmed.match(/^(\d+)\.\s+(.*)$/);
    if (numberMatch) {
      if (!currentList || currentList.type !== 'ol') {
        flushList();
        currentList = { type: 'ol', items: [] };
      }
      currentList.items.push({ text: numberMatch[2], isNested: false });
      continue;
    }

    // Ligne normale
    flushList();

    // Détection de note légale / avertissement
    const lower = trimmed.toLowerCase();
    const isDisclaimer = lower.includes('conseil en investissement') || lower.includes('titre indicatif') || lower.includes('avertissement');

    if (isDisclaimer) {
      elements.push(
        <div key={`disclaimer-${i}`} className="md-disclaimer-box">
          <span className="disclaimer-icon">⚖️</span>
          <div className="disclaimer-text">{renderInline(trimmed)}</div>
        </div>
      );
    } else {
      elements.push(
        <p key={`p-${i}`} className="md-paragraph">
          {renderInline(trimmed)}
        </p>
      );
    }
  }

  flushList();

  return <div className="markdown-rendered-body">{elements}</div>;
};
export default MarkdownContent;

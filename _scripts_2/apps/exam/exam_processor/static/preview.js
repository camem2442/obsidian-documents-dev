/* Preview-only layout. Stored Markdown remains the editable source of truth. */
function protectMath(source) {
  const formulas = [];
  const mathPattern = /\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|(?<![\\$])\$(?!\$)(?:\\.|[^\\$])+\$|\\\([\s\S]+?\\\)/g;
  const codePattern = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*?`+)/g;
  const pieces = String(source || '').split(codePattern);
  const text = pieces.map((piece, index) => {
    if (index % 2) return piece;
    return piece.replace(mathPattern, (match) => {
      const display = match.startsWith('$$') || match.startsWith('\\[');
      const tex = display ? match.slice(2, -2) : match.startsWith('\\(') ? match.slice(2, -2) : match.slice(1, -1);
      const token = `EXAMMATHTOKEN${formulas.length}END`;
      formulas.push({ token, tex: normalizeMathCommands(display ? tex : tex.replace(/\s*\r?\n\s*/g, ' ')), display });
      return token;
    });
  }).join('');
  return { text, formulas };
}

// Some vision transcriptions drop the backslash from common TeX commands.
// Repair only inside already detected math, leaving ordinary prose untouched.
function normalizeMathCommands(tex) {
  return String(tex || '')
    .replace(/\bherefore(?=\s*[A-Za-z0-9({])/g, '\\therefore')
    .replace(/(?<=\d|\)|\]|\})\s*imes\b/g, '\\times');
}

function renderMath(target, formulas) {
  if (!formulas.length) return;
  const byToken = new Map(formulas.map(formula => [formula.token, formula]));
  const walker = document.createTreeWalker(target, NodeFilter.SHOW_TEXT);
  const nodes = [];
  while (walker.nextNode()) nodes.push(walker.currentNode);
  const tokenPattern = /EXAMMATHTOKEN\d+END/g;
  for (const node of nodes) {
    if (node.parentElement?.closest('code, pre')) continue;
    const value = node.nodeValue;
    let offset = 0, match;
    tokenPattern.lastIndex = 0;
    const fragment = document.createDocumentFragment();
    while ((match = tokenPattern.exec(value))) {
      fragment.append(document.createTextNode(value.slice(offset, match.index)));
      const formula = byToken.get(match[0]);
      if (!formula) {
        fragment.append(document.createTextNode(match[0]));
      } else {
        const span = document.createElement('span');
        span.className = formula.display ? 'exam-math-display' : 'exam-math-inline';
        try {
          katex.render(formula.tex, span, { displayMode: formula.display, throwOnError: true });
        } catch (error) {
          span.classList.add('exam-math-error');
          span.title='수식 표시 오류: '+error.message;
          katex.render(formula.tex, span, { displayMode: formula.display, throwOnError: false });
        }
        fragment.append(span);
      }
      offset = tokenPattern.lastIndex;
    }
    if (offset) {
      fragment.append(document.createTextNode(value.slice(offset)));
      node.replaceWith(fragment);
    }
  }
}

function normalizeMathQuoteConditions(source) {
  return String(source || '').split('\n').map(line => {
    const match = line.match(/^(>\s+)(.*)$/);
    if (!match || !/\(가\)/.test(match[2])) return line;
    const parts = match[2].split(/\s+(?=\((?:나|다|라|마)\))/);
    return match[1] + parts.map((part, index) => `${index ? '> ' : ''}${part.trim()}`).join('\n');
  }).join('\n');
}

function normalizeMathTextMarkers(source) {
  const protectedPattern = /(```[\s\S]*?```|~~~[\s\S]*?~~~|`+[^`\n]*?`+|\$\$[\s\S]+?\$\$|\\\[[\s\S]+?\\\]|(?<![\\$])\$(?!\$)(?:\\.|[^\\$])+\$|\\\([\s\S]+?\\\))/g;
  const circled = '⓪①②③④⑤⑥⑦⑧⑨';
  return String(source || '').split(protectedPattern).map((piece, index) => {
    if (index % 2) return piece;
    return piece.replace(/\\textcircled\{([A-Z0-9])\}/g, (_, label) => (
      /[0-9]/.test(label) ? circled[Number(label)] : String.fromCodePoint(0x24B6 + label.charCodeAt(0) - 65)
    ));
  }).join('');
}

window.ExamPreview = {
  render(target, text, fileURL, jobId) {
    const normalized = normalizeMathTextMarkers(normalizeMathQuoteConditions(text));
    const protectedSource = protectMath(normalized);
    const bound = protectedSource.text.replace(/!\[\[([^\]]+)\]\]/g, (_, path) => {
      if (!path.startsWith('assets/')) return `> 공통 지문: ${path}`;
      return `![원본 자료](${fileURL(path)})`;
    });
    target.innerHTML = DOMPurify.sanitize(marked.parse(bound, {breaks: true}), {
      FORBID_TAGS: ['iframe', 'object', 'embed', 'form', 'input', 'script', 'style'],
      FORBID_ATTR: ['style']
    });
    target.querySelectorAll('img').forEach(img => {
      if (!img.getAttribute('src')?.startsWith(`/api/jobs/${jobId}/file/`)) img.remove();
    });
    target.querySelectorAll('a').forEach(link => {
      link.target = '_blank';
      link.rel = 'noopener noreferrer';
    });
    target.querySelectorAll('p, li').forEach(paragraph => {
      if (paragraph.closest('blockquote, table, pre')) return;
      if (paragraph.tagName === 'LI' && paragraph.querySelector('p, ul, ol')) return;
      const lines = [[]];
      for (const node of paragraph.childNodes) {
        if (node.nodeName === 'BR') lines.push([]);
        else lines.at(-1).push(node);
      }
      const isChoice = line => /^\s*[①②③④⑤]/.test(line.map(node => node.textContent).join(''));
      if (!lines.some(isChoice)) return;
      const replacement = document.createDocumentFragment();
      let block;
      for (const line of lines) {
        if (!block || isChoice(line)) {
          block = document.createElement('p');
          if (isChoice(line)) block.className = 'exam-choice';
          replacement.append(block);
        } else {
          block.append(block.classList.contains('exam-choice') ? document.createTextNode(' ') : document.createElement('br'));
        }
        line.forEach(node => block.append(node));
      }
      if (paragraph.tagName === 'LI') paragraph.replaceChildren(replacement);
      else paragraph.replaceWith(replacement);
    });
    target.querySelectorAll('blockquote').forEach(box => {
      box.classList.add('source-box');
      const first = box.querySelector('p')?.firstChild;
      if (first?.nodeType === Node.TEXT_NODE) {
        first.textContent = first.textContent.replace(/^\[![\w가-힣-]+\]\s*/, '');
      }
    });
    renderMath(target, protectedSource.formulas);
    const errors = target.querySelectorAll('.exam-math-error').length;
    target.dataset.mathStatus = errors ? 'error' : 'rendered';
    target.dataset.mathErrors = String(errors);
    return {mathErrors: errors, formulas: protectedSource.formulas.length};
  }
};

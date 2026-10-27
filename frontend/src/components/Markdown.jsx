import React from 'react';

// A small Markdown renderer for the in-app user guide (userGuide.js): just the
// constructs that guide uses, rendered as React elements -- no HTML strings,
// so nothing in the text can inject markup. Not a general Markdown parser.
//
// Blocks: #/##/### headings, paragraphs, - / 1. / - [ ] lists (nested by
// indentation), > quotes, pipe tables, and a line of {{NAME}} that renders
// the matching entry of `embeds`. Inline: **bold**, `code`, [text](url), <url>.

const LIST_RE = /^(\s*)([-*]|\d+\.)\s+(\[[ xX]\]\s+)?(.*)$/;
const SEP_RE = /^:?-{3,}:?$/;

export function slugify(text) {
  return text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '');
}

function splitRow(line) {
  return line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(c => c.trim());
}

function parseList(lines) {
  const root = { indent: -1, children: [] };
  const stack = [root];
  lines.forEach(line => {
    const m = LIST_RE.exec(line);
    const item = { indent: m[1].length, ordered: /\d/.test(m[2]), task: !!m[3], text: m[4], children: [] };
    while (stack.length > 1 && item.indent <= stack[stack.length - 1].indent) stack.pop();
    stack[stack.length - 1].children.push(item);
    stack.push(item);
  });
  return root.children;
}

export function parseBlocks(md) {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let i = 0;
  const startsBlock = (l) => /^#{1,3}\s/.test(l) || l.startsWith('>') || l.startsWith('|')
    || LIST_RE.test(l) || /^\{\{\w+\}\}$/.test(l.trim());
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    let m;
    if ((m = /^(#{1,3})\s+(.*)$/.exec(line))) {
      blocks.push({ type: 'heading', level: m[1].length, text: m[2] });
      i++;
    } else if ((m = /^\{\{(\w+)\}\}$/.exec(line.trim()))) {
      blocks.push({ type: 'embed', name: m[1] });
      i++;
    } else if (line.startsWith('>')) {
      const text = [];
      while (i < lines.length && lines[i].startsWith('>')) text.push(lines[i++].replace(/^>\s?/, ''));
      blocks.push({ type: 'quote', text: text.join(' ') });
    } else if (line.startsWith('|')) {
      const rows = [];
      while (i < lines.length && lines[i].startsWith('|')) rows.push(splitRow(lines[i++]));
      const body = rows.filter(r => !r.every(c => SEP_RE.test(c)));
      blocks.push({ type: 'table', head: body[0], rows: body.slice(1) });
    } else if (LIST_RE.test(line)) {
      const items = [];
      while (i < lines.length && LIST_RE.test(lines[i])) items.push(lines[i++]);
      blocks.push({ type: 'list', items: parseList(items) });
    } else {
      const text = [];
      while (i < lines.length && lines[i].trim() && !startsBlock(lines[i])) text.push(lines[i++].trim());
      blocks.push({ type: 'paragraph', text: text.join(' ') });
    }
  }
  return blocks;
}

const INLINE_RE = /\*\*(.+?)\*\*|`([^`]+)`|\[([^\]]+)\]\(([^)\s]+)\)|<((?:https?:\/\/)[^>\s]+|[^>\s@]+@[^>\s]+)>/g;

function Inline({ text }) {
  const out = [];
  let last = 0;
  let m;
  INLINE_RE.lastIndex = 0;
  while ((m = INLINE_RE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = out.length;
    if (m[1] !== undefined) out.push(<strong key={k} style={{ color: '#f0f4f8', fontWeight: 600 }}><Inline text={m[1]} /></strong>);
    else if (m[2] !== undefined) out.push(<code key={k} style={styles.code}>{m[2]}</code>);
    else if (m[3] !== undefined) out.push(<Link key={k} href={m[4]}>{m[3]}</Link>);
    else out.push(<Link key={k} href={m[5].includes('@') && !m[5].startsWith('http') ? `mailto:${m[5]}` : m[5]}>{m[5]}</Link>);
    last = INLINE_RE.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return <>{out}</>;
}

function Link({ href, children }) {
  const external = /^https?:/.test(href);
  return (
    <a href={href} style={{ color: '#74C0FC' }}
      {...(external ? { target: '_blank', rel: 'noopener noreferrer' } : {})}>
      {children}
    </a>
  );
}

function List({ items }) {
  const ordered = items[0]?.ordered;
  const Tag = ordered ? 'ol' : 'ul';
  const allTasks = items.every(it => it.task);
  return (
    <Tag style={{ margin: '0 0 12px', paddingLeft: allTasks ? 4 : 22, listStyle: allTasks ? 'none' : undefined }}>
      {items.map((it, i) => (
        <li key={i} style={{ margin: '4px 0', lineHeight: 1.55 }}>
          {it.task ? (
            <label style={{ display: 'flex', gap: 8, alignItems: 'flex-start', cursor: 'pointer' }}>
              {/* Explicit size: index.css makes every input full width. */}
              <input type="checkbox" style={{ width: 14, height: 14, marginTop: 4, accentColor: '#00d4ff', flexShrink: 0 }} />
              <span><Inline text={it.text} /></span>
            </label>
          ) : <Inline text={it.text} />}
          {it.children.length > 0 && <div style={{ marginTop: 4 }}><List items={it.children} /></div>}
        </li>
      ))}
    </Tag>
  );
}

export default function Markdown({ source, embeds = {} }) {
  const blocks = React.useMemo(() => parseBlocks(source), [source]);
  return (
    <div style={styles.body}>
      {blocks.map((b, i) => {
        switch (b.type) {
          case 'heading': {
            const Tag = `h${b.level + 1}`;
            // The first heading opens the page: no rule above it.
            const style = i === 0 ? { ...styles[`h${b.level}`], borderTop: 'none', marginTop: 12 } : styles[`h${b.level}`];
            return <Tag key={i} id={slugify(b.text)} style={style}><Inline text={b.text} /></Tag>;
          }
          case 'paragraph':
            return <p key={i} style={{ margin: '0 0 12px', lineHeight: 1.6 }}><Inline text={b.text} /></p>;
          case 'quote':
            return (
              <div key={i} style={styles.quote}><Inline text={b.text} /></div>
            );
          case 'list':
            return <List key={i} items={b.items} />;
          case 'table':
            return (
              <div key={i} style={{ overflowX: 'auto', margin: '0 0 14px' }}>
                <table style={styles.table}>
                  <thead>
                    <tr>{b.head.map((c, j) => <th key={j} style={styles.th}><Inline text={c} /></th>)}</tr>
                  </thead>
                  <tbody>
                    {b.rows.map((r, j) => (
                      <tr key={j}>{r.map((c, k) => <td key={k} style={styles.td}><Inline text={c} /></td>)}</tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
          case 'embed':
            return <div key={i} style={{ margin: '4px 0 14px' }}>{embeds[b.name] || null}</div>;
          default:
            return null;
        }
      })}
    </div>
  );
}

const styles = {
  body: { color: '#c8d0da', fontSize: 14, fontFamily: 'IBM Plex Sans, sans-serif' },
  h1: { fontSize: 20, fontWeight: 600, color: '#e8edf2', margin: '28px 0 12px', paddingTop: 16, borderTop: '1px solid #1e2530', scrollMarginTop: 8 },
  h2: { fontSize: 18, fontWeight: 600, color: '#e8edf2', margin: '30px 0 10px', paddingTop: 18, borderTop: '1px solid #1e2530', scrollMarginTop: 8 },
  h3: { fontSize: 14.5, fontWeight: 600, color: '#e8edf2', margin: '18px 0 8px' },
  code: { fontFamily: 'IBM Plex Mono, monospace', fontSize: 12.5, background: '#1a2029', border: '1px solid #2a3340', borderRadius: 3, padding: '0 4px', color: '#e8edf2' },
  quote: { borderLeft: '3px solid #ffab40', background: '#1a1000', padding: '10px 14px', borderRadius: 4, margin: '0 0 14px', lineHeight: 1.6 },
  table: { borderCollapse: 'collapse', width: '100%', fontSize: 13 },
  th: { textAlign: 'left', padding: '7px 10px', borderBottom: '1px solid #2a3340', color: '#e8edf2', fontWeight: 600, background: '#13161c', verticalAlign: 'top' },
  td: { padding: '7px 10px', borderBottom: '1px solid #1a1e24', verticalAlign: 'top', lineHeight: 1.5 },
};

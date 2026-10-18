/**
 * The little markdown the model writes in its replies: **bold** and "- " bullet lines. Parsed into plain data
 * that components turn into React nodes, so nothing in a reply can ever become markup.
 */
export interface Span {
  text: string;
  bold: boolean;
}

export interface ReplyLine {
  bullet: boolean;
  spans: Span[];
}

const BULLET = /^\s*[-*•]\s+/;

export function inlineSpans(text: string): Span[] {
  const spans: Span[] = [];
  const re = /\*\*(.+?)\*\*/g;
  let last = 0;
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) spans.push({ text: text.slice(last, m.index), bold: false });
    spans.push({ text: m[1], bold: true });
    last = m.index + m[0].length;
  }
  if (last < text.length) spans.push({ text: text.slice(last), bold: false });
  // An unpaired "**" is noise, not emphasis.
  return spans.map((s) => ({ ...s, text: s.text.replace(/\*\*/g, "") })).filter((s) => s.text);
}

export function replyLines(text: string): ReplyLine[] {
  return text.split("\n").map((line) => {
    const bullet = BULLET.test(line) && !/^\s*\*\*/.test(line);
    return { bullet, spans: inlineSpans(bullet ? line.replace(BULLET, "") : line) };
  });
}

/** The same reply as one line of plain text, for compact places like the decision trace. */
export function plainReply(text: string): string {
  return replyLines(text)
    .map((l) => (l.bullet ? "• " : "") + l.spans.map((s) => s.text).join(""))
    .filter((l) => l.trim())
    .join(" ");
}

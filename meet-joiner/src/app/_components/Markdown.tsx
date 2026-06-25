// Zero-dependency Markdown renderer for LLM output (answers, streamed deltas).
// Covers the subset models actually emit: headings, bullet/numbered lists, code
// fences + inline code, blockquotes, bold/italic, links, and paragraph/line breaks.
// Deliberately small (no remark/react-markdown) to match the repo's no-deps style;
// renders gracefully on *partial* input, so it's safe on a still-streaming buffer.

import { Fragment, type ReactNode } from "react";

// --- inline: `code`, **bold**, *italic*, [text](url) -------------------------
// Code is matched first (highest precedence) so markdown inside it stays literal.
const INLINE =
  /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)|(\[[^\]]+\]\([^)\s]+\))/g;

function renderInline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  let last = 0;
  let m: RegExpExecArray | null;
  INLINE.lastIndex = 0;
  let i = 0;
  while ((m = INLINE.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("`")) {
      out.push(
        <code key={key} className="rounded bg-[#f3ead3]/10 px-1 py-px font-mono text-[0.92em]">
          {tok.slice(1, -1)}
        </code>,
      );
    } else if (tok.startsWith("**")) {
      out.push(
        <strong key={key} className="font-semibold text-[#f3ead3]">
          {renderInline(tok.slice(2, -2), key)}
        </strong>,
      );
    } else if (tok.startsWith("*")) {
      out.push(
        <em key={key} className="italic">
          {renderInline(tok.slice(1, -1), key)}
        </em>,
      );
    } else {
      const mm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok);
      if (mm) {
        out.push(
          <a
            key={key}
            href={mm[2]}
            target="_blank"
            rel="noreferrer"
            className="text-sky-300 underline underline-offset-2 hover:text-sky-200"
          >
            {mm[1]}
          </a>,
        );
      } else {
        out.push(tok);
      }
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

// --- blocks ------------------------------------------------------------------
export default function Markdown({ text, className }: { text: string; className?: string }) {
  const lines = text.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;
  let k = 0;

  while (i < lines.length) {
    const line = lines[i];

    // skip blank lines between blocks
    if (!line.trim()) {
      i++;
      continue;
    }

    // fenced code block ``` … ```
    const fence = /^\s*```/.test(line);
    if (fence) {
      i++;
      const code: string[] = [];
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++; // closing fence (may be absent if still streaming)
      blocks.push(
        <pre
          key={k++}
          className="my-1 overflow-x-auto rounded bg-black/40 p-2 font-mono text-[0.9em] leading-relaxed"
        >
          <code>{code.join("\n")}</code>
        </pre>,
      );
      continue;
    }

    // heading # … ######
    const h = /^(#{1,6})\s+(.*)$/.exec(line);
    if (h) {
      const lvl = h[1].length;
      blocks.push(
        <p key={k++} className={`mt-1.5 font-semibold ${lvl <= 2 ? "text-[1.05em]" : ""}`}>
          {renderInline(h[2], `h${k}`)}
        </p>,
      );
      i++;
      continue;
    }

    // blockquote > …
    if (/^\s*>\s?/.test(line)) {
      const quote: string[] = [];
      while (i < lines.length && /^\s*>\s?/.test(lines[i])) quote.push(lines[i++].replace(/^\s*>\s?/, ""));
      blocks.push(
        <blockquote key={k++} className="my-1 border-l-2 border-[#f3ead3]/25 pl-2 italic text-[#f3ead3]/70">
          {renderInline(quote.join(" "), `q${k}`)}
        </blockquote>,
      );
      continue;
    }

    // unordered list - * +
    if (/^\s*[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*+]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*[-*+]\s+/, ""));
      blocks.push(
        <ul key={k++} className="my-1 list-disc space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ul${k}-${j}`)}</li>
          ))}
        </ul>,
      );
      continue;
    }

    // ordered list 1. 2. …
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+\.\s+/, ""));
      blocks.push(
        <ol key={k++} className="my-1 list-decimal space-y-0.5 pl-5">
          {items.map((it, j) => (
            <li key={j}>{renderInline(it, `ol${k}-${j}`)}</li>
          ))}
        </ol>,
      );
      continue;
    }

    // paragraph: consecutive non-blank lines that aren't another block
    const para: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() &&
      !/^\s*```/.test(lines[i]) &&
      !/^(#{1,6})\s+/.test(lines[i]) &&
      !/^\s*>\s?/.test(lines[i]) &&
      !/^\s*[-*+]\s+/.test(lines[i]) &&
      !/^\s*\d+\.\s+/.test(lines[i])
    ) {
      para.push(lines[i++]);
    }
    blocks.push(
      <p key={k++} className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
        {para.map((ln, j) => (
          <Fragment key={j}>
            {j > 0 && <br />}
            {renderInline(ln, `p${k}-${j}`)}
          </Fragment>
        ))}
      </p>,
    );
  }

  return <div className={className}>{blocks}</div>;
}

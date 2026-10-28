import { Fragment } from "react";

/** Minimal, XSS-safe renderer: **bold**, bare URLs -> links, newlines. Builds React nodes only. */
export function Rich({ text }: { text: string }) {
  return (
    <>
      {text.split("\n").map((line, i) => (
        <Fragment key={i}>
          {i > 0 && <br />}
          {line.split(/(\*\*[^*]+\*\*|https?:\/\/[^\s)]+)/g).map((part, j) => {
            if (part.startsWith("**") && part.endsWith("**")) return <strong key={j}>{part.slice(2, -2)}</strong>;
            if (/^https?:\/\//.test(part))
              return <a key={j} href={part} target="_blank" rel="noopener noreferrer">{part}</a>;
            return <Fragment key={j}>{part}</Fragment>;
          })}
        </Fragment>
      ))}
    </>
  );
}

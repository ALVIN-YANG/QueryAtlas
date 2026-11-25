import { createElement } from "react";

export function AnswerText({ text }) {
  const parts = text.split(/(?<![\\*])\*\*(?!\s)([^*\r\n]*?[^\s*\\])\*\*(?!\*)/g);
  return parts.map((part, index) =>
    index % 2 === 0 ? part : createElement("strong", { key: index }, part),
  );
}

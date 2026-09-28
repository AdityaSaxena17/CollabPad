import type { Editor } from "@tiptap/core";
import type { Node } from "@tiptap/pm/model";

export type Suggestion = { text: string; pos: number; doc: Node };

export function captureCompletionContext(editor: Editor) {
  const { doc, selection } = editor.state;
  if (!selection.empty || !selection.$from.parent.isTextblock ||
      selection.$from.parent.type.name === "codeBlock") return null;
  const pos = selection.from;
  const prefix = doc.textBetween(0, pos, "\n", "\n").slice(-3000);
  const suffix = doc.textBetween(pos, doc.content.size, "\n", "\n").slice(0, 1000);
  const wordCharacter = /[\p{L}\p{N}]/u;
  if (prefix && suffix && wordCharacter.test(prefix.at(-1)!) && wordCharacter.test(suffix[0])) return null;
  if (!prefix.trim() && !suffix.trim()) return null;
  return { doc, pos, prefix, suffix };
}

export function suggestionIsCurrent(editor: Editor, suggestion: Suggestion) {
  return editor.state.selection.empty && editor.state.selection.from === suggestion.pos &&
    editor.state.doc.eq(suggestion.doc);
}

export function takeSuggestion(text: string, mode: "all" | "word" | "line") {
  if (mode === "all") return text;
  if (mode === "line") {
    const end = text.indexOf("\n");
    return end < 0 ? text : text.slice(0, end + 1);
  }
  if (text.startsWith("\n")) return "\n";
  const match = text.match(/^[^\S\n]*\S+[^\S\n]*/u);
  return match?.[0] ?? text;
}

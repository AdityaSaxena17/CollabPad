import type { Editor } from "@tiptap/core";
import type { Mark, Slice } from "@tiptap/pm/model";
import type { Transaction } from "@tiptap/pm/state";

export type EnhancementSelection = {
  from: number;
  to: number;
  source: string;
  slice: Slice;
  marks: readonly Mark[];
};

export function captureEnhancementSelection(editor: Editor): EnhancementSelection | string {
  const { doc, selection } = editor.state;
  const { from, to, $from, $to } = selection;
  if (selection.empty) return "Select text to improve.";
  if (!$from.parent.isTextblock || $from.parent.type.name === "codeBlock" ||
      $from.depth !== $to.depth || $from.start() !== $to.start()) {
    return "Select text within one paragraph, heading, or list item.";
  }
  const source = doc.textBetween(from, to);
  if (!source.trim()) return "Select text to improve.";
  if (source.length > 2_000) return "Select no more than 2,000 characters.";
  let marks: readonly Mark[] | null = null;
  let invalid = false;
  $from.parent.nodesBetween($from.parentOffset, $to.parentOffset, (node) => {
    if (!node.isText) {
      invalid = true;
    } else if (marks === null) {
      marks = node.marks;
    } else if (!marksMatch(marks, node.marks)) {
      invalid = true;
    }
  });
  if (invalid || marks === null) {
    return "Select text with one consistent format and no line breaks or inline objects.";
  }
  return { from, to, source, slice: doc.slice(from, to), marks };
}

function marksMatch(left: readonly Mark[], right: readonly Mark[]): boolean {
  return left.length === right.length && left.every((mark) => right.some((other) => mark.eq(other)));
}

export function enhancementSelectionIsCurrent(editor: Editor, selected: EnhancementSelection): boolean {
  const { doc } = editor.state;
  return selected.from < selected.to && selected.from >= 0 && selected.to <= doc.content.size &&
    doc.slice(selected.from, selected.to).eq(selected.slice);
}

/** Move an untouched selection through edits elsewhere; reject edits inside it. */
export function mapEnhancementSelection(
  selected: EnhancementSelection,
  transaction: Transaction,
): EnhancementSelection | null {
  if (!transaction.docChanged) return selected;
  let { from, to } = selected;
  for (const map of transaction.mapping.maps) {
    let changedInside = false;
    map.forEach((oldStart, oldEnd) => {
      if ((oldStart < to && oldEnd > from) ||
          (oldStart === oldEnd && oldStart > from && oldStart < to)) {
        changedInside = true;
      }
    });
    if (changedInside) return null;
    from = map.map(from, 1);
    to = map.map(to, -1);
  }
  const mapped = { ...selected, from, to };
  return from < to && to <= transaction.doc.content.size &&
    transaction.doc.slice(from, to).eq(selected.slice) ? mapped : null;
}

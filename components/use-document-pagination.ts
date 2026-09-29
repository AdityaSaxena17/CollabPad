import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { Editor } from "@tiptap/core";
import type { EditorView } from "@tiptap/pm/view";
import { useEffect, useState } from "react";

export const PAGE_HEIGHT = 1056;
export const PAGE_GAP = 32;
export const PAGE_TOP = 72;
export const PAGE_BOTTOM = 96;

const PAGE_STRIDE = PAGE_HEIGHT + PAGE_GAP;
const CONTENT_BOTTOM = PAGE_HEIGHT - PAGE_BOTTOM;

type PageBreak = { pos: number; height: number };
type Line = { pos: number; top: number; bottom: number };

const paginationKey = new PluginKey<PageBreak[]>("document-pagination");

/** Page spacers live only in the editor view; collaborators share the text, not layout. */
export const DocumentPagination = Extension.create({
  name: "documentPagination",
  addProseMirrorPlugins() {
    return [new Plugin<PageBreak[]>({
      key: paginationKey,
      state: {
        init: () => [],
        apply(transaction, previous) {
          const breaks = transaction.getMeta(paginationKey);
          if (breaks !== undefined) return breaks as PageBreak[];
          return transaction.docChanged ? [] : previous;
        },
      },
      props: {
        decorations(state) {
          const breaks = paginationKey.getState(state) ?? [];
          return DecorationSet.create(state.doc, breaks.map(({ pos, height }) =>
            Decoration.widget(pos, () => {
              const spacer = document.createElement("span");
              spacer.className = "document-page-break";
              spacer.contentEditable = "false";
              spacer.setAttribute("aria-hidden", "true");
              spacer.style.height = `${height}px`;
              return spacer;
            }, { side: -1, key: `page-${pos}` }),
          ));
        },
      },
    })];
  },
});

function setBreaks(editor: Editor, breaks: PageBreak[]) {
  editor.view.dispatch(editor.state.tr.setMeta(paginationKey, breaks));
}

function lineAt(view: EditorView, pos: number, rootTop: number, scale: number): Line {
  const rect = view.coordsAtPos(pos, 1);
  return {
    pos,
    top: (rect.top - rootTop) / scale,
    bottom: (rect.bottom - rootTop) / scale,
  };
}

/** Find the first document position on each rendered line of every text block. */
function measureLines(view: EditorView, scale: number): Line[] {
  const lines: Line[] = [];
  const rootTop = view.dom.getBoundingClientRect().top;

  view.state.doc.descendants((node, blockPos) => {
    if (!node.isTextblock) {
      if (node.isBlock && node.isAtom) {
        const dom = view.nodeDOM(blockPos);
        if (dom instanceof HTMLElement) {
          const rect = dom.getBoundingClientRect();
          lines.push({
            pos: blockPos,
            top: (rect.top - rootTop) / scale,
            bottom: (rect.bottom - rootTop) / scale,
          });
        }
      }
      return true;
    }

    const start = blockPos + 1;
    const end = start + node.content.size;
    let pos = start;
    let first = true;

    while (pos <= end) {
      const line = lineAt(view, pos, rootTop, scale);
      let splitPos = pos;
      if (first) {
        splitPos = blockPos;
        let resolved = view.state.doc.resolve(splitPos);
        while (resolved.depth > 0 && resolved.parentOffset === 0 &&
          (resolved.parent.type.name === "listItem" || resolved.parent.type.name === "blockquote")) {
          splitPos = resolved.before(resolved.depth);
          resolved = view.state.doc.resolve(splitPos);
        }
      }
      first = false;

      // A caret's rect can be shorter than another font on the same line.
      // Compare vertical overlap to avoid treating a font change as a new line.
      let low = pos + 1;
      let high = end + 1;
      while (low < high) {
        const middle = Math.floor((low + high) / 2);
        const next = lineAt(view, middle, rootTop, scale);
        if (next.top >= line.bottom - 1) high = middle;
        else low = middle + 1;
      }

      const nextPos = low;
      if (nextPos > pos + 1) {
        const last = lineAt(view, nextPos - 1, rootTop, scale);
        line.bottom = Math.max(line.bottom, last.bottom);
      }
      lines.push({ ...line, pos: splitPos });
      if (nextPos > end) break;
      pos = nextPos;
    }

    return false;
  });

  return lines.sort((a, b) => a.top - b.top || a.pos - b.pos);
}

function calculateBreaks(lines: Line[]): PageBreak[] {
  const breaks: PageBreak[] = [];
  let page = 0;
  let displaced = 0;

  for (const line of lines) {
    if (line.bottom + displaced <= page * PAGE_STRIDE + CONTENT_BOTTOM + 0.5) continue;

    const nextTop = (page + 1) * PAGE_STRIDE + PAGE_TOP;
    const height = Math.max(0, nextTop - (line.top + displaced));
    breaks.push({ pos: line.pos, height });
    displaced += height;
    page += 1;
  }

  return breaks;
}

/** Keep pagination local to this browser, including font and viewport reflow. */
export function useDocumentPagination(editor: Editor | null, zoom: number) {
  const [pageCount, setPageCount] = useState(1);

  useEffect(() => {
    if (!editor) return;

    let frame = 0;
    let disposed = false;
    const scale = zoom / 100;

    function layout() {
      frame = 0;
      if (disposed || !editor || editor.isDestroyed || editor.view.composing) return;

      setBreaks(editor, []);
      const breaks = calculateBreaks(measureLines(editor.view, scale));
      setBreaks(editor, breaks);
      setPageCount(breaks.length + 1);
    }

    function schedule() {
      if (frame) cancelAnimationFrame(frame);
      frame = requestAnimationFrame(layout);
    }

    editor.on("update", schedule);
    editor.view.dom.addEventListener("compositionend", schedule);
    window.addEventListener("resize", schedule);
    document.fonts?.addEventListener("loadingdone", schedule);
    void document.fonts?.ready.then(schedule);
    schedule();

    return () => {
      disposed = true;
      if (frame) cancelAnimationFrame(frame);
      editor.off("update", schedule);
      editor.view.dom.removeEventListener("compositionend", schedule);
      window.removeEventListener("resize", schedule);
      document.fonts?.removeEventListener("loadingdone", schedule);
    };
  }, [editor, zoom]);

  return pageCount;
}

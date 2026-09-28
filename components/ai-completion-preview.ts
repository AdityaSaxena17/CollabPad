import { Extension } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import type { Editor } from "@tiptap/core";

type Preview = { pos: number; text: string } | null;
const previewKey = new PluginKey<Preview>("ai-completion-preview");

export const AiCompletionPreview = Extension.create({
  name: "aiCompletionPreview",
  addProseMirrorPlugins() {
    return [new Plugin<Preview>({
      key: previewKey,
      state: {
        init: () => null,
        apply(transaction, previous) {
          const next = transaction.getMeta(previewKey);
          if (next !== undefined) return next as Preview;
          return transaction.docChanged ? null : previous;
        },
      },
      props: {
        decorations(state) {
          const preview = previewKey.getState(state);
          if (!preview || preview.pos > state.doc.content.size) return DecorationSet.empty;
          return DecorationSet.create(state.doc, [Decoration.widget(preview.pos, () => {
            const span = document.createElement("span");
            span.className = "ai-completion-ghost";
            span.contentEditable = "false";
            span.setAttribute("aria-hidden", "true");
            span.textContent = preview.text;
            return span;
          }, { side: 1 })]);
        },
      },
    })];
  },
});

export function setCompletionPreview(editor: Editor, preview: Preview) {
  editor.view.dispatch(editor.state.tr.setMeta(previewKey, preview));
}

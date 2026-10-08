// The VS Code side: a read-only text view of `.shape` files and "Shape: Compare with Git HEAD".
// The extension sends no telemetry and makes no network request; the only programs it runs are
// `shape` (--version, cat) and `git` (rev-parse, show).
import * as vscode from "vscode";

import { Exec, ShapeCli, ShapeUnavailableError, catFile, checkShape } from "./cli";
import { headTextForm, nodeExec } from "./git";

export const SCHEME = "shape-text";
export const COMPARE = "shape.compareWithHead";

export type Revision = "working" | "head";

export function textUri(file: string, rev: Revision): vscode.Uri {
  const name = vscode.Uri.file(file).path.split("/").pop() ?? "file.shape";
  return vscode.Uri.from({
    scheme: SCHEME,
    path: `/${name}`,
    query: JSON.stringify({ file, rev }),
  });
}

function parseUri(uri: vscode.Uri): { file: string; rev: Revision } {
  const q = JSON.parse(uri.query) as { file: string; rev: Revision };
  return { file: q.file, rev: q.rev === "head" ? "head" : "working" };
}

/** Locates `shape` once per value of the `shape.path` setting. A failure is reported once with
 * one error message; until the setting changes, nothing else is run. */
export class Guard {
  private cli: Promise<ShapeCli | null> | undefined;
  private key = "";

  constructor(
    private readonly exec: Exec = nodeExec,
    private readonly report: (message: string) => void = (m) => {
      void vscode.window.showErrorMessage(m);
    },
    private readonly setting: () => string = () =>
      vscode.workspace.getConfiguration("shape").get<string>("path", ""),
    private readonly trusted: () => boolean = () => vscode.workspace.isTrusted,
  ) {}

  /** The usable `shape`, or null (the reason was reported once). */
  async get(): Promise<ShapeCli | null> {
    if (!this.trusted()) {
      if (this.key !== "untrusted") {
        this.key = "untrusted";
        this.cli = Promise.resolve(null);
        this.report("Shape runs the shape executable, which needs a trusted workspace.");
      }
      return null;
    }
    const key = this.setting();
    if (this.cli === undefined || key !== this.key) {
      this.key = key;
      this.cli = checkShape(key, this.exec).then(
        (cli) => cli,
        (err: unknown) => {
          this.report(err instanceof ShapeUnavailableError ? err.message : String(err));
          return null;
        },
      );
    }
    return this.cli;
  }

  reset(): void {
    this.cli = undefined;
    this.key = "";
  }
}

class TextForm implements vscode.TextDocumentContentProvider {
  constructor(
    private readonly guard: Guard,
    private readonly exec: Exec,
  ) {}

  async provideTextDocumentContent(uri: vscode.Uri): Promise<string> {
    const cli = await this.guard.get();
    if (cli === null) {
      return "# The Shape command line is not available; see the error message.\n";
    }
    const { file, rev } = parseUri(uri);
    try {
      return rev === "head"
        ? await headTextForm(cli, file, this.exec)
        : await catFile(cli, file, this.exec);
    } catch (err) {
      return `# ${err instanceof Error ? err.message : String(err)}\n`;
    }
  }
}

/** Opens the text form in place of the binary file. */
class ShapeEditor implements vscode.CustomReadonlyEditorProvider {
  constructor(private readonly guard: Guard) {}

  openCustomDocument(uri: vscode.Uri): vscode.CustomDocument {
    return { uri, dispose: () => undefined };
  }

  async resolveCustomEditor(
    document: vscode.CustomDocument,
    panel: vscode.WebviewPanel,
  ): Promise<void> {
    const column = panel.viewColumn;
    const cli = await this.guard.get();
    if (cli === null) {
      panel.webview.html =
        "<!DOCTYPE html><html><body><p>The Shape command line is not available. " +
        "See the error message.</p></body></html>";
      return;
    }
    const doc = await vscode.workspace.openTextDocument(textUri(document.uri.fsPath, "working"));
    await vscode.window.showTextDocument(doc, { viewColumn: column, preview: false });
    panel.dispose();
  }
}

export interface Api {
  textUri: typeof textUri;
  /** Every error message the extension has shown (for the tests). */
  reported: string[];
}

export function activate(context: vscode.ExtensionContext): Api {
  const reported: string[] = [];
  const guard = new Guard(nodeExec, (message) => {
    reported.push(message);
    void vscode.window.showErrorMessage(message);
  });
  const provider = new TextForm(guard, nodeExec);
  context.subscriptions.push(
    vscode.workspace.registerTextDocumentContentProvider(SCHEME, provider),
    vscode.window.registerCustomEditorProvider("shape.textView", new ShapeEditor(guard), {
      supportsMultipleEditorsPerDocument: true,
    }),
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("shape.path")) {
        guard.reset();
      }
    }),
    vscode.commands.registerCommand(COMPARE, async (arg?: vscode.Uri) => {
      const uri = arg ?? vscode.window.activeTextEditor?.document.uri;
      const file =
        uri === undefined
          ? undefined
          : uri.scheme === SCHEME
            ? parseUri(uri).file
            : uri.scheme === "file"
              ? uri.fsPath
              : undefined;
      if (file === undefined) {
        void vscode.window.showErrorMessage("Select a .shape file to compare with Git HEAD.");
        return;
      }
      if ((await guard.get()) === null) {
        return;
      }
      const name = vscode.Uri.file(file).path.split("/").pop();
      await vscode.commands.executeCommand(
        "vscode.diff",
        textUri(file, "head"),
        textUri(file, "working"),
        `${name}: Git HEAD ↔ working copy`,
      );
    }),
  );
  return { textUri, reported };
}

export function deactivate(): void {}

import { Component, type ErrorInfo, type ReactNode } from "react";

interface Props {
  children: ReactNode;
}
interface State {
  error: Error | null;
}

/**
 * Catches render/runtime errors in a page so a single failing visualisation
 * (e.g. a lost WebGL context) never blanks the whole dashboard.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error("[dashboard] page crashed", error, info.componentStack);
  }

  private isWebGLError(): boolean {
    const message = this.state.error?.message ?? "";
    const name = (this.state.error as { name?: string } | null)?.name ?? "";
    return (
      name === "GPUInitializationError" ||
      /webgl2 is required|webgl/i.test(message)
    );
  }

  render(): ReactNode {
    if (this.state.error) {
      if (this.isWebGLError()) {
        return (
          <div
            role="alert"
            className="panel m-4 border-amber-500/20 bg-amber-500/5 p-6 text-sm text-amber-200"
          >
            <p className="font-medium">
              3D map unavailable — this browser provided no WebGL2 context.
            </p>
            <p className="mt-1 text-amber-200/70">
              The GPU-accelerated map could not start. Your tables, KPIs, and
              other panels are unaffected. Enable hardware acceleration / WebGL
              in a current Chrome, Edge, Firefox, or Safari release, update GPU
              drivers, then retry.{" "}
              <a
                className="underline"
                href="https://wiki.openstreetmap.org/wiki/This_map_requires_WebGL"
                target="_blank"
                rel="noreferrer"
              >
                About WebGL2
              </a>
            </p>
            <div className="mt-3 flex gap-2">
              <button
                onClick={() => this.setState({ error: null })}
                className="rounded-md border border-amber-500/30 px-3 py-1 text-xs text-amber-200 hover:bg-amber-500/10"
              >
                Retry
              </button>
              <button
                onClick={() => window.location.reload()}
                className="rounded-md border border-white/10 px-3 py-1 text-xs text-zinc-300 hover:bg-white/5"
              >
                Reload page
              </button>
            </div>
          </div>
        );
      }
      return (
        <div className="panel m-4 border-amber-500/20 bg-amber-500/5 p-6 text-sm text-amber-200">
          <p className="font-medium">This view failed to render.</p>
          <p className="mt-1 text-amber-200/70">
            {this.state.error.message}. Try reloading the page or switching views.
          </p>
          <button
            onClick={() => this.setState({ error: null })}
            className="mt-3 rounded-md border border-amber-500/30 px-3 py-1 text-xs text-amber-200 hover:bg-amber-500/10"
          >
            Retry
          </button>
        </div>
      );
    }
    return this.props.children;
  }
}

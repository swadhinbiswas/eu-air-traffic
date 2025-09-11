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

  render(): ReactNode {
    if (this.state.error) {
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

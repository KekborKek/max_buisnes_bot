// Ловит падение рендера, чтобы вместо белого экрана была плашка с «Повторить».
import { Button, Panel, Typography } from "@maxhub/max-ui";
import { Component, type ErrorInfo, type ReactNode } from "react";

import { texts } from "../texts";

interface Props {
  children: ReactNode;
  onError?: (error: Error, info: ErrorInfo) => void;
}

export class ErrorBoundary extends Component<Props, { failed: boolean }> {
  state = { failed: false };

  static getDerivedStateFromError() {
    return { failed: true };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.props.onError?.(error, info);
  }

  render() {
    if (!this.state.failed) return this.props.children;
    return (
      <Panel centeredX centeredY className="screen-center" role="alert">
        <Typography.Body>{texts.common.error}</Typography.Body>
        <Button onClick={() => this.setState({ failed: false })}>{texts.common.retry}</Button>
      </Panel>
    );
  }
}

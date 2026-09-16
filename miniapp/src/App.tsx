import { Button, CellList, CellSimple, Panel, Spinner, Typography } from "@maxhub/max-ui";
import { useCallback, useEffect, useState } from "react";

import { api, type Me } from "./api";
import { texts } from "./texts";

type State = { kind: "loading" } | { kind: "error" } | { kind: "ready"; me: Me };

export default function App() {
  const [state, setState] = useState<State>({ kind: "loading" });

  const load = useCallback(async () => {
    setState({ kind: "loading" });
    try {
      const me = await api.me();
      setState({ kind: "ready", me });
      api.track("miniapp_opened").catch(() => undefined);
    } catch {
      setState({ kind: "error" });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (state.kind === "loading") {
    return (
      <Panel centeredX centeredY style={{ minHeight: "100vh" }}>
        <Spinner />
        <Typography.Body>{texts.loading}</Typography.Body>
      </Panel>
    );
  }

  if (state.kind === "error") {
    return (
      <Panel centeredX centeredY style={{ minHeight: "100vh", gap: 12, padding: 16 }}>
        <Typography.Body>{texts.error}</Typography.Body>
        <Button onClick={load}>{texts.retry}</Button>
      </Panel>
    );
  }

  const { me } = state;
  return (
    <Panel style={{ minHeight: "100vh", padding: 16, display: "grid", gap: 16, alignContent: "start" }}>
      <Typography.Headline>{texts.greeting(me.first_name ?? "")}</Typography.Headline>
      {me.is_dev && <Typography.Label>{texts.demoBadge}</Typography.Label>}
      <CellList mode="island">
        <CellSimple title={texts.title} subtitle={`user_id: ${me.user_id}`} />
      </CellList>
      <Button stretched size="large" onClick={() => api.track("primary_action_clicked")}>
        {texts.primaryAction}
      </Button>
    </Panel>
  );
}

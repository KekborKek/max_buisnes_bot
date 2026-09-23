// Временная заглушка экранов 15 (T13), 16 и 17 (T12). Держит место в роутере,
// чтобы переход «вперёд» и возврат через BackButton работали уже сейчас.
import { Panel, Typography } from "@maxhub/max-ui";

import { texts } from "../texts";

export function PlaceholderScreen({ screen }: { screen: "15" | "16" | "17" }) {
  return (
    <Panel centeredX centeredY className="placeholder" data-screen={screen}>
      <Typography.Body>{texts.placeholder.soon}</Typography.Body>
    </Panel>
  );
}

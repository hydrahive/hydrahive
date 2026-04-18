import { useMemo } from "react";
import { useParams, useSearchParams } from "react-router-dom";
import { ChatShell } from "@/components/chat-v2/ChatShell";
import { buildChatV2Target } from "@/components/chat-v2/hydrahive-runtime";

export function ChatV2DemoPage() {
  const { id = "hydrahivedev" } = useParams<{ id: string }>();
  const [search] = useSearchParams();
  const kind = search.get("kind") || "project";
  const target = useMemo(() => buildChatV2Target(kind, id), [kind, id]);

  return (
    <div className="h-full min-h-0">
      <ChatShell target={target} />
    </div>
  );
}

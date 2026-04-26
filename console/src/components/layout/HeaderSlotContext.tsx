import { createContext, useContext, useEffect, type DependencyList, type ReactNode } from "react";

export const HeaderSlotCtx = createContext<{ setSlot: (n: ReactNode) => void } | null>(null);

/** Inject a node into the layout header's bottom slot (e.g. a tab bar). */
export function useHeaderSlot(node: ReactNode, deps: DependencyList) {
  const ctx = useContext(HeaderSlotCtx);
  useEffect(() => {
    ctx?.setSlot(node);
    return () => ctx?.setSlot(null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ctx, ...deps]);
}

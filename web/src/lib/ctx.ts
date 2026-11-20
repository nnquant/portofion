import { createContext, useContext } from "react";
import type { Palette } from "./colors";
import type { Me, WsInfo } from "./types";

export interface WsContext {
  slug: string;
  info: WsInfo;
  me: Me;
  palette: Palette;
  canEdit: boolean;
  isAdmin: boolean;
}

export const WsCtx = createContext<WsContext | null>(null);

export function useWs(): WsContext {
  const c = useContext(WsCtx);
  if (!c) throw new Error("useWs outside workspace");
  return c;
}

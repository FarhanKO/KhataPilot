import { createContext, useContext } from "react";

import type { ChatResponse, OwnerStatus, Pending, Shop, ToolOutcome } from "./types";
import type { Tone } from "./ui";

export type TabId = "chat" | "ledger" | "forecast" | "credit" | "loans" | "controls" | "audit" | "sandbox" | "trace";

export interface Forwarded {
  source: "forwarded_sms" | "invoice_text";
  text: string;
}

export interface ChatMessage {
  id: string;
  role: "owner" | "assistant" | "inbound" | "system";
  text: string;
  from?: string; // for inbound messages: who sent it
  forwarded?: Forwarded[];
  outcomes?: ToolOutcome[];
  pending?: Pending[];
  requestId?: string;
  at: string;
}

export interface AppContextValue {
  shop: Shop | null;
  status: OwnerStatus | null;
  pending: Pending[];
  version: number;
  refresh: () => void;
  tab: TabId;
  setTab: (tab: TabId) => void;
  messages: ChatMessage[];
  sending: boolean;
  sendChat: (message: string, forwarded?: Forwarded[]) => Promise<ChatResponse | null>;
  sendInbound: (fromNumber: string, text: string, channel?: "sms_gateway" | "messenger") => Promise<void>;
  newConversation: () => void;
  conversationId: string;
  openConversation: (id: string) => Promise<void>;
  review: (pendingId?: string) => void;
  toast: (text: string, tone?: Tone) => void;
}

export const AppContext = createContext<AppContextValue | null>(null);

export function useApp(): AppContextValue {
  const value = useContext(AppContext);
  if (!value) throw new Error("useApp outside AppContext");
  return value;
}

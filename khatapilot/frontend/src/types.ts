// Shapes of the backend's responses (only the fields the app uses). Money is always in paisa.

export interface Shop {
  id: string;
  name: string;
  owner_name: string;
  shop_type: string;
  area: string;
  district: string;
  data_since: string;
  llm_mode: string;
  model: string;
  risk_model: string;
  now: string;
}

export interface Check {
  name: string;
  label: string;
  status: "pass" | "escalate" | "block" | "skip" | "info";
  detail: string;
  tier: number | null;
}

export interface AuditEntry {
  seq: number;
  entry_id: string;
  ts: string;
  request_id: string;
  channel: string;
  actor: string;
  provenance: string;
  event: string;
  summary: string;
  action: Record<string, unknown> | null;
  tier: number | null;
  checks: Check[];
  decision: string;
  reason: string;
  sandbox_ref: string | null;
  decision_id: string | null;
  prev_hash: string;
  hash: string;
}

export interface Segment {
  source: string;
  provenance: "owner" | "untrusted" | string;
  text: string;
}

export type TraceEvent = { trace_id: number; request_id?: string } & (
  | ({ type: "audit" } & AuditEntry)
  | { type: "turn"; channel: string; ts: string; sender: "owner" | "external"; sender_display: string; segments: Segment[] }
  | { type: "reply"; text: string; kill_switch: boolean }
  | { type: "quarantine"; block: number; kind: string; summary: string; suspected: boolean }
  | { type: "tool"; tool: string; status: string; tier: number | null; decision_id: string | null }
  | { type: "llm_step"; step: number; model: string; stop_reason: string; tool_calls: string[] }
  | { type: "llm_error"; message: string }
  | { type: "reset" }
);

export interface Pending {
  id: string;
  decision_id: string;
  tool: string;
  kind: string;
  tier: number;
  requires_pin: boolean;
  summary: string;
  warnings: string[];
  action: {
    summary: string;
    payment?: {
      payee_name: string | null;
      payee_number: string | null;
      amount_paisa: number;
      method: string;
      execute_on: string | null;
      note: string | null;
    } | null;
    params?: Record<string, unknown>;
  };
  status: string;
  created_at: string;
  expires_at: string;
}

export interface ApprovalResult {
  ok: boolean;
  status: string;
  message: string;
  attempts_left: number | null;
  data: Record<string, unknown>;
}

export interface ToolOutcome {
  call_id: string | null;
  tool: string;
  status: "ok" | "held" | "blocked" | "error";
  tier: number | null;
  decision_id: string | null;
  pending_id: string | null;
  requires_pin: boolean;
  message: string;
  data: unknown;
}

export interface ConversationSummary {
  id: string;
  title: string;
  started_at: string;
  last_at: string;
  messages: number;
}

/** A message of an earlier chat, as it was shown. */
export interface ShownMessage {
  role: "user" | "assistant";
  text: string;
  sender: string;
  request_id: string | null;
  at: string;
  forwarded: { source: "forwarded_sms" | "invoice_text"; text: string }[];
  outcomes: ToolOutcome[];
}

export interface ChatResponse {
  conversation_id: string;
  request_id: string;
  reply: string;
  outcomes: ToolOutcome[];
  pending: Pending[];
  quarantine: { block: number; kind: string; summary: string; suspected: boolean }[];
  kill_switch: boolean;
  model: string;
}

export interface OwnerStatus {
  kill_switch: { on: boolean; since: string | null; reason: string | null };
  limits: { per_payment: string; per_day: string; per_payment_paisa: number; per_day_paisa: number };
  pin: { set: boolean; locked: boolean; locked_until: string | null };
  trusted_payees: { id: string; name: string; number: string; method: string; added_via: string }[];
}

export interface ControlResult {
  ok: boolean;
  message: string;
  attempts_left?: number | null;
  frozen?: boolean;
}

export interface DayTotals {
  day: string;
  sales_paisa: number;
  baki_given_paisa: number;
  baki_collected_paisa: number;
  money_in_paisa: number;
  money_out_paisa: number;
  net_paisa: number;
  entries: number;
}

export interface LedgerEntry {
  id: string;
  occurred_at: string;
  local_date: string;
  kind: string;
  direction: "in" | "out";
  method: string;
  amount_paisa: number;
  category: string;
  counterparty_name: string | null;
  counterparty_number: string | null;
  note: string | null;
  trx_id: string | null;
  source: string;
}

export interface Debtor {
  name: string;
  number: string | null;
  outstanding_paisa: number;
  oldest_open_since: string | null;
  days_outstanding: number | null;
}

/** One baki reminder as held for approval: the exact text the customer will get. */
export interface ReminderDraft {
  customer_name: string;
  to_number: string;
  owed_paisa: number;
  days_outstanding: number;
  open_since: string | null;
  language: "english" | "banglish";
  body: string;
}

export interface Reminder {
  id: string;
  customer_name: string;
  to_masked: string;
  owed_paisa: number;
  days_outstanding: number;
  body: string;
  status: "sent" | "failed";
  sandbox_ref: string | null;
  failure: string | null;
  sent_at: string;
  paid_since_paisa: number;
  still_owes_paisa: number;
  outcome: "paid" | "part_paid" | "waiting" | "not_sent";
}

export interface Obligation {
  id: string;
  kind: string;
  counterparty_name: string;
  category: string;
  amount_paisa: number;
  due_date: string;
  note: string | null;
}

export interface Payment {
  id: string;
  payee_name: string | null;
  payee_number: string;
  method: string;
  amount_paisa: number;
  execute_on: string;
  status: string;
  approved_tier: number;
  approved_with_pin: boolean;
  sandbox_txn_id: string | null;
  failure: string | null;
  created_at: string;
}

export interface ForecastItem {
  key: string;
  label: string;
  amount_paisa: number;
  direction: "in" | "out";
  day: string;
  kind: string;
  certain: boolean;
  overdue: boolean;
  counterparty: string | null;
}

export interface ForecastDay {
  day: string;
  expected_in_paisa: number;
  expected_out_paisa: number;
  items: ForecastItem[];
  balance_paisa: number;
  low_paisa: number;
  high_paisa: number;
  below_buffer: boolean;
}

export interface CashForecast {
  as_of: string;
  days_ahead: number;
  start_balance_paisa: number;
  buffer_paisa: number;
  days: ForecastDay[];
  first_shortfall: { day: string; balance_paisa: number; short_by_paisa: number; cause: string } | null;
  lowest_day: string;
  lowest_balance_paisa: number;
  suggestions: { kind: string; text: string; amount_paisa: number; new_lowest_paisa: number; closes_gap: boolean }[];
  headline: string;
  method: string[];
}

export interface Consent {
  granted: boolean;
  version: string;
  text: string;
  granted_at: string | null;
}

export interface Reason {
  feature: string;
  label: string;
  value: number;
  typical: number;
  impact: number;
  direction: "helps" | "hurts";
  text: string;
}

export interface CreditProfile {
  id: string;
  created_at: string;
  model_version: string;
  probability_of_default: number;
  band: string;
  band_words: string;
  summary: string;
  helps: Reason[];
  hurts: Reason[];
  features: { name: string; label: string; value: number; typical: number; unit: string; scored: boolean }[];
  history_days: number;
}

export interface LoanProduct {
  id: string;
  name: string;
  lender_type: string;
  min_taka: number;
  max_taka: number;
  tenures: number[];
  annual_rate: number;
  flat_fee_rate: number;
  fee_rate: number;
  bands: string[];
  speed: string;
  collateral: string;
  notes: string;
  illustrative: boolean;
}

export interface LoanMatch {
  product: LoanProduct;
  eligible: boolean;
  reasons_not: string[];
  amount_paisa: number;
  covers_request: boolean;
  tenure_months: number | null;
  monthly_instalment_paisa: number | null;
  total_cost_paisa: number | null;
  affordable: boolean;
  covers_shortfall: boolean;
  rank: number | null;
  explanation: string;
}

export interface LoanOptions {
  requested_paisa: number;
  amount_basis: string;
  purpose: string;
  band: string;
  probability_of_default: number;
  affordability: {
    monthly_business_income_paisa: number;
    max_instalment_paisa: number;
    shortfall_gap_paisa: number;
    shortfall_day: string | null;
  };
  matches: LoanMatch[];
  recommended: string | null;
  recommendation: string;
}

export interface LoanApplication {
  id: string;
  product_name: string;
  amount_paisa: number;
  tenure_months: number;
  status: string;
  sandbox_ref: string | null;
  failure: string | null;
  submitted_at: string;
}

export interface SandboxAccount {
  id: string;
  provider: string;
  number_masked: string;
  holder_name: string;
  kind: string;
  balance_paisa: number;
  frozen: boolean;
  frozen_reason: string | null;
}

export interface SandboxTxn {
  trx_id: string;
  direction: "debit" | "credit";
  txn_type: string;
  amount_paisa: number;
  fee_paisa: number;
  balance_after_paisa: number;
  counterparty_masked: string | null;
  counterparty_name: string | null;
  reference: string | null;
  status: string;
  authorization_id: string | null;
  occurred_at: string;
}

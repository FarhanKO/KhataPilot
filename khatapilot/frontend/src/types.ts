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
  | { type: "reply"; text: string; kill_switch: boolean; route?: Route; route_reason?: string }
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
  /** "asked": nothing was done; the owner has to choose first (see SmsAskData). */
  status: "ok" | "held" | "blocked" | "error" | "asked";
  tier: number | null;
  decision_id: string | null;
  pending_id: string | null;
  requires_pin: boolean;
  message: string;
  data: unknown;
}

/** What record_sms (a forwarded SMS) or record_customer_payment (money the owner typed) returns when money came
 * in from a customer who owes baki: the two choices to show. trx_id is empty for typed money. */
export interface SmsAskData {
  question_id: string;
  customer: string;
  amount: string;
  owes: string;
  trx_id: string;
  choices: { purpose: "baki" | "other"; label: string }[];
}

/** A held SMS question as the server keeps it; status says whether and how the owner answered. */
export interface SmsQuestion {
  id: string;
  customer_name: string;
  amount_paisa: number;
  owed_paisa: number;
  status: "open" | "baki" | "other";
  answered_at: string | null;
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
  route: Route;
  route_reason: string;
}

/** Who planned a reply: the model, the offline rules (no API call), the offline planner standing in
 *  because the model could not be reached, or the guard itself ("stop", a PIN typed in chat). */
export type Route = "model" | "rules" | "offline" | "capped" | "guard";

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
  /** What was sold or bought and how much, in the owner's words; only when they said it. */
  product_note: string | null;
  trx_id: string | null;
  source: string;
}

export interface Debtor {
  name: string;
  number: string | null;
  outstanding_paisa: number;
  oldest_open_since: string | null;
  days_outstanding: number | null;
  /** The day the customer promised to pay back, if the owner gave one when recording the baki. */
  expected_by: string | null;
  /** No baki reminders: "customer" replied STOP (only they can undo it), "owner" stopped them. */
  no_reminders: "customer" | "owner" | null;
}

export interface ReminderOptOut {
  customer_name: string | null;
  number_masked: string;
  source: "customer" | "owner";
  words: string | null;
  since: string;
  owner_can_lift: boolean;
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
  priority: boolean;
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

export interface SafetyBuffer {
  amount_paisa: number;
  mode: "auto" | "owner" | "standard";
  explanation: string;
  auto_paisa: number | null;
  daily_in_paisa: number | null;
  swing_paisa: number | null;
  swing_ratio: number | null;
  steadiness: "steady" | "moderate" | "choppy" | null;
  set_at: string | null;
}

export interface CashForecast {
  as_of: string;
  days_ahead: number;
  start_balance_paisa: number;
  buffer_paisa: number;
  buffer: SafetyBuffer;
  days: ForecastDay[];
  first_shortfall: { day: string; balance_paisa: number; short_by_paisa: number; cause: string } | null;
  lowest_day: string;
  lowest_balance_paisa: number;
  suggestions: { kind: string; text: string; amount_paisa: number; new_lowest_paisa: number; closes_gap: boolean }[];
  headline: string;
  method: string[];
}

export interface SavingsMonth {
  month: string;
  label: string;
  profit_paisa: number;
  target_paisa: number;
  saved_paisa: number;
  so_far: boolean;
}

export interface SavingsStatus {
  as_of: string;
  state: "no_plan" | "no_profit" | "no_account" | "done" | "ready" | "partial" | "wait";
  headline: string;
  share_pct: number | null;
  account: { name: string; number_masked: string; method: "bkash" | "nagad" | "bank"; set_at: string } | null;
  month: string;
  based_on: string;
  basis: "last_month" | "three_months";
  basis_text: string;
  last_month_profit_paisa: number;
  target_paisa: number;
  saved_paisa: number;
  pending_paisa: number;
  remaining_paisa: number;
  safe_now_paisa: number;
  suggested_paisa: number;
  buffer_paisa: number;
  lowest_day: string;
  lowest_balance_paisa: number;
  history: SavingsMonth[];
  scale_up: { from_pct: number; to_pct: number; growth_pct: number; new_target_paisa: number; text: string } | null;
  method: string[];
  saved_total_paisa: number;
  goals: SavingsGoal[];
  goals_headline: string;
  jump?: { direction: "up" | "down"; last_profit_paisa: number; usual_profit_paisa: number; base_paisa: number; text: string } | null;
}

/** Something the owner saves toward; savings fill goals from the top. */
export interface SavingsGoal {
  id: string;
  name: string;
  kind: "custom" | "cushion" | "eid_stock" | "grow_shop";
  set_by: "owner" | "khatapilot";
  target_paisa: number;
  saved_paisa: number;
  remaining_paisa: number;
  pct: number;
  by_month: string | null;
  months_left: number | null;
  on_track: boolean | null;
  reached: boolean;
  why: string | null;
  text: string;
  created_at: string;
}

/** This calendar month's AI use for the shop, against its monthly budget. */
export interface LlmMonth {
  month: string;
  month_label: string;
  mode: string;
  model: string;
  offline_only: boolean;
  budget_usd: number | null;
  budget_bdt: number | null;
  spent_usd: number;
  spent_bdt: number;
  used_pct: number | null;
  capped: boolean;
  warn: boolean;
  resets_on: string;
  calls: number;
  unpriced_calls: number;
  tokens: { input: number; output: number; cache_read: number; cache_write: number };
  turns: Record<"model" | "rules" | "offline" | "capped", number>;
  free_turns: number;
  text: string;
}

export interface ReportLine {
  label: string;
  paisa: number;
  note: string | null;
}

export interface ReportMonthFigures {
  month: string;
  label: string;
  sales_paisa: number;
  sales_mobile_paisa: number;
  baki_given_paisa: number;
  baki_back_paisa: number;
  other_in_paisa: number;
  stock_bought_paisa: number;
  stock_paid_paisa: number;
  running_costs_paisa: number;
  costs: ReportLine[];
  household_paisa: number;
  savings_paisa: number;
  cash_profit_paisa: number;
  gross_margin_pct: number | null;
  closing_cash_paisa: number;
  baki_outstanding_paisa: number;
  payables_paisa: number;
  supplier_payments_on_time: number;
  supplier_payments_late: number;
  days: number;
}

export interface ReportShop {
  name: string;
  owner_name: string;
  shop_type: string;
  place: string;
  trading_since: string;
  accounts: string[];
}

export interface ReportIntegrity {
  provider: { checked: string; reachable: boolean; matches: boolean; problems: string[] };
  audit: { entries: number; verified: boolean; head: string };
  fingerprint: string;
}

export interface OwnerReport {
  kind: "owner";
  id: string;
  generated_at: string;
  month: string;
  title: string;
  period: string;
  shop: ReportShop;
  headline: string;
  figures: ReportMonthFigures;
  previous: ReportMonthFigures | null;
  money_in: ReportLine[];
  money_out: ReportLine[];
  own_money: ReportLine[];
  weeks: { label: string; money_in_paisa: number; money_out_paisa: number; profit_paisa: number }[];
  baki: {
    given_paisa: number;
    back_paisa: number;
    outstanding_end_paisa: number;
    customers_owing_now: number;
    owed_now_paisa: number;
    over_60_days_now_paisa: number;
    top_now: { name: string; owed_paisa: number; days: number | null }[];
  };
  bills: {
    payables_end_paisa: number;
    open_now_paisa: number;
    open_now: { payee: string; amount_paisa: number; due: string; overdue: boolean; priority: boolean }[];
  };
  payments: {
    count: number;
    total_paisa: number;
    not_completed: number;
    items: { day: string; to: string; amount_paisa: number; status: string; purpose: string }[];
  };
  savings: {
    share_pct: number | null;
    target_paisa: number | null;
    saved_paisa: number;
    saved_total_paisa: number;
    goals: { name: string; target_paisa: number; saved_paisa: number; text: string }[];
  };
  forecast: string;
  growth: { stage: string; text: string; moves: { title: string; text: string }[] } | null;
  notes: string[];
  security: { blocked: number; held_for_you: number; alerts: number; wrong_pin: number; stop_used: number };
  integrity: ReportIntegrity;
}

export interface LenderReport {
  kind: "lender";
  id: string;
  generated_at: string;
  month: string;
  title: string;
  period: string;
  shop: ReportShop;
  headline: string;
  months: ReportMonthFigures[];
  averages: {
    months: number;
    sales_paisa: number;
    cash_profit_paisa: number;
    gross_margin_pct: number | null;
    best: { month: string; profit_paisa: number };
    worst: { month: string; profit_paisa: number };
    steadiness: string;
    profit_swing_pct: number | null;
    sales_trend_pct_a_month: number | null;
  };
  customer_credit: {
    outstanding_end_paisa: number;
    given_paisa: number;
    paid_back_paisa: number;
    paid_back_pct: number | null;
    customers_owing_now: number;
    over_60_days_now_paisa: number;
  };
  obligations: {
    supplier_payables_end_paisa: number;
    supplier_payments_on_time: number;
    supplier_payments_late: number;
    on_time_pct: number | null;
    informal_loans_outstanding_paisa: number;
    applications_through_khatapilot: { lender: string; amount_paisa: number; status: string; day: string }[];
  };
  capacity: {
    monthly_business_income_paisa: number;
    household_paisa: number;
    left_after_household_paisa: number;
    max_instalment_paisa: number;
    fits: boolean;
    text: string;
  };
  credit_profile:
    | { included: false; why: string }
    | { included: true; band: string; band_words: string; summary: string; built: string; model: string; history_days: number; helps: string[]; hurts: string[] };
  savings_record: { share_pct: number | null; months_saved: number; months: number; saved_paisa: number; saved_total_paisa: number };
  notes: string[];
  declaration: string;
  integrity: ReportIntegrity;
}

export type MonthlyReport = OwnerReport | LenderReport;

export interface FlowPart {
  key: string;
  label: string;
  amount_paisa: number;
}

export interface Scenario {
  key: "as_now" | "reinvest" | "collect_and_reinvest";
  label: string;
  assumption: string;
  months: { month: string; label: string; profit_paisa: number; low_paisa: number; high_paisa: number; season: string | null }[];
  total_profit_paisa: number;
  savings_paisa: number;
  extra_vs_now_paisa: number;
}

export interface Lever {
  kind: "household" | "collect_baki" | "supplier_terms" | "rising_cost" | "margin" | "reinvest" | "cushion";
  title: string;
  text: string;
  cash_paisa: number;
  monthly_paisa: number;
  tab: "ledger" | "forecast" | "savings" | "chat";
}

export interface GrowthPlan {
  as_of: string;
  headline: string;
  flow: {
    month: string;
    label: string;
    money_in: FlowPart[];
    costs: FlowPart[];
    profit_paisa: number;
    uses: FlowPart[];
    gross_margin_pct: number;
    headline: string;
  };
  profile: {
    stage: "cushion_first" | "shrinking" | "ready_to_grow" | "steady" | "up_and_down" | "choppy";
    stage_label: string;
    stage_text: string;
    steadiness: "steady" | "moderate" | "choppy" | null;
    daily_swing_pct: number | null;
    trend: "growing" | "flat" | "shrinking";
    monthly_trend_pct: number;
    profit_swing_pct: number | null;
    reinvest_pct: number;
    reinvest_suggested_pct: number;
    reinvest_choice_pct: number | null;
    reinvest_why: string;
    reinvest_wait: string;
  };
  base_profit_paisa: number;
  scenarios: Scenario[];
  levers: Lever[];
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

export type AlertSegmentId = "security" | "money" | "cash" | "customers";
export type AlertPriority = "urgent" | "important" | "info";

/** One card in the alerts feed: an audit entry (or a same-day run of repeats) the owner should hear about. */
export interface AlertItem {
  id: string;
  segment: AlertSegmentId;
  priority: AlertPriority;
  title: string;
  body: string;
  tab: "chat" | "ledger" | "forecast" | "savings" | "credit" | "loans" | "controls" | "audit";
  count: number;
  seq: number;
  seqs: number[];
  ts: string;
  first_ts: string;
  channel: string;
  event: string;
}

export interface AlertSegment {
  id: AlertSegmentId;
  label: string;
  hint: string;
  items: AlertItem[];
  counts: Record<AlertPriority, number>;
}

export interface AlertFeed {
  chain_id: string;
  latest_seq: number;
  segments: AlertSegment[];
}

// Endpoints the platform agent is adding (GET/PUT /mcp/settings, GET /mcp/log, GET /reminders/ics) and a Google
// connection check nobody owns yet. shortcut: kept in memory here, with the shapes from feat/platform's mcp_settings.py;
// move them into api.ts and mock.ts once the endpoints are on main.
import { BASE, MOCK } from '../api'

export type McpSettings = {
  enabled: boolean
  allow: Record<string, string[]> // document type -> fields; ["*"] is every field plus the page text; missing type = hidden
  env_override: boolean // SNIFF_MCP_ALLOW is set, so the app's switches do nothing
  fields: Record<string, string[]> // every type and its fields, so the UI does not copy the schema
}
export type McpLogEntry = { time: string; tool: string; args: Record<string, unknown>; ids: number[]; refused?: boolean }

const FIELDS: Record<string, string[]> = {
  id_card: ['name', 'id_number', 'date_of_birth', 'issue_date', 'expiry_date'],
  utility_bill: ['provider', 'account_number', 'billing_month', 'bill_date', 'due_date', 'current_charges', 'previous_balance', 'tax', 'amount_due', 'payment_status', 'payment_date'],
  certificate: ['holder_name', 'title', 'institution', 'issue_date'],
  receipt: ['merchant', 'date', 'subtotal', 'tax', 'total_amount', 'warranty_until'],
  passport: ['name', 'passport_number', 'nationality', 'date_of_birth', 'issue_date', 'expiry_date'],
  transcript: ['student_name', 'registration_number', 'institution', 'program', 'cgpa', 'issue_date'],
  bank_statement: ['bank', 'account_number', 'period_start', 'period_end', 'opening_balance', 'total_credits', 'total_debits', 'closing_balance'],
  warranty_card: ['product', 'brand', 'serial_number', 'purchase_date', 'warranty_until'],
  offer_letter: ['employer', 'candidate_name', 'position', 'monthly_salary', 'offer_date', 'start_date'],
  insurance_policy: ['insurer', 'policy_number', 'insured_name', 'premium', 'start_date', 'end_date'],
  tenancy_agreement: ['landlord', 'tenant', 'property_address', 'monthly_rent', 'security_deposit', 'start_date', 'end_date'],
  fee_voucher: ['student_name', 'registration_number', 'institution', 'semester', 'amount', 'issue_date', 'due_date'],
  other: [],
}
const HIDDEN = ['passport', 'bank_statement', 'offer_letter', 'transcript', 'id_card'] // off by default
let mcp: McpSettings = {
  enabled: false, env_override: false, fields: FIELDS,
  allow: Object.fromEntries(Object.keys(FIELDS).filter((t) => !HIDDEN.includes(t)).map((t) => [t, ['*']])),
}
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString().slice(0, 19)
const log: McpLogEntry[] = [
  { time: ago(3), tool: 'search_documents', args: { query: 'electricity bill october' }, ids: [3, 4] },
  { time: ago(4), tool: 'ask', args: { question: 'When does my passport expire?' }, ids: [] },
  { time: ago(9), tool: 'list_upcoming', args: { days: 30 }, ids: [3, 4] },
  { time: ago(1440), tool: 'search_documents', args: { query: 'degree certificate' }, ids: [], refused: true },
]

const wait = <T,>(v: T) => new Promise<T>((r) => setTimeout(() => r(v), 80))

export const pending = {
  mcpSettings: () => wait(mcp),
  saveMcp: (enabled: boolean, allow: McpSettings['allow']) => wait((mcp = { ...mcp, enabled, allow })),
  mcpLog: () => wait(log.slice(0, 50)),
  googleConnected: () => wait(true),
  // Opens the reminder in the default calendar app; nothing leaves the laptop.
  ics: (fact_id: number) => (MOCK ? wait(null) : Promise.resolve(window.open(`${BASE}/reminders/ics?fact_id=${fact_id}`, '_blank'))),
}

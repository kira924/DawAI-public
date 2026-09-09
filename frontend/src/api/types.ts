export type UserRole = "super_admin" | "manager" | "pharmacist";

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  token_type: string;
  expires_in: number;
}

export interface UserProfile {
  id: number;
  email: string;
  full_name: string;
  role: UserRole;
  tenant_id: number;
  created_at: string;
}

export interface Product {
  id: number;
  tenant_id: number;
  catalog_product_id: number | null;
  name: string;
  barcode: string | null;
  category: "medicine" | "cosmetic" | "medical_supply" | "baby_care" | "other";
  price: string;
  is_divisible: boolean;
  parts_per_unit: number;
  part_name: string | null;
  part_price: string | null;
  expiry_date: string | null;
  return_policy: "standard" | "unopened_only" | "non_returnable";
  total_parts: number;
  sellable_parts: number;
  expired_parts: number;
  quarantined_parts: number;
  tracked_physical_parts: number;
  available_boxes: number;
  available_parts: number;
}

export interface CatalogProduct {
  id: number;
  display_name: string;
  name_en: string | null;
  name_ar: string | null;
  active_ingredients: string | null;
  manufacturer: string | null;
  reference_price: string | null;
  units_per_package: number | null;
  package_size: number | null;
  package_unit: string | null;
  dosage_form: string | null;
  therapeutic_category: string | null;
  barcode: string | null;
  data_quality_flags: string[];
  needs_review: boolean;
}

export interface ProductInput {
  name: string;
  barcode: string | null;
  catalog_product_id: number;
  category: "medicine";
  price: string;
  is_divisible: boolean;
  parts_per_unit: number;
  part_name: string | null;
  part_price: string | null;
  expiry_date: null;
  return_policy: "standard" | "unopened_only" | "non_returnable";
  initial_boxes: number;
  initial_parts: number;
  initial_batch: { batch_number: string; expiry_date: string } | null;
  confirm_near_expiry: boolean;
}

export interface ProductBatch {
  id: number;
  batch_number: string;
  expiry_date: string;
  quantity: number;
  product_id: number;
  tenant_id: number;
  days_until_expiry: number;
  expiry_status: "valid" | "warning" | "critical" | "expired";
  is_sellable: boolean;
}

export interface Shift {
  id: number;
  status: "OPEN" | "CLOSED";
  start_time: string;
  end_time: string | null;
  opening_balance: string;
  expected_closing_balance: string;
  actual_closing_balance: string | null;
  difference: string | null;
  user_id: number;
  tenant_id: number;
  opening_business_date: string;
  closing_business_date: string | null;
}

export interface Customer {
  id: number;
  name: string;
  phone: string | null;
  credit_limit: string;
  total_debt: string;
  credit_balance: string;
  created_at: string;
  tenant_id: number;
}

export interface InvoiceItemInput {
  product_id: number;
  quantity: number;
  sale_unit_price: string;
}

export interface InvoiceInput {
  idempotency_key: string;
  payment_type: "cash" | "credit";
  customer_id: number | null;
  items: InvoiceItemInput[];
}

export interface InvoiceItem {
  id: number;
  invoice_id: number;
  product_id: number;
  quantity: number;
  list_unit_price: string;
  sale_unit_price: string;
  subtotal: string;
}

export interface Invoice {
  id: number;
  idempotency_key: string;
  total_amount: string;
  outstanding_amount: string;
  payment_type: "cash" | "credit";
  customer_id: number | null;
  created_at: string;
  user_id: number;
  tenant_id: number;
  items: InvoiceItem[];
}

export interface DashboardSummary {
  business_date: string;
  timezone: string;
  total_sales: string;
  total_expenses: string;
  total_returns: string;
  net_revenue: string;
  low_stock_count: number;
}

export interface ApiValidationError {
  loc?: Array<string | number>;
  msg?: string;
}

export interface ApiErrorBody {
  detail?: string | ApiValidationError[];
}

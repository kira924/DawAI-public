# DawAI API Contract

## Scope and Versioning

- `/api/v1` is the canonical public API prefix. New consumers and endpoint documentation must use it.
- `/api` is a temporary compatibility alias for the current browser client. It is excluded from OpenAPI and returns `Deprecation: true` plus `X-API-Version: 1`.
- `/`, `/health/live`, `/health/ready`, and the compatibility `/health` endpoint are operational endpoints and remain unversioned.
- Removing or changing the legacy alias requires measured consumer usage, a communicated migration window, and explicit owner approval. No removal date is currently committed.
- A change is breaking when an existing valid request stops working, a response field is removed or changes meaning/type, an enum is narrowed, authentication or authorization requirements change, or ordering/pagination semantics change incompatibly. Breaking changes require a new major API prefix or an explicitly approved migration plan.
- Additive optional fields, new endpoints, and new optional query parameters are normally non-breaking, but still require OpenAPI and response tests.

## Errors and Correlation

Every `/api/v1` error uses this envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Request validation failed",
    "request_id": "7d457869-3fcf-4f73-b2f2-f778fce36f46",
    "details": []
  }
}
```

- `code` is a stable machine-readable category. Clients must not branch on `message`.
- `message` is a safe human-readable summary and must never expose internal exception text or secrets.
- `request_id` matches the `X-Request-ID` response header. A caller-supplied ID is accepted only when it is 1-64 characters and contains letters, digits, `.`, `_`, or `-`; otherwise the server generates a UUID.
- `details` is optional. Validation details contain only `field`, `message`, and `code`; rejected raw input is not echoed.
- The legacy `/api` alias preserves the existing FastAPI `detail` envelope and standard validation location/message/type fields during the migration window, but raw rejected input is removed to prevent secret disclosure.

## Request Models and Domain Enums

- JSON request bodies reject unknown fields. This fail-closed validation applies to both `/api/v1` and the legacy `/api` alias because accepting misspelled or ignored mutation fields is unsafe; protocol-generated form bodies keep their protocol-defined shape.
- Finite public domain values are serialized as string enums whose values match the canonical database constraints and application rules.
- Adding an enum value is an additive contract change that still requires OpenAPI and runtime coverage. Removing, renaming, or narrowing an accepted enum value is breaking.
- A legacy value that may appear in historical responses but must not be submitted by clients belongs in a response-only enum, separate from the writable input enum.
- `subscription_plan` remains an open string until the commercial plan and entitlement taxonomy is confirmed. It must not be converted into an invented enum.

## Collections and Ordering

- Ordinary collection endpoints use `skip >= 0` and `1 <= limit <= 100`.
- Immutable audit/history collections may allow `1 <= limit <= 500`.
- Search endpoints may use a smaller explicit maximum and may omit `skip` where the ranked-result contract is intentionally prefix-oriented.
- Every offset-paginated database query must have a deterministic total order with an immutable tie-breaker such as `id`.
- Tenant, user, product, customer, supplier, and invoice lists currently use ascending `id`. Batch lists use ascending expiry then `id`.
- Expenses, purchase orders, supplier payments, purchase returns, stock movements, stock counts, quarantine items, support grants, and financial transactions use newest-first event time then descending `id`. Support audit events use oldest-first event time then ascending `id`.
- Search results use their documented relevance rank with an immutable identifier tie-breaker.
- Current list responses remain plain arrays for compatibility. Cursor pagination and page metadata require a separately reviewed additive or versioned contract.

## Replenishment and Reporting Semantics

- A replenishment policy is either explicitly unconfigured with both values null, or satisfies `target_stock_parts > reorder_point_parts >= 0`. Existing and newly created products are not assigned an invented default.
- A product becomes due when current sellable parts are less than or equal to its reorder point. Suggested order parts restore the product to its configured target and exclude expired and quarantined stock.
- `manual_policy` is the only current recommendation source. Future forecast sources must be additive, measurable against the manual baseline, and advisory before any automated action is considered.
- Pharmacists and Managers may read operational recommendations. Only Managers with inventory-control permission may set or clear a policy. Recommendations and policy writes are tenant-scoped.
- Dashboard fields `gross_sales`, `sales_returns`, `net_sales`, `operating_expenses`, and `operating_result_before_cogs_and_tax` state the implemented arithmetic explicitly. The retained `net_revenue` field is deprecated compatibility output and must not be represented as accounting profit.
- Dashboard financial values belong to `business_date`; its replenishment count is a current stock snapshot whose separate `inventory_date` is returned explicitly. Historical stock reconstruction is not implied.

## Contract Validation

- OpenAPI exposes only canonical `/api/v1` domain routes and advertises `/api/v1/auth/login` for OAuth2.
- Every contract change requires semantic OpenAPI assertions and runtime response tests.
- Legacy compatibility must remain covered until the alias is deliberately removed.
- PostgreSQL-backed integration tests remain required for behavior that depends on transactions, constraints, locking, or production database semantics.

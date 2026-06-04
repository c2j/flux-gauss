# Task 2: Business SQL Packages — Comparison Report

**Date:** 2026-05-23  
**Scope:** pkg_order, pkg_payment, pkg_inventory, pkg_product  
**Comparators:** dest_py (Python converter) vs dest_ru (Rust converter)

---

## 1. Procedure Coverage Summary

| SQL Package | SQL Procedures | dest_py Methods | dest_ru Methods | Coverage Match? |
|---|---|---|---|---|
| pkg_order | create_order, cancel_order, get_order_detail, batch_create_orders, complete_order | createOrder, cancelOrder, getOrderDetail, batchCreateOrders, completeOrder | createOrder, cancelOrder, getOrderDetail, batchCreateOrders, completeOrder | YES (5/5 both) |
| pkg_payment | process_payment, refund_payment, query_payment_status, reconcile_payments | processPayment, refundPayment, queryPaymentStatus, reconcilePayments | processPayment, refundPayment, queryPaymentStatus, reconcilePayments | YES (4/4 both) |
| pkg_inventory | check_stock, reserve_stock, release_stock, sync_from_supplier | checkStock, reserveStock, releaseStock, syncFromSupplier | checkStock, reserveStock, releaseStock, syncFromSupplier | YES (4/4 both) |
| pkg_product | get_product_info, search_products, update_product_price, batch_update_prices, deactivate_product | getProductInfo, searchProducts, updateProductPrice, batchUpdatePrices, deactivateProduct | getProductInfo, searchProducts, updateProductPrice, batchUpdatePrices, deactivateProduct | YES (5/5 both) |

**Result:** Both converters produce all 18 procedures. No missing methods.

---

## 2. Cross-Service Call Verification

### 2.1 pkg_order → pkg_inventory

| SQL Call | dest_py Java | dest_ru Java | Verdict |
|---|---|---|---|
| `CALL pkg_inventory.reserve_stock(p_product_id, p_qty)` in create_order | `inventoryService.reserveStock(pProductId, pQty)` | `inventoryService.reserveStock(pProductId, pQty)` | CORRECT both |
| `CALL pkg_inventory.release_stock(v_product_id, v_qty)` in cancel_order | `inventoryService.releaseStock(vProductId, vQty)` | `inventoryService.releaseStock(vProductId, vQty)` | CORRECT both |

### 2.2 pkg_order → pkg_order (self-call)

| SQL Call | dest_py Java | dest_ru Java | Verdict |
|---|---|---|---|
| `CALL pkg_order.create_order(p_user_id, 1, 1)` in batch_create_orders | `this.createOrder(pUserId, 1, 1)` | `this.createOrder(pUserId, 1, 1)` | CORRECT both |

### 2.3 pkg_inventory → pkg_inventory (self-call)

| SQL Call | dest_py Java | dest_ru Java | Verdict |
|---|---|---|---|
| `CALL pkg_inventory.check_stock(p_product_id, p_qty)` in reserve_stock | `this.checkStock(pProductId, pQty)` | `this.checkStock(pProductId, pQty)` | CORRECT both |

### 2.4 All → pkg_common calls

| SQL Call | dest_py Java | dest_ru Java | Verdict |
|---|---|---|---|
| `PERFORM pkg_common.log_operation(...)` (10 occurrences) | `commonService.logOperation(...)` | `commonService.logOperation(...)` | CORRECT both |
| `PERFORM pkg_common.send_notification(...)` (5 occurrences) | `commonService.sendNotification(...)` | `commonService.sendNotification(...)` | CORRECT both |
| `pkg_common.get_sys_date()` in create_order INSERT | Replaced with `CURRENT_TIMESTAMP` in XML | Replaced with `CURRENT_TIMESTAMP` in XML | CORRECT both |
| `pkg_common.format_amount(p_amount)` in process_payment | `commonService.formatAmount(pAmount)` | `vFormatted = null;` (NOT called!) | **CRITICAL: dest_ru bug** |
| `pkg_common.format_amount(p_new_price)` in update_product_price | `commonService.formatAmount(pNewPrice)` | `vFormatted = null;` (NOT called!) | **CRITICAL: dest_ru bug** |

### 2.5 Dependency Injection

| Package | dest_py Injected Services | dest_ru Injected Services | Match? |
|---|---|---|---|
| OrderService | OrderMapper, InventoryService, CommonService | OrderMapper, CommonService, InventoryService | YES (same 3, different order) |
| PaymentService | PaymentMapper, CommonService | PaymentMapper, CommonService | YES |
| InventoryService | InventoryMapper, CommonService | InventoryMapper, CommonService | YES |
| ProductService | ProductMapper, CommonService | ProductMapper, CommonService | YES |

---

## 3. Detailed Differences by Package

### 3.1 pkg_order — OrderService

#### cancelOrder — SELECT INTO variable capture

**SQL logic:**
```sql
SELECT product_id, qty INTO v_product_id, v_qty
FROM t_orders WHERE id = p_order_id;
```

**dest_py:**
```java
Long vProductId = 0L;
Integer vQty = 0;
Map<String, Object> _row = orderMapper.selectCancelOrder(pOrderId);
if (_row == null) _row = java.util.Collections.emptyMap();
inventoryService.releaseStock(vProductId, vQty);
```
- Mapper: `selectCancelOrder(@Param("pOrderId") Long pOrderId)` — returns `Map<String, Object>`
- **BUG (dest_py):** SELECT result is stored in `_row` but `vProductId` and `vQty` are NEVER populated from `_row`. The variables remain at their initialized defaults (0, 0). The releaseStock call uses (0, 0) instead of actual DB values.

**dest_ru:**
```java
Integer vQty = 0;
Long vProductId = 0L;
Map<String, Object> _row = orderMapper.selectCancelOrder(pOrderId, vProductId, vQty);
inventoryService.releaseStock(vProductId, vQty);
```
- Mapper: `selectCancelOrder(@Param("pOrderId") Long, @Param("vProductId") Long, @Param("vQty") Integer)` — extra unused params
- **BUG (dest_ru):** Same as dest_py — `_row` is not unpacked. Additionally, `vProductId` and `vQty` are passed as params to the mapper (useless — they're input-only). Both converters fail to extract values from the result map.

**Severity:** **CRITICAL** (both) — Wrong inventory released (always releases 0 qty for product 0).

---

### 3.2 pkg_payment — PaymentService

#### process_payment — format_amount function call

**SQL:**
```sql
v_formatted := pkg_common.format_amount(p_amount);
```

**dest_py:** `vFormatted = commonService.formatAmount(pAmount);` — **CORRECT**

**dest_ru:** `vFormatted = null;` — **WRONG**, the function call is lost entirely.

**Severity:** **CRITICAL** (dest_ru only) — Business logic (formatting) is silently dropped.

#### query_payment_status — COALESCE / RETURN handling

**SQL:**
```sql
SELECT status INTO v_status FROM t_payments WHERE order_id = p_order_id;
RETURN COALESCE(v_status, 'NO_PAYMENT');
```

**dest_py:**
```java
String vStatus = null;
vStatus = paymentMapper.selectQueryPaymentStatus(pOrderId);
return Objects.requireNonNullElse(vStatus, "NO_PAYMENT");
```
- Mapper: `String selectQueryPaymentStatus(@Param("pOrderId") Long pOrderId)` — correct signature
- **CORRECT** — COALESCE correctly translated to `Objects.requireNonNullElse()`

**dest_ru:**
```java
String vStatus = null;
vStatus = paymentMapper.selectQueryPaymentStatus(pOrderId, vStatus);
return (vStatus != null ? vStatus : "NO_PAYMENT");
```
- Mapper: `String selectQueryPaymentStatus(@Param("pOrderId") Long, @Param("vStatus") String)` — extra unused param
- COALESCE logic: `(vStatus != null ? vStatus : "NO_PAYMENT")` — functionally correct
- **Minor issue:** Extra `vStatus` param passed to mapper (unused, but harmless)

**Severity:** **Minor** (dest_ru) — Extra param in mapper; logic otherwise correct.

#### reconcile_payments — DATE cast syntax

**SQL:**
```sql
WHERE DATE(paid_at) = p_date::DATE
```

**dest_py XML:**
```xml
where CAST(paid_at AS DATE) = #{pDate}
```
- Converts `p_date::DATE` to `CAST(? AS DATE)` — drops the `::DATE` cast, compares TIMESTAMP to VARCHAR

**dest_ru XML:**
```xml
where CAST(paid_at AS DATE) = #{pDate} :: date
```
- Preserves the `::date` cast on the parameter side — **more correct** for PostgreSQL

**Severity:** **Minor** (dest_py) — Potential type mismatch in strict SQL modes. dest_ru is more faithful.

---

### 3.3 pkg_inventory — InventoryService

#### check_stock — RAISE EXCEPTION handling

**SQL:**
```sql
IF v_available < p_qty THEN
    RAISE EXCEPTION 'Insufficient stock: % < %', v_available, p_qty;
END IF;
```

**dest_py:**
```java
if (vAvailable < pQty) {
    throw new BusinessException(String.format("Insufficient stock: %s < %s", vAvailable, pQty));
}
```
- Correctly uses `String.format()` with `%s` format specifiers
- Also has null safety: `if (vAvailable == null) vAvailable = 0;`

**dest_ru:**
```java
if (vAvailable < pQty) {
    throw new BusinessException(String.format("'Insufficient stock: {} < {}'", vAvailable, pQty));
}
```
- Uses `String.format()` but with `{}` placeholders (SLF4J-style) — **BUG**: `String.format` uses `%s`, not `{}`
- Error message will literally contain `{}` instead of actual values
- No null safety check on `vAvailable`

**Severity:** **CRITICAL** (dest_ru) — Error message formatting is broken; exception will show literal `{}` instead of values. Also potential NPE if `vAvailable` is null.

#### check_stock — SELECT INTO variable capture

**SQL:**
```sql
SELECT stock_qty INTO v_available FROM t_products WHERE id = p_product_id;
```

**dest_py:**
```java
vAvailable = inventoryMapper.selectCheckStock(pProductId, pQty);
```
- Mapper: `Integer selectCheckStock(@Param("pProductId") Long, @Param("pQty") Integer)` — extra `pQty` param but result correctly assigned

**dest_ru:**
```java
vAvailable = inventoryMapper.selectCheckStock(pProductId, pQty, vAvailable);
```
- Mapper: `Integer selectCheckStock(@Param("pProductId") Long, @Param("pQty") Integer, @Param("vAvailable") Integer)` — extra `vAvailable` param passed

**Severity:** **Minor** (both) — Extra unused params in mapper interface. Functionally works since `vAvailable` is correctly assigned from return value.

---

### 3.4 pkg_product — ProductService

#### update_product_price — format_amount function call

**SQL:**
```sql
v_formatted := pkg_common.format_amount(p_new_price);
```

**dest_py:** `vFormatted = commonService.formatAmount(pNewPrice);` — **CORRECT**

**dest_ru:** `vFormatted = null;` — **WRONG**, function call lost (same pattern as process_payment)

**Severity:** **CRITICAL** (dest_ru only)

---

## 4. Mapper Interface Differences

### Pattern: Extra OUT-variable params in dest_ru

dest_ru consistently passes local variables as additional `@Param` arguments to mapper methods where the SQL uses `SELECT ... INTO var`:

| Method | dest_py Mapper Signature | dest_ru Mapper Signature |
|---|---|---|
| selectCancelOrder | `(pOrderId)` | `(pOrderId, vProductId, vQty)` |
| selectQueryPaymentStatus | `(pOrderId)` | `(pOrderId, vStatus)` |
| selectCheckStock | `(pProductId, pQty)` | `(pProductId, pQty, vAvailable)` |

These extra params are never referenced in the XML SQL and serve no purpose. The XML only uses `#{pOrderId}` etc.

**Severity:** **Minor** — Dead parameters; no runtime impact but indicates the Rust converter doesn't distinguish between input params and INTO targets.

---

## 5. Mapper XML Formatting Differences

| Aspect | dest_py | dest_ru |
|---|---|---|
| SQL formatting | Multi-line, indented | Single-line, compact |
| Source comments | Exact line ranges (e.g., `pkg_order.sql:1-12`) | Always `:1-1` (wrong line ranges) |
| XML element formatting | Standard indentation | No indentation |
| SQL equivalence | Functionally identical | Functionally identical |

**Severity:** **Minor** — dest_ru source tracing is less useful (always reports line 1). dest_py has precise line tracking.

---

## 6. Summary of All Differences

### CRITICAL Issues

| # | Package | Converter | Issue | Impact |
|---|---|---|---|---|
| C1 | pkg_order / cancelOrder | **BOTH** | SELECT INTO result not unpacked — `vProductId` and `vQty` remain 0 | Wrong inventory released |
| C2 | pkg_payment / processPayment | **dest_ru** | `format_amount()` call lost — assigned `null` instead | Business logic silently dropped |
| C3 | pkg_product / updateProductPrice | **dest_ru** | `format_amount()` call lost — assigned `null` instead | Business logic silently dropped |
| C4 | pkg_inventory / checkStock | **dest_ru** | Exception message uses `{}` instead of `%s` in `String.format()` | Error message broken at runtime |

### MAJOR Issues

None.

### MINOR Issues

| # | Package | Converter | Issue | Impact |
|---|---|---|---|---|
| M1 | pkg_payment / reconcilePayments | **dest_py** | Drops `::DATE` cast on parameter | Potential type mismatch |
| M2 | pkg_payment / queryPaymentStatus | **dest_ru** | Extra `vStatus` param in mapper interface | Dead code |
| M3 | pkg_inventory / checkStock | **dest_ru** | No null safety on `vAvailable` | Potential NPE |
| M4 | pkg_inventory / checkStock | **BOTH** | Extra unused params in mapper | Dead code |
| M5 | All packages | **dest_ru** | Source line ranges always `1-1` | Debug tracing impaired |
| M6 | All packages | **dest_ru** | Single-line XML formatting | Readability only |

---

## 7. Shared Issues (Both Converters)

1. **SELECT INTO not unpacked (C1):** Both converters generate a mapper call that returns a `Map<String, Object>` for `SELECT INTO` statements, but neither extracts the column values from the map into local variables. This is the most significant correctness gap.

2. **No SAVEPOINT/COMMIT/ROLLBACK in these SQL files:** None of the 4 business packages use explicit transaction control. Both converters apply `@Transactional` to mutating methods — appropriate for this case.

3. **No EXCEPTION WHEN OTHERS blocks:** None of the 4 packages have exception handlers, so no try/catch conversion was needed.

4. **No OUT/INOUT parameters:** All procedures use IN params only. No Map/DTO passing needed.

5. **Return values ignored for void SELECT:** `get_order_detail`, `get_product_info`, `search_products` execute SELECTs whose results are stored in `_result` but never returned — matching the SQL (procedures, not functions).

---

## 8. Converter Scorecard

| Criterion | dest_py | dest_ru |
|---|---|---|
| Procedure coverage | 18/18 | 18/18 |
| Cross-service calls | Correct | Correct |
| Function calls preserved | YES | NO (format_amount lost) |
| SELECT INTO handling | Broken (no unpack) | Broken (no unpack) |
| Exception message format | Correct | Broken ({} vs %s) |
| COALESCE translation | Correct (Objects.requireNonNullElse) | Correct (ternary) |
| Null safety | Better | Worse |
| Source tracing | Precise line ranges | Always line 1 |
| XML readability | Good | Poor (single-line) |
| **Critical issues** | **1** (shared SELECT INTO) | **4** (SELECT INTO + 2 lost calls + broken format) |
| **Minor issues** | **2** | **5** |

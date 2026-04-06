# Terminal Return/Refund & Receipt Correction Architecture Research

## 🎯 PURPOSE
Deep analysis of all interconnected systems before implementing Terminal-based Return/Refund and Incomplete Stock Correction features.

---

## 📊 **DATABASE COLLECTIONS AFFECTED**

### **Direct Impact (Must Update)**
1. **`invoices`** — Receipt data (items, totals, balance, status)
2. **`returns`** — Return/refund transaction records (RMA)
3. **`expenses`** — Refund recorded as expense
4. **`inventory`** — Stock adjustments (return to shelf)
5. **`inventory_movements`** — Audit trail of stock changes
6. **`inventory_corrections`** — Pull-out/loss records
7. **`fund_wallets`** — Cashier/Safe balance tracking
8. **`wallet_movements`** — Fund movement audit trail
9. **`safe_lots`** — Safe fund lot tracking (FIFO deduction)
10. **`customers`** — Balance adjustments (if credit customer)

### **Indirect Impact (May Need to Check)**
11. **`daily_closings`** — Date validation (cannot modify closed days)
12. **`sales_log`** — Historical sales data (already immutable post-sync)
13. **`notifications`** — Admin alerts for pull-out losses
14. **`upload_sessions`** — File attachments linked to records

### **Audit & Compliance**
15. **`invoice_corrections`** — NEW collection (to create)
16. **`sms_queue`** — SMS notifications for balance changes

---

## 🔍 **EXISTING RETURN & REFUND FLOW** (`POST /api/returns`)

### **Input Data**
```json
{
  "branch_id": "...",
  "return_date": "2026-01-15",
  "customer_name": "Juan Dela Cruz",
  "customer_type": "credit",
  "reason": "Defective",
  "invoice_number": "KS-20260115-0042",
  "notes": "Optional notes",
  "items": [
    {
      "product_id": "...",
      "product_name": "Rice 25kg",
      "sku": "RICE-25",
      "category": "Grains",
      "unit": "bag",
      "quantity": 2,
      "condition": "damaged",
      "inventory_action": "pullout",
      "refund_price": 150.00,
      "cost_price": 120.00
    }
  ],
  "refund_method": "full",
  "refund_amount": 300.00,
  "fund_source": "cashier"
}
```

### **Processing Steps**
1. **Validate Fund Balance**
   - Checks cashier wallet OR safe lots
   - Ensures sufficient funds for refund
   - Raises error if insufficient

2. **Generate RMA Number**
   - Format: `RTN-YYYYMMDD-####`
   - Sequential counter per day

3. **Process Each Item**
   - **Inventory Action: `shelf`**
     - Increments `inventory.quantity`
     - Logs movement: `return_to_shelf`
     - Stock returns to available inventory
   
   - **Inventory Action: `pullout`**
     - Does NOT return stock
     - Creates `inventory_corrections` record
     - Tracks loss value
     - Logs movement: `return_pullout` (qty 0, informational)
   
   - **Veterinary Items**
     - ALWAYS forced to `pullout` (regulatory)

4. **Record Refund as Expense**
   - Creates expense record:
     - Category: "Customer Return Refund"
     - Fund source: cashier or safe
     - Links to RMA number

5. **Deduct Funds**
   - **From Cashier:** `update_cashier_wallet(branch_id, -amount, ref)`
   - **From Safe:** Deduct from safe lots (FIFO), update `safe_lots`

6. **Save Return Record**
   - Stores complete return transaction in `returns` collection
   - Includes all items, RMA, amounts, timestamps

7. **Notify Admins** (if pull-out losses)
   - Creates notification for owner/admin
   - Shows loss value and items

8. **Audit Trail**
   - `inventory_movements` — Stock changes
   - `wallet_movements` — Fund deductions
   - `inventory_corrections` — Pull-out losses
   - `expenses` — Refund expense

### **KEY CHARACTERISTICS**
- ✅ **Original invoice UNTOUCHED** — Remains as-is for accountability
- ✅ **Separate RMA record** — Full audit trail
- ✅ **No date restrictions** — Can process returns anytime
- ✅ **Fund validation** — Ensures sufficient balance
- ✅ **Stock returns** — Adds back to inventory (if sellable)

---

## 📅 **DAILY CLOSE SYSTEM** (`/api/daily-close`)

### **Purpose**
Locks accounting periods to prevent retroactive changes.

### **How It Works**
1. **Last Close Date Lookup**
   ```python
   last_close = await db.daily_closings.find_one(
       {"branch_id": branch_id, "status": "closed"},
       sort=[("date", -1)]
   )
   last_close_date = last_close["date"] if last_close else None
   ```

2. **Date Validation Check**
   ```python
   if invoice_date <= last_close_date:
       raise HTTPException(400, "Cannot modify — day already closed")
   ```

3. **What Gets Locked**
   - Sales transactions on that date
   - Expenses on that date
   - Invoices with `order_date` on that date
   - Fund movements on that date

4. **What's Still Allowed**
   - New transactions on unclosed dates
   - Return & Refund (creates new records, doesn't modify old)
   - Payments on existing invoices (updates balance, not historical data)

### **Close Wizard Collections Used**
- `daily_closings` — Close status per branch per date
- `sales_log` — Sales summary (immutable after sync)
- `expenses` — Expense totals
- `invoices` — Invoice counts and balances
- `fund_wallets` — Starting/ending cashier balance
- `safe_lots` — Safe movements
- `wallet_movements` — All fund changes

---

## 💰 **FUND MANAGEMENT SYSTEM**

### **Wallet Types**
1. **Cashier Wallet**
   - Direct balance field: `fund_wallets.balance`
   - Updated via: `update_cashier_wallet(branch_id, amount, ref)`
   - Used for: Daily operations, cash sales, refunds

2. **Safe Wallet**
   - Lot-based system (`safe_lots` collection)
   - Each deposit creates a new lot
   - Withdrawals use FIFO (oldest lots first)
   - Balance = sum of `remaining_amount` across all lots

3. **Digital Wallet**
   - Platform-specific (GCash, Maya, etc.)
   - Direct balance field
   - Used for: Digital payment receipts

4. **Bank Wallet**
   - Direct balance
   - Admin-only visibility
   - Requires TOTP for transactions

### **Deduction Logic (`deduct_from_fund_source`)**
```python
if fund_source == "safe":
    # Find all lots with remaining balance
    lots = await db.safe_lots.find({
        "wallet_id": safe_wallet["id"],
        "remaining_amount": {"$gt": 0}
    }).sort("remaining_amount", -1)  # Largest first
    
    # Deduct from lots until amount covered
    remaining = amount
    for lot in lots:
        take = min(lot["remaining_amount"], remaining)
        await db.safe_lots.update_one(
            {"id": lot["id"]},
            {"$inc": {"remaining_amount": -take}}
        )
        remaining -= take
    
    # Record safe movement
    await record_safe_movement(branch_id, -amount, ref)
    
elif fund_source == "digital":
    await update_digital_wallet(branch_id, -amount, ref, platform=method)
    
else:  # cashier
    await update_cashier_wallet(branch_id, -amount, ref)
```

### **Critical Wallet Rules**
1. **Returns/Refunds → ALWAYS deduct from source wallet**
2. **Expense voids → Return to ORIGINAL source** (not always cashier!)
3. **Fund transfers → Require PIN/TOTP authorization**
4. **Negative balances → Generally not allowed** (except overdraft scenarios)

---

## 📝 **INVOICE SYSTEM** (`/api/invoices`)

### **Invoice Schema (Relevant Fields)**
```python
{
  "id": "...",
  "invoice_number": "KS-20260115-0042",
  "prefix": "KS",
  "customer_id": "...",
  "customer_name": "Juan Dela Cruz",
  "branch_id": "...",
  "order_date": "2026-01-15",
  "invoice_date": "2026-01-15",
  "due_date": "2026-01-22",
  "items": [
    {
      "product_id": "...",
      "product_name": "Rice 25kg",
      "quantity": 5,
      "rate": 150.00,
      "total": 750.00
    }
  ],
  "subtotal": 1850.00,
  "grand_total": 1850.00,
  "amount_paid": 0,
  "balance": 1850.00,
  "status": "unpaid",  // or "paid", "partial", "voided"
  "payment_type": "cash",
  "payment_method": "Cash",
  "fund_source": "cashier",
  "release_mode": "full",  // or "partial"
  "payments": [
    {
      "id": "...",
      "amount": 500.00,
      "date": "2026-01-16",
      "method": "Cash",
      "recorded_by": "Admin Name",
      "recorded_at": "2026-01-16T10:30:00Z"
    }
  ],
  "created_at": "2026-01-15T14:20:00Z"
}
```

### **Invoice Update Rules**
1. **Cannot modify if date is closed**
   ```python
   closed_doc = await db.daily_closings.find_one({
       "branch_id": branch_id,
       "date": invoice["order_date"],
       "status": "closed"
   })
   if closed_doc:
       raise HTTPException(400, "Cannot update — date closed")
   ```

2. **Voiding requires PIN**
3. **Payment history is append-only** (never delete payments)
4. **Balance recalculation** must be atomic
5. **Customer balance sync** required for credit customers

---

## 🧾 **EXPENSE SYSTEM** (`/api/expenses`)

### **Expense Categories** (Relevant)
- "Customer Return Refund" — Created by return flow
- "Supplier Payment" — AP payments
- "Employee Advance" — Cash advances
- "Customer Cash-out" — Loans to customers
- "Farm Expense" — Farm services (creates linked invoice)
- "Miscellaneous" — General

### **Expense Record Structure**
```python
{
  "id": "...",
  "branch_id": "...",
  "category": "Customer Return Refund",
  "description": "Refund — RTN-20260115-0023 — Juan Dela Cruz — Defective",
  "notes": "Reason: Defective | Items: Rice 25kg, Chicken Feed | Invoice: KS-20260115-0042",
  "amount": 300.00,
  "payment_method": "Cash",
  "fund_source": "cashier",
  "reference_number": "RTN-20260115-0023",
  "date": "2026-01-15",
  "rma_number": "RTN-20260115-0023",  // Links to return
  "created_by": "user_id",
  "created_by_name": "Staff Name",
  "created_at": "2026-01-15T15:30:00Z",
  "voided": false
}
```

### **Expense in Z-Report**
- All non-voided expenses appear in daily close
- Grouped by category
- Deducted from cashier drawer
- Affects end-of-day fund reconciliation

---

## 📦 **INVENTORY SYSTEM** (`/api/inventory`)

### **Inventory Record**
```python
{
  "product_id": "...",
  "branch_id": "...",
  "quantity": 50,  // Available stock
  "updated_at": "2026-01-15T10:00:00Z"
}
```

### **Inventory Movements** (Audit Trail)
```python
{
  "id": "...",
  "product_id": "...",
  "branch_id": "...",
  "movement_type": "return_to_shelf",  // or "return_pullout", "sale", "purchase", etc.
  "quantity": 2,  // Positive = add, Negative = deduct
  "from_location": "",
  "to_location": "RTN-20260115-0023",
  "cost_price": 120.00,
  "user_id": "...",
  "user_name": "Staff Name",
  "notes": "Customer return — Defective — RTN-20260115-0023",
  "created_at": "2026-01-15T15:30:00Z"
}
```

### **Inventory Corrections** (Pull-out/Loss)
```python
{
  "id": "...",
  "product_id": "...",
  "product_name": "Rice 25kg",
  "branch_id": "...",
  "old_qty": 50,
  "new_qty": 50,  // No change (pulled out before reaching shelf)
  "qty_pulled_out": 2,
  "reason": "Customer return pull-out: damaged — Defective",
  "rma_number": "RTN-20260115-0023",
  "loss_value": 240.00,
  "corrected_by": "user_id",
  "corrected_by_name": "Staff Name",
  "created_at": "2026-01-15T15:30:00Z",
  "type": "customer_return_pullout"
}
```

---

## 🔐 **SECURITY & AUTHORIZATION**

### **PIN Verification Levels**
1. **Manager PIN** — For returns, fund transfers (cashier ↔ safe)
2. **Admin TOTP** — For bank deposits, critical actions
3. **Owner PIN** — For capital injection, high-value operations

### **Verification Flow** (`/api/verify/pin`)
```python
from routes.verify import verify_pin_for_action

verifier = await verify_pin_for_action(pin, "action_type")
if not verifier:
    await log_failed_pin_attempt(user, action_desc, action_type)
    raise HTTPException(403, "Invalid PIN")

authorized_by = verifier["verifier_name"]  # Use in audit logs
```

### **Action Types**
- `fund_transfer_cashier_safe` — Manager PIN
- `fund_transfer_safe_bank` — Admin TOTP
- `fund_transfer_capital_add` — Owner PIN
- `void_expense` — Manager PIN
- `void_invoice` — Admin PIN

---

## 📬 **SMS NOTIFICATION SYSTEM** (`/api/sms`)

### **SMS Hooks** (`sms_hooks.py`)
Triggered automatically on certain events:

1. **`on_payment_received`** — Customer makes payment
   - Sends balance update SMS
   - Triggered from: `qr_actions.py`, `invoices.py`, `accounting.py`

2. **`on_charge_applied`** — Interest/penalty added
   - Notifies customer of new charges
   - Triggered from: `accounting.py`

3. **`on_invoice_created`** — New credit sale
   - Sends invoice details
   - Triggered from: `sales.py`, `invoices.py`

### **Integration Point for Receipt Corrections**
```python
# After updating invoice and refunding money
from routes.sms_hooks import on_payment_received

# Calculate effective "payment" (negative adjustment)
adjustment_amount = original_total - corrected_total

# Get updated customer balance
customer = await db.customers.find_one({"id": customer_id}, {"_id": 0})

# Send SMS with updated balance
await on_payment_received(
    customer_id,
    adjustment_amount,
    "Cash",
    f"Receipt correction — {invoice_number}",
    customer["balance"],
    branch_id
)
```

---

## 🆕 **PROPOSED: INCOMPLETE STOCK CORRECTION FLOW**

### **New Endpoint: `POST /api/invoices/{id}/correct-incomplete-stock`**

#### **Input Data**
```json
{
  "items": [
    {
      "product_id": "prod_123",
      "original_qty": 5,
      "actual_qty": 3,
      "product_name": "Rice 25kg",
      "rate": 150.00
    }
  ],
  "manager_pin": "521325",
  "reprint_receipt": true,
  "notes": "Customer only received 3 bags"
}
```

#### **Processing Steps**

1. **Validate Date Not Closed**
   ```python
   invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
   if not invoice:
       raise HTTPException(404, "Invoice not found")
   
   closed = await db.daily_closings.find_one({
       "branch_id": invoice["branch_id"],
       "date": invoice["order_date"],
       "status": "closed"
   })
   
   if closed:
       raise HTTPException(400, 
           f"Cannot update — day {invoice['order_date']} already closed. "
           f"Use Return & Refund instead."
       )
   ```

2. **Verify Manager PIN**
   ```python
   from routes.verify import verify_pin_for_action
   verifier = await verify_pin_for_action(manager_pin, "correct_incomplete_stock")
   if not verifier:
       raise HTTPException(403, "Invalid manager PIN")
   ```

3. **Calculate Differences**
   ```python
   corrected_items = []
   items_to_return = []
   refund_amount = 0
   
   for correction in data["items"]:
       orig_qty = float(correction["original_qty"])
       actual_qty = float(correction["actual_qty"])
       diff_qty = orig_qty - actual_qty
       
       if diff_qty > 0:  # Some items not given
           rate = float(correction["rate"])
           refund_amount += diff_qty * rate
           
           items_to_return.append({
               "product_id": correction["product_id"],
               "product_name": correction["product_name"],
               "quantity": diff_qty
           })
       
       corrected_items.append({
           **correction,
           "quantity": actual_qty,  # Update to actual
           "total": actual_qty * rate
       })
   ```

4. **Return Stock to Shelves**
   ```python
   for item in items_to_return:
       # Increment inventory
       await db.inventory.update_one(
           {"product_id": item["product_id"], "branch_id": invoice["branch_id"]},
           {"$inc": {"quantity": item["quantity"]}, "$set": {"updated_at": now_iso()}},
           upsert=True
       )
       
       # Log movement
       await log_movement(
           item["product_id"], invoice["branch_id"],
           "incomplete_stock_return", item["quantity"],
           "", invoice["invoice_number"], 0,
           user["id"], user.get("full_name", ""),
           f"Receipt correction — items not given — {invoice['invoice_number']}"
       )
   ```

5. **Create Correction Audit Record**
   ```python
   correction_id = new_id()
   await db.invoice_corrections.insert_one({
       "id": correction_id,
       "invoice_id": invoice["id"],
       "invoice_number": invoice["invoice_number"],
       "correction_type": "incomplete_stock",
       "branch_id": invoice["branch_id"],
       "customer_id": invoice.get("customer_id"),
       "customer_name": invoice.get("customer_name"),
       "order_date": invoice["order_date"],
       "original_items": invoice["items"],
       "corrected_items": corrected_items,
       "items_returned_to_shelf": items_to_return,
       "original_subtotal": invoice["subtotal"],
       "original_grand_total": invoice["grand_total"],
       "corrected_subtotal": new_subtotal,
       "corrected_grand_total": new_grand_total,
       "refund_amount": refund_amount,
       "corrected_by_id": user["id"],
       "corrected_by_name": user.get("full_name", ""),
       "authorized_by": verifier["verifier_name"],
       "manager_pin_verified": True,
       "notes": data.get("notes", ""),
       "created_at": now_iso()
   })
   ```

6. **Update Invoice**
   ```python
   await db.invoices.update_one(
       {"id": invoice_id},
       {"$set": {
           "items": corrected_items,
           "subtotal": new_subtotal,
           "grand_total": new_grand_total,
           "balance": new_balance,
           "correction_applied": True,
           "correction_id": correction_id,
           "updated_at": now_iso(),
           "updated_by": user["id"]
       }}
   )
   ```

7. **Refund Money from Cashier Wallet**
   ```python
   if refund_amount > 0:
       ref_text = (
           f"Refund incomplete stock — {invoice['invoice_number']} — "
           f"{invoice.get('customer_name', 'Walk-in')} — "
           f"{len(items_to_return)} items not given"
       )
       
       await update_cashier_wallet(
           invoice["branch_id"],
           -refund_amount,
           ref_text,
           allow_negative=False
       )
       
       # Record as expense for Z-report
       await db.expenses.insert_one({
           "id": new_id(),
           "branch_id": invoice["branch_id"],
           "category": "Customer Return Refund",
           "description": f"Incomplete stock refund — {invoice['invoice_number']}",
           "notes": data.get("notes", ""),
           "amount": refund_amount,
           "payment_method": "Cash",
           "fund_source": "cashier",
           "reference_number": invoice["invoice_number"],
           "date": now_iso()[:10],
           "invoice_id": invoice_id,
           "correction_id": correction_id,
           "created_by": user["id"],
           "created_by_name": user.get("full_name", ""),
           "created_at": now_iso()
       })
   ```

8. **Update Customer Balance** (if credit customer)
   ```python
   if invoice.get("customer_id") and refund_amount > 0:
       await db.customers.update_one(
           {"id": invoice["customer_id"]},
           {"$inc": {"balance": -refund_amount}}
       )
   ```

9. **Send SMS Notification** (if credit customer)
   ```python
   if invoice.get("customer_id") and refund_amount > 0:
       from routes.sms_hooks import on_payment_received
       
       customer = await db.customers.find_one(
           {"id": invoice["customer_id"]}, {"_id": 0}
       )
       
       await on_payment_received(
           invoice["customer_id"],
           refund_amount,
           "Cash",
           f"Receipt correction — {invoice['invoice_number']}",
           customer["balance"],
           invoice["branch_id"]
       )
   ```

10. **Return Updated Invoice** (for reprint)
    ```python
    updated_invoice = await db.invoices.find_one({"id": invoice_id}, {"_id": 0})
    
    return {
        "message": "Receipt corrected successfully",
        "invoice": updated_invoice,
        "correction_id": correction_id,
        "refund_amount": refund_amount,
        "items_returned": len(items_to_return),
        "reprint": data.get("reprint_receipt", False)
    }
    ```

---

## ✅ **DATA INTEGRITY CHECKLIST**

### **Before Implementation**
- [ ] Understand daily close date validation
- [ ] Map all fund wallet deduction paths
- [ ] Review inventory movement logging
- [ ] Check customer balance sync logic
- [ ] Verify SMS hook triggers

### **During Implementation**
- [ ] Validate date not closed (for Update Receipt only)
- [ ] Verify PIN before critical action
- [ ] Create correction audit record BEFORE updating invoice
- [ ] Return stock to shelves atomically
- [ ] Deduct from correct fund source (cashier)
- [ ] Update customer balance (if credit)
- [ ] Create expense record (for Z-report)
- [ ] Log inventory movements
- [ ] Trigger SMS notification
- [ ] Preserve original invoice in `invoice_corrections`

### **After Implementation**
- [ ] Test Return & Refund flow (existing)
- [ ] Test Update Receipt flow (new)
- [ ] Verify date closed error handling
- [ ] Check fund balance calculations
- [ ] Validate inventory count accuracy
- [ ] Confirm SMS delivery
- [ ] Review audit trail completeness
- [ ] Test daily close with corrected invoices
- [ ] Verify Z-report includes correction expenses

---

## 🚨 **CRITICAL INTEGRATION POINTS**

### **1. Daily Close System**
- **Check:** Last close date before allowing Update Receipt
- **Action:** Return & Refund always allowed (new record)
- **Action:** Update Receipt blocked if date closed

### **2. Fund Management**
- **Source:** ALWAYS cashier wallet (customer just paid)
- **Validation:** Check cashier balance before refund
- **Logging:** `wallet_movements` for audit

### **3. Inventory System**
- **Return to Shelf:** Increment `inventory.quantity`
- **Logging:** `inventory_movements` with type `incomplete_stock_return`
- **Audit:** Link movement to invoice number

### **4. Accounting/Expenses**
- **Record:** Create expense for refund
- **Category:** "Customer Return Refund"
- **Impact:** Appears in Z-Report, affects daily close

### **5. Customer Ledger**
- **Balance:** Deduct refund amount
- **SMS:** Send updated balance notification

### **6. Invoice History**
- **Preserve:** Store original invoice in `invoice_corrections`
- **Flag:** Set `correction_applied: true`
- **Link:** `correction_id` references audit record

### **7. Audit Trail**
- **Collections:** `invoice_corrections`, `inventory_movements`, `wallet_movements`, `expenses`
- **Fields:** Include who, when, why, and PIN verifier name

---

## 📋 **COLLECTIONS SCHEMA (New)**

### **`invoice_corrections`**
```python
{
  "id": "corr_xxx",
  "invoice_id": "inv_xxx",
  "invoice_number": "KS-20260115-0042",
  "correction_type": "incomplete_stock",
  "branch_id": "branch_xxx",
  "customer_id": "cust_xxx",
  "customer_name": "Juan Dela Cruz",
  "order_date": "2026-01-15",
  
  # Original state
  "original_items": [...],
  "original_subtotal": 1850.00,
  "original_grand_total": 1850.00,
  
  # Corrected state
  "corrected_items": [...],
  "corrected_subtotal": 1110.00,
  "corrected_grand_total": 1110.00,
  
  # What changed
  "items_returned_to_shelf": [
    {"product_id": "...", "product_name": "Rice 25kg", "quantity": 2},
    {"product_id": "...", "product_name": "Vitamins", "quantity": 2}
  ],
  "refund_amount": 740.00,
  
  # Authorization
  "corrected_by_id": "user_xxx",
  "corrected_by_name": "Staff Name",
  "authorized_by": "Manager Name",
  "manager_pin_verified": true,
  
  # Metadata
  "notes": "Customer only received 3 bags, not 5",
  "created_at": "2026-01-15T16:00:00Z"
}
```

---

## 🎯 **NEXT STEPS**

1. ✅ **Architecture Research Complete**
2. 🔜 **Implement Backend Endpoint** — `POST /api/invoices/{id}/correct-incomplete-stock`
3. 🔜 **Add to DocViewer Terminal Actions** — UI buttons
4. 🔜 **Build Correction Modal** — Item adjustment interface
5. 🔜 **Test Date Validation** — Closed day error handling
6. 🔜 **Test Fund Deduction** — Cashier wallet accuracy
7. 🔜 **Test Inventory Return** — Stock count verification
8. 🔜 **Test SMS Notifications** — Balance update delivery
9. 🔜 **Test Daily Close Integration** — Z-report accuracy
10. 🔜 **User Acceptance Testing** — Physical terminal device

---

**Research completed:** January 2026  
**Status:** ✅ Ready for Implementation  
**Confidence:** HIGH — All integration points identified and documented

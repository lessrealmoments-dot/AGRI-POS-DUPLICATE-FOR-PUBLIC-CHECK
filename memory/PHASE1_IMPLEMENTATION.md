# Phase 1: Return & Refund Terminal UI Implementation

## 🎯 **OBJECTIVE**
Add "Return & Refund" button to DocViewerPage Terminal Actions for invoice/sales receipts.

## 📋 **SCOPE**
- **Entry Point:** DocViewerPage → Terminal Actions (invoice doc_type only)
- **Backend:** ✅ Already exists (`POST /api/returns`)
- **Frontend:** New Terminal UI integration

---

## 🏗️ **IMPLEMENTATION STEPS**

### **Step 1: Add Return & Refund Button to Terminal Actions**

**Location:** `/app/frontend/src/pages/DocViewerPage.jsx` (lines 1770-1795)

**Current Logic:**
- Shows actions for `purchase_order` (Pull PO)
- Shows actions for `branch_transfer` (Pull Transfer)
- Shows "No terminal actions available" for other doc types

**New Logic:**
- Add actions for `invoice` doc_type
- Show "Return & Refund" button
- Show "Update for Incomplete Stock" button (Phase 2)

**Condition:**
```jsx
{basic.doc_type === 'invoice' && (
  <>
    {/* Return & Refund */}
    {/* Update for Incomplete Stock (Phase 2) */}
  </>
)}
```

---

### **Step 2: Create Return & Refund Modal Component**

**New Component:** `TerminalReturnRefundModal`

**Props:**
```jsx
{
  invoice: fullData,  // Complete invoice object
  terminalSession: terminalSession,  // Terminal session data
  onSuccess: () => {},  // Callback after successful return
  onClose: () => {}  // Close modal
}
```

**UI Flow:**
1. **Item Selection** — Checkboxes for each invoice item
2. **Quantity Entry** — Input for return quantity (max = original qty)
3. **Condition Selection** — Sellable / Damaged / Expired / Defective
4. **Inventory Action** — Return to Shelf / Pull Out
5. **Refund Amount** — Auto-calculated or manual entry
6. **PIN Confirmation** — Manager/Admin PIN required
7. **Submit** — Call `/api/returns` endpoint
8. **Success** — Show success message + option to print Return Slip

---

### **Step 3: State Management**

**Add to DocViewerPage state:**
```jsx
const [showReturnModal, setShowReturnModal] = useState(false);
```

**Button Click:**
```jsx
onClick={() => setShowReturnModal(true)}
```

---

### **Step 4: API Integration**

**Endpoint:** `POST /api/returns`

**Payload Structure:**
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
  "fund_source": "cashier",
  "cashier_id": "...",
  "cashier_name": "..."
}
```

---

### **Step 5: Return Slip Printing**

**After successful return:**
- Generate Return Slip using `PrintEngine`
- Option to print 1 or 2 copies
- Use existing `PrintBridge.printReceipt()` method

**Return Slip Format:**
```
=====================================
          RETURN SLIP
=====================================
RMA: RTN-20260115-0023
Date: Jan 15, 2026

Customer: Juan Dela Cruz
Original Invoice: KS-20260115-0042

RETURNED ITEMS:
-------------------------------------
Rice 25kg (Damaged)
  Qty: 2 bags × ₱150.00 = ₱300.00
  Action: Pulled Out

-------------------------------------
Refund Amount: ₱300.00
Paid via: Cashier

Reason: Defective product
Notes: Customer reported quality issues

Processed by: Staff Name
=====================================
```

---

## 📝 **FILES TO MODIFY**

### **1. `/app/frontend/src/pages/DocViewerPage.jsx`**
- Add state: `showReturnModal`
- Add button in Terminal Actions (invoice condition)
- Import new modal component

### **2. `/app/frontend/src/components/TerminalReturnRefundModal.jsx`** (NEW)
- Complete modal component
- Item selection UI
- Condition/action selectors
- API integration
- Success handling

---

## 🎨 **UI DESIGN**

### **Terminal Actions Section (Modified)**
```jsx
<div className="p-5 space-y-3">
  {basic.doc_type === 'invoice' && (
    <>
      <div className="border-t border-slate-100 pt-3 mt-2">
        <p className="text-xs font-semibold text-amber-700 uppercase tracking-wide mb-2">
          ──── CORRECTIONS ────
        </p>
      </div>
      
      <Button 
        className="w-full h-11 bg-red-600 hover:bg-red-700 text-white font-semibold flex items-center justify-center gap-2"
        onClick={() => setShowReturnModal(true)}
        data-testid="terminal-return-refund-btn"
      >
        <RotateCcw size={14} />
        Return & Refund
      </Button>
      
      {/* Phase 2: Update for Incomplete Stock */}
    </>
  )}
  
  {/* Existing PO and Transfer actions */}
</div>
```

---

## ✅ **TESTING CHECKLIST**

### **Functional Testing**
- [ ] Button appears for invoice doc_type
- [ ] Button hidden for non-invoice doc_types
- [ ] Modal opens on button click
- [ ] Can select items to return
- [ ] Can enter return quantities
- [ ] Can select condition (Sellable/Damaged/Expired/Defective)
- [ ] Can choose inventory action (Shelf/Pullout)
- [ ] Refund amount auto-calculates correctly
- [ ] PIN validation works
- [ ] API call succeeds
- [ ] Success message displays
- [ ] Invoice data refreshes after return
- [ ] Return slip print works

### **Edge Cases**
- [ ] Cannot return more than original quantity
- [ ] Cannot proceed without selecting items
- [ ] Cannot proceed without PIN
- [ ] Handles API errors gracefully
- [ ] Works offline (queues for sync)

---

## 📊 **SUCCESS CRITERIA**

1. ✅ Button visible in Terminal Actions for invoices
2. ✅ Modal opens with all invoice items
3. ✅ Can select and configure return items
4. ✅ PIN validation works
5. ✅ Successfully calls `/api/returns` endpoint
6. ✅ Returns stock to shelves (if sellable)
7. ✅ Refunds money from cashier wallet
8. ✅ Creates RMA record
9. ✅ Creates expense record
10. ✅ Logs inventory movements
11. ✅ Shows success confirmation
12. ✅ Optional Return Slip printing

---

## ⏱️ **ESTIMATED TIME: 2 hours**

- File modifications: 30 min
- Modal component: 60 min
- API integration: 20 min
- Testing: 10 min

---

**Status:** 🚧 In Progress  
**Next:** Implement DocViewerPage modifications

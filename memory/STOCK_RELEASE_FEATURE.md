# Stock Release Mode Feature - Terminal Implementation

## Overview
Added **Stock Release Mode** selection to Terminal Sales interface, bringing feature parity with the web sales interface (`/sales-new`).

## Problem Statement
The Terminal POS was missing the ability to choose between Full Release and Partial Release for stock management, which was available on the web interface. This prevented terminal users from:
- Creating orders with staged/batch pickup
- Reserving inventory for later release
- Handling split deliveries via QR code scanning

## Solution Implemented

### 1. **Terminal Sales UI Changes** (`/app/frontend/src/pages/terminal/TerminalSales.jsx`)

#### Added State Management
- New state variable: `releaseMode` ('full' | 'partial' | '')
- Reset on checkout cancel or completion

#### Added UI Flow
**Placement:** After payment type selection, before final confirmation

**Two-button selector:**
- **Full Release** (Green) - "All items released now"
- **Partial Release** (Amber) - "Items staged for pickup"

**Validation:**
- User MUST select a release mode before proceeding
- Shows error toast if not selected: "Select stock release mode"
- Once selected, shows current mode with "Change" button

**Information Panel:**
Explains both modes clearly:
- Full Release: Stock deducted immediately, customer receives all items now
- Partial Release: Stock reserved, customer scans QR to release in batches

#### Backend Integration
- Added `release_mode` field to sale payload sent to API
- Flows through existing `/unified-sale` endpoint (backend already supports this)

### 2. **Print Receipt Updates** (`/app/frontend/src/lib/PrintEngine.js`)

Updated both thermal (58mm) and full-page receipt formats to show release status prominently.

#### Thermal Receipts (58mm)
**Full Release:**
```
Status: FULLY RELEASED
```

**Partial Release:**
```
╔═══════════════════════════════════════════╗
║ PARTIAL RELEASE - SCAN QR CODE TO        ║
║ RELEASE ITEMS                             ║
╚═══════════════════════════════════════════╝
```
- Highlighted amber background
- Bold, uppercase text for visibility

#### Full-Page Receipts (8.5" × 11")
**Full Release:**
- Shows "Status: FULLY RELEASED" in header metadata

**Partial Release:**
- Large amber alert banner with warning icon (⚠)
- Primary message: "PARTIAL RELEASE — Items must be scanned via QR code for release"
- Secondary instruction: "Scan the QR code below to manage item releases"
- Professional styling with borders and emphasis

### 3. **Functions Modified**

#### TerminalSales.jsx
1. `resetCheckout()` - Now resets `releaseMode` state
2. `processSale()` - Added validation for release mode selection
3. Checkout dialog JSX - Added release mode selector UI
4. `saleData` object - Includes `release_mode` field

#### PrintEngine.js
1. `orderSlipThermal()` - Shows release status in receipt header
2. `trustReceiptThermal()` - Shows release status for credit sales
3. `orderSlipFullPage()` - Shows release status with banner for partial
4. `trustReceiptFullPage()` - Shows release status with banner for partial

## User Experience Flow

### Terminal Checkout Process (Updated)
1. **Add items to cart** → Tap "Checkout"
2. **Select customer** (optional for walk-in)
3. **Choose payment type** (Cash / Digital / Credit / Split)
4. **Enter payment details** (amount tendered, reference numbers, etc.)
5. **🆕 SELECT STOCK RELEASE MODE** ← New step
   - Choose Full or Partial
   - See explanation of each option
6. **Confirm & process sale**
7. **Receipt prints with release status clearly shown**

### Visual Flow
```
Cart → Checkout → Customer → Payment Type → Payment Details 
  → 🆕 RELEASE MODE → Confirm → Print Receipt (with status)
```

## Technical Details

### Data Flow
```
Terminal UI (releaseMode state)
    ↓
processSale() validation
    ↓
saleData object { release_mode: 'full' | 'partial' }
    ↓
POST /api/unified-sale
    ↓
Invoice record created with release_mode
    ↓
PrintEngine receives invoice data
    ↓
Receipt generated with status indicator
```

### File Changes Summary
- **Modified:** `/app/frontend/src/pages/terminal/TerminalSales.jsx` (+64 lines)
- **Modified:** `/app/frontend/src/lib/PrintEngine.js` (+16 lines)
- **No breaking changes** - Backward compatible

### Backward Compatibility
- Existing invoices without `release_mode` field continue to work
- Print engine safely handles missing/undefined `release_mode`
- No database migration required (backend already supports field)

## Testing Checklist

### Functional Testing
- [ ] Terminal loads without errors
- [ ] Can complete sale with Full Release
- [ ] Can complete sale with Partial Release
- [ ] Cannot proceed without selecting release mode (shows error)
- [ ] "Change" button allows switching release modes
- [ ] Full release receipt shows "FULLY RELEASED"
- [ ] Partial release receipt shows amber warning banner
- [ ] Both thermal and full-page prints work correctly
- [ ] Offline sales include release_mode in sync payload

### Edge Cases
- [ ] Quick consecutive sales maintain separate release mode selections
- [ ] Canceling checkout resets release mode
- [ ] Back button from release mode step works correctly
- [ ] Works with all payment types (cash, digital, credit, split)

## Benefits

### For Business
✅ **Feature Parity** - Terminal now matches web interface capabilities  
✅ **Inventory Control** - Better management of stock reservations  
✅ **Customer Flexibility** - Support batch pickups and staged deliveries  
✅ **Clear Communication** - Receipt clearly shows release status  
✅ **Operational Efficiency** - Reduces confusion about stock availability  

### For Users
✅ **No Training Required** - Clear UI with explanations  
✅ **Error Prevention** - Forced selection prevents accidental full releases  
✅ **Professional Receipts** - Clear status indicators  
✅ **Consistent Experience** - Works same as web interface  

## Known Limitations
- Terminal users must be online to create partial release sales (QR release requires backend)
- Receipt printing relies on hardware thermal printer compatibility
- Currency symbol intentionally uses "P" instead of "₱" due to H10 hardware font limitations (existing limitation)

## Future Enhancements (Not in Scope)
- Quick toggle between Full/Partial for repeat transactions
- Default release mode per customer setting
- Visual indicator of how many items remain to be released (shown in QR scan interface)

## Deployment Notes
- No environment variables required
- No backend changes needed (endpoint already supports release_mode)
- Frontend hot-reload will pick up changes automatically
- No database migrations required

---

**Implementation Date:** January 2026  
**Status:** ✅ Complete and Ready for Testing  
**Breaking Changes:** None

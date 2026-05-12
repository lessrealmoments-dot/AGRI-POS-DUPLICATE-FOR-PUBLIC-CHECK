"""
AgriPOS Configuration Module
Multi-tenant aware database proxy with per-request org isolation.
"""
from contextvars import ContextVar
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from pathlib import Path
import os
import logging

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

MONGO_URL = os.environ['MONGO_URL']
DB_NAME = os.environ['DB_NAME']
JWT_SECRET = os.environ['JWT_SECRET']

client = AsyncIOMotorClient(MONGO_URL)
_raw_db = client[DB_NAME]

# ContextVar: holds current org_id per async request (None = super admin / unscoped)
_current_org_id: ContextVar = ContextVar('_current_org_id', default=None)

# Collections that must be isolated per organization
TENANT_COLLECTIONS = {
    # Core business data
    'users', 'branches', 'products', 'inventory', 'customers',
    'invoices', 'sales', 'purchase_orders', 'suppliers', 'employees',
    'movements', 'fund_wallets', 'wallet_movements', 'fund_transfers',
    'expenses', 'branch_prices', 'branch_transfer_orders',
    'count_sheets', 'daily_closings', 'sales_log', 'returns',
    'discrepancy_log', 'notifications', 'view_tokens', 'safe_lots',
    'price_schemes', 'settings', 'system_settings', 'accounts_payable',
    'capital_changes', 'security_events', 'pin_attempt_log',
    # Previously missing — added after isolation audit
    'payables',                      # Accounts payable (supplier credit terms)
    'receivables',                   # AR from credit customers
    'product_vendors',               # Vendor pricing history per product
    'invoice_edits',                 # Audit trail of invoice edits
    'inventory_corrections',         # Admin inventory correction logs
    'inventory_adjustments',         # Count sheet adjustments
    'inventory_logs',                # General inventory movement logs
    'employee_advance_logs',         # Cash advance history per employee
    'safe_lot_usages',               # Safe lot drawdown records
    'branch_transfer_price_memory',  # Last transfer capital/retail per product/branch
    'branch_transfer_templates',     # Saved markup templates per destination branch
    'audits',                        # Audit session records
    'upload_sessions',               # Receipt/document upload sessions
    'business_documents',            # AgriDocs — business document cloud
    'doc_upload_tokens',             # QR upload tokens for phone uploads
    # Phase 5+ isolation hardening (B-1): internal_invoices were inserted via
    # `db.internal_invoices` but the collection was never registered with the
    # tenant proxy → rows landed with organization_id=None and reads scoped
    # only by branch_id. Registering it here turns on auto-injection on
    # insert and auto-scoping on read for every code path that goes through
    # the `db` proxy (routes/internal_invoices.py, routes/search.py).
    'internal_invoices',
    # SMS — per-company isolation
    'sms_queue',                     # Outbound SMS queue per company
    'sms_templates',                 # Customizable message templates per company
    'sms_settings',                  # Per-trigger enable/disable settings per company
    'sms_inbox',                     # Incoming SMS replies from customers per company
    'product_categories',            # Org-defined product category list
    'crop_credits',                  # Charged-to-crop credit accounts
    'signature_sessions',            # Digital signature capture sessions
    'parked_sales',                  # Cashier "park sale" / draft cart staging
    'parked_branch_transfers',       # Manager "park transfer" / draft transfer staging
}


class TenantCollection:
    """Transparent wrapper that injects organization_id into all DB operations.

    Fails CLOSED when no org context is set: returns no data and refuses to
    insert orphan docs. This protects tenant data from leaking to a
    super-admin or unauthenticated caller who would otherwise see/modify all
    organizations' records via the scoped `db` proxy.

    Routes that legitimately need cross-tenant access (super-admin tools,
    boot-time sweeps, scheduled jobs) must use `_raw_db` directly with
    explicit organization_id filters, OR call `set_org_context(target_org_id)`
    first to scope the access intentionally.
    """

    # Sentinel value injected when no org context is active. No tenant doc has
    # this string in its organization_id field, so queries return empty.
    _NO_CONTEXT_SENTINEL = "__no_org_context__"

    def __init__(self, collection):
        self._col = collection

    def _org_filter(self, filter_dict=None):
        org_id = _current_org_id.get()
        if not org_id:
            # Fail-closed — match nothing rather than leak across tenants.
            return {**(filter_dict or {}), 'organization_id': self._NO_CONTEXT_SENTINEL}
        f = dict(filter_dict or {})
        f['organization_id'] = org_id
        return f

    def _inject_org(self, document):
        org_id = _current_org_id.get()
        if not org_id:
            # No context AND caller didn't set organization_id explicitly →
            # refuse rather than create an orphan tenant doc that would later
            # be invisible to its own org. Caller must use _raw_db or set
            # the context first.
            if 'organization_id' not in document:
                raise RuntimeError(
                    "TenantCollection: refusing to insert without an organization_id "
                    "(no org context set). Use _raw_db or set_org_context() first."
                )
        elif 'organization_id' not in document:
            document['organization_id'] = org_id
        return document

    async def find_one(self, filter_dict=None, *args, **kwargs):
        return await self._col.find_one(self._org_filter(filter_dict), *args, **kwargs)

    def find(self, filter_dict=None, *args, **kwargs):
        return self._col.find(self._org_filter(filter_dict), *args, **kwargs)

    async def count_documents(self, filter_dict=None, **kwargs):
        return await self._col.count_documents(self._org_filter(filter_dict or {}), **kwargs)

    async def insert_one(self, document, *args, **kwargs):
        self._inject_org(document)
        return await self._col.insert_one(document, *args, **kwargs)

    async def insert_many(self, documents, *args, **kwargs):
        for doc in documents:
            self._inject_org(doc)
        return await self._col.insert_many(documents, *args, **kwargs)

    async def update_one(self, filter_dict, update, *args, **kwargs):
        return await self._col.update_one(self._org_filter(filter_dict), update, *args, **kwargs)

    async def update_many(self, filter_dict, update, *args, **kwargs):
        return await self._col.update_many(self._org_filter(filter_dict), update, *args, **kwargs)

    async def delete_one(self, filter_dict, *args, **kwargs):
        return await self._col.delete_one(self._org_filter(filter_dict), *args, **kwargs)

    async def delete_many(self, filter_dict, *args, **kwargs):
        return await self._col.delete_many(self._org_filter(filter_dict), *args, **kwargs)

    async def find_one_and_update(self, filter_dict, update, *args, **kwargs):
        return await self._col.find_one_and_update(self._org_filter(filter_dict), update, *args, **kwargs)

    def aggregate(self, pipeline, *args, **kwargs):
        org_id = _current_org_id.get()
        if not org_id:
            # Fail-closed — match nothing rather than aggregate across tenants.
            pipeline = [{"$match": {"organization_id": self._NO_CONTEXT_SENTINEL}}] + list(pipeline)
        else:
            pipeline = [{"$match": {"organization_id": org_id}}] + list(pipeline)
        return self._col.aggregate(pipeline, *args, **kwargs)

    async def create_index(self, *args, **kwargs):
        return await self._col.create_index(*args, **kwargs)

    async def drop(self):
        return await self._col.drop()

    def __getattr__(self, name):
        return getattr(self._col, name)


class TenantDB:
    """Database proxy that wraps tenant collections with org isolation."""

    def __init__(self, raw_db):
        self._db = raw_db

    def __getattr__(self, name):
        col = getattr(self._db, name)
        if name in TENANT_COLLECTIONS:
            return TenantCollection(col)
        return col

    def __getitem__(self, name):
        col = self._db[name]
        if name in TENANT_COLLECTIONS:
            return TenantCollection(col)
        return col

    async def list_collection_names(self):
        return await self._db.list_collection_names()


db = TenantDB(_raw_db)


def set_org_context(org_id):
    """Set the current organization ID for this async context."""
    _current_org_id.set(org_id)


def get_org_context():
    """Get the current organization ID."""
    return _current_org_id.get()


logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

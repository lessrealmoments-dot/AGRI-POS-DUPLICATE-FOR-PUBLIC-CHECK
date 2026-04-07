"""
Routes package init - export all routers.
"""
from .auth import router as auth_router
from .branches import router as branches_router
from .users import router as users_router
from .products import router as products_router
from .customers import router as customers_router
from .inventory import router as inventory_router
from .price_schemes import router as price_schemes_router
from .invoices import router as invoices_router
from .sales import router as sales_router
from .purchase_orders import router as purchase_orders_router
from .dashboard import router as dashboard_router
from .accounting import router as accounting_router
from .daily_operations import router as daily_ops_router
from .suppliers import router as suppliers_router
from .sync import router as sync_router
from .employees import router as employees_router
from .settings import router as settings_router
from .count_sheets import router as count_sheets_router
from .setup import router as setup_router
from .branch_prices import router as branch_prices_router
from .import_data import router as import_router
from .branch_transfers import router as branch_transfers_router
from .notifications import router as notifications_router
from .reports import router as reports_router
from .returns import router as returns_router
from .audit import router as audit_router
from .uploads import router as uploads_router
from .verify import router as verify_router
from .organizations import router as organizations_router
from .superadmin import router as superadmin_router
from .admin_auth import router as admin_auth_router
from .terminal import router as terminal_router
from .roles import router as roles_router

__all__ = [
    'auth_router', 'branches_router', 'users_router', 'products_router',
    'customers_router', 'inventory_router', 'price_schemes_router', 'invoices_router',
    'sales_router', 'purchase_orders_router', 'dashboard_router', 'accounting_router',
    'daily_ops_router', 'suppliers_router', 'sync_router', 'employees_router',
    'settings_router', 'count_sheets_router', 'setup_router', 'branch_prices_router',
    'import_router', 'notifications_router', 'branch_transfers_router', 'reports_router',
    'returns_router', 'audit_router', 'uploads_router', 'verify_router',
    'organizations_router', 'superadmin_router', 'admin_auth_router',
    'terminal_router', 'roles_router',
]

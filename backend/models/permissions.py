"""
Permission system definitions: modules, actions, and role presets.
Inflow Cloud-style granular permissions.
"""

# Permission structure: { module: { action: true/false } }
# Modules: dashboard, branches, products, inventory, sales, purchase_orders,
#          suppliers, customers, accounting, reports, settings
# Actions vary by module but typically include: view, create, edit, delete
# Special permissions: view_cost, sell_below_cost, adjust_inventory, void_invoices, etc.

PERMISSION_MODULES = {
    "dashboard": {
        "label": "Dashboard",
        "actions": {
            "view": "View Dashboard",
        }
    },
    "branches": {
        "label": "Branches",
        "actions": {
            "view": "View Branches",
            "create": "Create Branch",
            "edit": "Edit Branch",
            "delete": "Delete Branch",
        }
    },
    "products": {
        "label": "Products",
        "actions": {
            "view": "View Products",
            "create": "Create Product",
            "edit": "Edit Product",
            "delete": "Delete Product",
            "view_cost": "View Cost Price",
            "edit_cost": "Edit Capital / Cost Price",
        }
    },
    "inventory": {
        "label": "Inventory",
        "actions": {
            "view": "View Inventory",
            "adjust": "Adjust Stock",
            "transfer": "Transfer Stock",
        }
    },
    "sales": {
        "label": "Sales / POS",
        "actions": {
            "view": "View Sales",
            "create": "Create Sale",
            "edit": "Edit Invoice",
            "void": "Void Invoice",
            "sell_below_cost": "Sell Below Cost",
            "give_discount": "Apply Discount",
        }
    },
    "purchase_orders": {
        "label": "Purchase Orders",
        "actions": {
            "view": "View POs",
            "create": "Create PO",
            "edit": "Edit PO",
            "receive": "Receive Stock",
            "delete": "Delete PO",
        }
    },
    "suppliers": {
        "label": "Suppliers",
        "actions": {
            "view": "View Suppliers",
            "create": "Create Supplier",
            "edit": "Edit Supplier",
            "delete": "Delete Supplier",
        }
    },
    "customers": {
        "label": "Customers",
        "actions": {
            "view": "View Customers",
            "create": "Create Customer",
            "edit": "Edit Customer",
            "delete": "Delete Customer",
            "view_balance": "View Balance",
            "manage_credit": "Manage Credit Limit",
        }
    },
    "accounting": {
        "label": "Accounting",
        "actions": {
            "view": "View Accounting",
            "receive_payment": "Receive Payments",
            "create_expense": "Create Expense",
            "edit_expense": "Edit Expense",
            "generate_interest": "Generate Interest",
            "generate_penalty": "Generate Penalty",
            "manage_funds": "Manage Funds",
        }
    },
    "price_schemes": {
        "label": "Price Schemes",
        "actions": {
            "view": "View Schemes",
            "create": "Create Scheme",
            "edit": "Edit Scheme",
            "delete": "Delete Scheme",
        }
    },
    "reports": {
        "label": "Reports",
        "actions": {
            "view": "View Reports",
            "view_profit": "View Profit Reports",
            "export": "Export Reports",
            "close_day": "Close Day",
        }
    },
    "settings": {
        "label": "Settings",
        "actions": {
            "view": "View Settings",
            "edit": "Edit Settings",
            "manage_users": "Manage Users",
            "manage_permissions": "Manage Permissions",
        }
    },
    "count_sheets": {
        "label": "Count Sheets",
        "actions": {
            "view": "View Count Sheets",
            "create": "Create Count Sheet",
            "count": "Enter Actual Counts",
            "complete": "Complete Count Sheet",
            "cancel": "Cancel Count Sheet",
            "adjust": "Apply Inventory Adjustments",
        }
    },
}

# Preset role templates
ROLE_PRESETS = {
    "admin": {
        "label": "Administrator",
        "description": "Full access to all features",
        "permissions": {
            "dashboard": {"view": True},
            "branches": {"view": True, "create": True, "edit": True, "delete": True},
            "products": {"view": True, "create": True, "edit": True, "delete": True, "view_cost": True, "edit_cost": True},
            "inventory": {"view": True, "adjust": True, "transfer": True},
            "sales": {"view": True, "create": True, "edit": True, "void": True, "sell_below_cost": True, "give_discount": True},
            "purchase_orders": {"view": True, "create": True, "edit": True, "receive": True, "delete": True},
            "suppliers": {"view": True, "create": True, "edit": True, "delete": True},
            "customers": {"view": True, "create": True, "edit": True, "delete": True, "view_balance": True, "manage_credit": True},
            "accounting": {"view": True, "receive_payment": True, "create_expense": True, "edit_expense": True, "generate_interest": True, "generate_penalty": True, "manage_funds": True},
            "price_schemes": {"view": True, "create": True, "edit": True, "delete": True},
            "reports": {"view": True, "view_profit": True, "export": True, "close_day": True},
            "settings": {"view": True, "edit": True, "manage_users": True, "manage_permissions": True},
            "count_sheets": {"view": True, "create": True, "count": True, "complete": True, "cancel": True, "adjust": True},
        }
    },
    "manager": {
        "label": "Branch Manager",
        "description": "Manage branch operations, limited admin access",
        "permissions": {
            "dashboard": {"view": True},
            "branches": {"view": True, "create": False, "edit": True, "delete": False},
            "products": {"view": True, "create": True, "edit": True, "delete": False, "view_cost": True, "edit_cost": True},
            "purchase_orders": {"view": True, "create": True, "edit": True, "receive": True, "delete": False},
            "suppliers": {"view": True, "create": True, "edit": True, "delete": False},
            "customers": {"view": True, "create": True, "edit": True, "delete": False, "view_balance": True, "manage_credit": True},
            "accounting": {"view": True, "receive_payment": True, "create_expense": True, "edit_expense": True, "generate_interest": True, "generate_penalty": True, "manage_funds": True},
            "price_schemes": {"view": True, "create": False, "edit": False, "delete": False},
            "reports": {"view": True, "view_profit": True, "export": True, "close_day": True},
            "settings": {"view": True, "edit": False, "manage_users": False, "manage_permissions": False},
            "count_sheets": {"view": True, "create": True, "count": True, "complete": True, "cancel": True, "adjust": False},
        }
    },
    "cashier": {
        "label": "Cashier",
        "description": "POS operations and basic customer service",
        "permissions": {
            "dashboard": {"view": True},
            "branches": {"view": True, "create": False, "edit": False, "delete": False},
            "products": {"view": True, "create": False, "edit": False, "delete": False, "view_cost": False, "edit_cost": False},
            "inventory": {"view": True, "adjust": False, "transfer": False},
            "sales": {"view": True, "create": True, "edit": False, "void": False, "sell_below_cost": False, "give_discount": False},
            "purchase_orders": {"view": False, "create": False, "edit": False, "receive": False, "delete": False},
            "suppliers": {"view": False, "create": False, "edit": False, "delete": False},
            "customers": {"view": True, "create": True, "edit": False, "delete": False, "view_balance": False, "manage_credit": False},
            "accounting": {"view": False, "receive_payment": False, "create_expense": False, "edit_expense": False, "generate_interest": False, "generate_penalty": False, "manage_funds": False},
            "price_schemes": {"view": True, "create": False, "edit": False, "delete": False},
            "reports": {"view": False, "view_profit": False, "export": False, "close_day": False},
            "settings": {"view": False, "edit": False, "manage_users": False, "manage_permissions": False},
            "count_sheets": {"view": False, "create": False, "count": False, "complete": False, "cancel": False, "adjust": False},
        }
    },
    "inventory_clerk": {
        "label": "Inventory Clerk",
        "description": "Manage inventory and stock operations",
        "permissions": {
            "dashboard": {"view": True},
            "branches": {"view": True, "create": False, "edit": False, "delete": False},
            "products": {"view": True, "create": True, "edit": True, "delete": False, "view_cost": True, "edit_cost": True},
            "inventory": {"view": True, "adjust": True, "transfer": True},
            "sales": {"view": True, "create": False, "edit": False, "void": False, "sell_below_cost": False, "give_discount": False},
            "purchase_orders": {"view": True, "create": True, "edit": True, "receive": True, "delete": False},
            "suppliers": {"view": True, "create": True, "edit": True, "delete": False},
            "customers": {"view": False, "create": False, "edit": False, "delete": False, "view_balance": False, "manage_credit": False},
            "accounting": {"view": False, "receive_payment": False, "create_expense": False, "edit_expense": False, "generate_interest": False, "generate_penalty": False, "manage_funds": False},
            "price_schemes": {"view": True, "create": False, "edit": False, "delete": False},
            "reports": {"view": True, "view_profit": False, "export": False, "close_day": False},
            "settings": {"view": False, "edit": False, "manage_users": False, "manage_permissions": False},
            "count_sheets": {"view": True, "create": True, "count": True, "complete": False, "cancel": False, "adjust": False},
        }
    },
}

# Legacy mapping for backward compatibility
# "inventory" and "inventory_clerk" are treated as aliases — both use inventory_clerk preset
DEFAULT_PERMISSIONS = {
    "admin": ROLE_PRESETS["admin"]["permissions"],
    "manager": ROLE_PRESETS["manager"]["permissions"],
    "cashier": ROLE_PRESETS["cashier"]["permissions"],
    "inventory": ROLE_PRESETS["inventory_clerk"]["permissions"],
    "inventory_clerk": ROLE_PRESETS["inventory_clerk"]["permissions"],
    "staff": ROLE_PRESETS["cashier"]["permissions"],  # staff defaults to cashier-level access
}

# All known system role names (used for validation)
SYSTEM_ROLES = {"admin", "manager", "cashier", "inventory", "inventory_clerk", "staff"}

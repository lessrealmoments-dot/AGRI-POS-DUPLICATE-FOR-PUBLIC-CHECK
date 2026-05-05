"""
Employee management routes.
"""
from fastapi import APIRouter, Depends, HTTPException
from typing import Optional
from datetime import datetime, timezone
from config import db
from utils import get_current_user, check_perm, now_iso, new_id, today_local

router = APIRouter(prefix="/employees", tags=["Employees"])


@router.get("")
async def list_employees(user=Depends(get_current_user), branch_id: Optional[str] = None, active: bool = True):
    """List all employees."""
    query = {"active": active}
    if branch_id:
        query["branch_id"] = branch_id
    employees = await db.employees.find(query, {"_id": 0}).to_list(200)
    return employees


@router.post("")
async def create_employee(data: dict, user=Depends(get_current_user)):
    """Create a new employee."""
    check_perm(user, "settings", "manage_users")
    employee = {
        "id": new_id(),
        "name": data["name"],
        "position": data.get("position", ""),
        "employment_type": data.get("employment_type", "regular"),  # regular, contractual, daily_wage
        "phone": data.get("phone", ""),
        "email": data.get("email", ""),
        "address": data.get("address", ""),
        "branch_id": data.get("branch_id", ""),
        "hire_date": data.get("hire_date") or await today_local(user.get("organization_id") or ""),
        "salary": float(data.get("salary", 0)),
        "daily_rate": float(data.get("daily_rate", 0)),
        "monthly_ca_limit": float(data.get("monthly_ca_limit", 0)),  # 0 = no limit
        "advance_balance": 0.0,  # Total unpaid cash advances
        "sss_number": data.get("sss_number", ""),
        "philhealth_number": data.get("philhealth_number", ""),
        "pagibig_number": data.get("pagibig_number", ""),
        "tin_number": data.get("tin_number", ""),
        "emergency_contact_name": data.get("emergency_contact_name", ""),
        "emergency_contact_phone": data.get("emergency_contact_phone", ""),
        "notes": data.get("notes", ""),
        "active": True,
        "created_at": now_iso(),
    }
    await db.employees.insert_one(employee)
    del employee["_id"]
    return employee


@router.get("/ca-report")
async def get_ca_report(
    user=Depends(get_current_user),
    branch_id: Optional[str] = None,
    month: Optional[str] = None,
):
    """
    Employee Cash Advance Summary Report.
    Returns all employees with their CA usage, limits, overages, and unpaid balances.
    Optional filters: branch_id, month (YYYY-MM format, defaults to current month).
    """
    now_dt = datetime.now(timezone.utc)
    if month:
        try:
            parts = month.split("-")
            year, mo = int(parts[0]), int(parts[1])
        except (ValueError, IndexError):
            year, mo = now_dt.year, now_dt.month
    else:
        year, mo = now_dt.year, now_dt.month

    month_start = f"{year}-{mo:02d}-01"
    if mo == 12:
        next_month_start = f"{year + 1}-01-01"
    else:
        next_month_start = f"{year}-{mo + 1:02d}-01"

    # Previous month bounds
    if mo == 1:
        prev_year, prev_mo = year - 1, 12
    else:
        prev_year, prev_mo = year, mo - 1
    prev_start = f"{prev_year}-{prev_mo:02d}-01"

    # Fetch employees
    emp_query = {"active": True}
    if branch_id:
        emp_query["branch_id"] = branch_id
    employees = await db.employees.find(emp_query, {"_id": 0}).to_list(500)

    # Aggregate all CA expenses for the selected month
    ca_pipeline = [
        {"$match": {
            "category": "Employee Advance",
            "date": {"$gte": month_start, "$lt": next_month_start},
            "voided": {"$ne": True},
        }},
        {"$group": {
            "_id": "$employee_id",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1},
            "over_limit_count": {
                "$sum": {"$cond": [{"$and": [{"$ne": ["$manager_approved_by", ""]}, {"$ne": ["$manager_approved_by", None]}]}, 1, 0]}
            },
            "last_date": {"$max": "$date"},
        }}
    ]
    ca_data = await db.expenses.aggregate(ca_pipeline).to_list(500)
    ca_map = {item["_id"]: item for item in ca_data}

    # Previous month totals
    prev_pipeline = [
        {"$match": {
            "category": "Employee Advance",
            "date": {"$gte": prev_start, "$lt": month_start},
            "voided": {"$ne": True},
        }},
        {"$group": {"_id": "$employee_id", "total": {"$sum": "$amount"}}}
    ]
    prev_data = await db.expenses.aggregate(prev_pipeline).to_list(500)
    prev_map = {item["_id"]: item["total"] for item in prev_data}

    report = []
    total_advances = 0
    total_unpaid = 0
    over_limit_employees = 0

    for emp in employees:
        eid = emp["id"]
        limit = float(emp.get("monthly_ca_limit", 0))
        balance = float(emp.get("advance_balance", 0))
        ca = ca_map.get(eid, {})
        this_month = ca.get("total", 0)
        prev_month = prev_map.get(eid, 0)
        prev_overage = max(0, prev_month - limit) if limit > 0 else 0
        is_over = limit > 0 and this_month > limit
        remaining = max(0, limit - this_month) if limit > 0 else None

        if is_over:
            over_limit_employees += 1
        total_advances += this_month
        total_unpaid += balance

        row = {
            "employee_id": eid,
            "name": emp.get("name", ""),
            "position": emp.get("position", ""),
            "branch_id": emp.get("branch_id", ""),
            "monthly_ca_limit": limit,
            "this_month_total": round(this_month, 2),
            "this_month_count": ca.get("count", 0),
            "over_limit_count": ca.get("over_limit_count", 0),
            "prev_month_total": round(prev_month, 2),
            "prev_month_overage": round(prev_overage, 2),
            "remaining": round(remaining, 2) if remaining is not None else None,
            "is_over_limit": is_over,
            "unpaid_balance": round(balance, 2),
            "last_advance_date": ca.get("last_date", ""),
            "usage_pct": round((this_month / limit) * 100, 1) if limit > 0 else None,
        }
        report.append(row)

    # Sort: over-limit first, then by unpaid balance desc
    report.sort(key=lambda r: (not r["is_over_limit"], -r["unpaid_balance"]))

    return {
        "month": f"{year}-{mo:02d}",
        "total_employees": len(employees),
        "total_advances_this_month": round(total_advances, 2),
        "total_unpaid_balance": round(total_unpaid, 2),
        "over_limit_employees": over_limit_employees,
        "employees": report,
    }


@router.get("/{emp_id}")
async def get_employee(emp_id: str, user=Depends(get_current_user)):
    """Get employee details."""
    employee = await db.employees.find_one({"id": emp_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")
    return employee


@router.put("/{emp_id}")
async def update_employee(emp_id: str, data: dict, user=Depends(get_current_user)):
    """Update employee details."""
    check_perm(user, "settings", "manage_users")
    allowed = [
        "name", "position", "employment_type", "phone", "email", "address",
        "branch_id", "salary", "daily_rate", "monthly_ca_limit", "hire_date",
        "sss_number", "philhealth_number", "pagibig_number", "tin_number",
        "emergency_contact_name", "emergency_contact_phone", "notes", "active"
    ]
    update = {k: v for k, v in data.items() if k in allowed}
    for f in ("salary", "daily_rate", "monthly_ca_limit"):
        if f in update:
            update[f] = float(update[f])
    update["updated_at"] = now_iso()
    await db.employees.update_one({"id": emp_id}, {"$set": update})
    return await db.employees.find_one({"id": emp_id}, {"_id": 0})


@router.delete("/{emp_id}")
async def delete_employee(emp_id: str, user=Depends(get_current_user)):
    """Soft delete an employee."""
    check_perm(user, "settings", "manage_users")
    await db.employees.update_one({"id": emp_id}, {"$set": {"active": False}})
    return {"message": "Employee deleted"}


@router.get("/{emp_id}/ca-summary")
async def get_employee_ca_summary(emp_id: str, user=Depends(get_current_user)):
    """Get Cash Advance summary for an employee (current month, previous month overage)."""
    employee = await db.employees.find_one({"id": emp_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    now = datetime.now(timezone.utc)
    year, month = now.year, now.month
    month_start = f"{year}-{month:02d}-01"

    # Previous month bounds
    if month == 1:
        prev_year, prev_month = year - 1, 12
    else:
        prev_year, prev_month = year, month - 1
    prev_start = f"{prev_year}-{prev_month:02d}-01"
    prev_end = month_start

    # Aggregation helpers
    async def sum_ca(date_filter):
        res = await db.expenses.aggregate([
            {"$match": {"employee_id": emp_id, "category": "Employee Advance", "date": date_filter}},
            {"$group": {"_id": None, "total": {"$sum": "$amount"}}}
        ]).to_list(1)
        return res[0]["total"] if res else 0

    this_month_total = await sum_ca({"$gte": month_start})
    prev_month_total = await sum_ca({"$gte": prev_start, "$lt": prev_end})

    monthly_limit = float(employee.get("monthly_ca_limit", 0))
    prev_overage = max(0, prev_month_total - monthly_limit) if monthly_limit > 0 else 0
    remaining = max(0, monthly_limit - this_month_total) if monthly_limit > 0 else None
    is_over_limit = monthly_limit > 0 and this_month_total >= monthly_limit

    # Recent advances this month
    recent = await db.expenses.find(
        {"employee_id": emp_id, "category": "Employee Advance", "date": {"$gte": month_start}},
        {"_id": 0}
    ).sort("date", -1).to_list(20)

    return {
        "employee_id": emp_id,
        "employee_name": employee["name"],
        "monthly_ca_limit": monthly_limit,
        "this_month_total": this_month_total,
        "prev_month_total": prev_month_total,
        "prev_month_overage": prev_overage,  # Amount over limit last month
        "remaining_this_month": remaining,
        "total_advance_balance": float(employee.get("advance_balance", 0)),
        "is_over_limit": is_over_limit,
        "recent_advances": recent,
    }


@router.get("/{emp_id}/advances")
async def get_employee_advances(emp_id: str, user=Depends(get_current_user)):
    """Get full advance history for an employee."""
    advances = await db.expenses.find(
        {"employee_id": emp_id, "category": "Employee Advance"},
        {"_id": 0}
    ).sort("created_at", -1).to_list(500)
    return advances


@router.post("/{emp_id}/deduct-advance")
async def deduct_from_advance(emp_id: str, data: dict, user=Depends(get_current_user)):
    """Deduct from employee's advance balance (e.g., salary deduction)."""
    check_perm(user, "accounting", "create_expense")
    employee = await db.employees.find_one({"id": emp_id}, {"_id": 0})
    if not employee:
        raise HTTPException(status_code=404, detail="Employee not found")

    amount = float(data["amount"])
    if amount > employee.get("advance_balance", 0):
        raise HTTPException(status_code=400, detail="Amount exceeds advance balance")

    await db.employees.update_one({"id": emp_id}, {"$inc": {"advance_balance": -amount}})

    log = {
        "id": new_id(),
        "employee_id": emp_id,
        "employee_name": employee["name"],
        "type": "deduction",
        "amount": amount,
        "reason": data.get("reason", "Salary deduction"),
        "recorded_by": user["id"],
        "recorded_by_name": user.get("full_name", user["username"]),
        "recorded_at": now_iso(),
    }
    await db.employee_advance_logs.insert_one(log)
    del log["_id"]
    return {"message": "Advance deducted", "new_balance": employee.get("advance_balance", 0) - amount, "log": log}

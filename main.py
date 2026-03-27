from fastapi import FastAPI, Header, HTTPException
from datetime import datetime, timedelta

app = FastAPI()

API_KEY = "stoklyn-secret-key"


def parse_pattern(pattern: str):
    if not pattern or "/" not in pattern:
        return None
    try:
        work_days, off_days = pattern.split("/")
        return int(work_days), int(off_days)
    except Exception:
        return None


def is_employee_available(employee, target_date, rest_days_map):
    employee_id = employee.get("id")
    weekly_pattern = (employee.get("weekly_pattern") or "").strip()
    hire_date = employee.get("hire_date") or "2026-01-01"

    rest_info = rest_days_map.get(employee_id, {})
    off_days = rest_info.get("off_days", [])

    weekday = target_date.weekday()
    if weekday in off_days:
        return False

    pattern = parse_pattern(weekly_pattern)
    if not pattern:
        return True

    work_days, off_days_count = pattern
    cycle = work_days + off_days_count
    if cycle <= 0:
        return True

    try:
        start_date = datetime.strptime(hire_date, "%Y-%m-%d").date()
    except Exception:
        start_date = datetime(2026, 1, 1).date()

    delta_days = (target_date - start_date).days
    if delta_days < 0:
        return False

    position_in_cycle = delta_days % cycle
    return position_in_cycle < work_days


def employee_matches_role(employee, department, role):
    main_role = (employee.get("main_role") or "").strip().lower()
    secondary_role = (employee.get("secondary_role") or "").strip().lower()
    employee_department = (employee.get("department") or "").strip().lower()

    target_department = (department or "").strip().lower()
    target_role = (role or "").strip().lower()

    if employee_department != target_department:
        return False, None

    if main_role == target_role:
        return True, "main"

    if secondary_role == target_role:
        return True, "secondary"

    return False, None


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/generate-schedule")
def generate_schedule(data: dict, x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
    end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()

    employees = [e for e in data.get("employees", []) if (e.get("status") or "").lower() == "activ"]
    teams = data.get("teams", [])
    employee_rest_days = data.get("employee_rest_days", [])
    employee_leaves = data.get("employee_leaves", [])
    daily_staffing_requirements = data.get("daily_staffing_requirements", [])

    rest_days_map = {item["employee_id"]: item for item in employee_rest_days}

    leave_map = {}
    for leave in employee_leaves:
        emp_id = leave.get("employee_id")
        if not emp_id:
            continue
        leave_start = datetime.strptime(leave["start_date"], "%Y-%m-%d").date()
        leave_end = datetime.strptime(leave["end_date"], "%Y-%m-%d").date()
        current = leave_start
        while current <= leave_end:
            leave_map.setdefault(emp_id, set()).add(current)
            current += timedelta(days=1)

    team_map = {}
    for team in teams:
        team_id = team.get("id")
        for member_id in team.get("member_ids", []):
            team_map[member_id] = team_id

    generated_schedule = []
    shortages = []
    conflicts = []
    total_assignments = 0

    current_date = start_date
    while current_date <= end_date:
        day_assignments = []
        assigned_employee_ids = set()

        for requirement in daily_staffing_requirements:
            department = requirement.get("department")
            role = requirement.get("role")
            shift_type = requirement.get("shift_type", "normal")
            required_staff = int(requirement.get("required_staff", 0))

            matched_employees = []

            for employee in employees:
                emp_id = employee.get("id")

                if emp_id in assigned_employee_ids:
                    continue

                if current_date in leave_map.get(emp_id, set()):
                    continue

                if not is_employee_available(employee, current_date, rest_days_map):
                    continue

                matches, source_role_type = employee_matches_role(employee, department, role)
                if not matches:
                    continue

                matched_employees.append((employee, source_role_type))

            matched_employees.sort(key=lambda item: 0 if item[1] == "main" else 1)

            selected = matched_employees[:required_staff]

            for employee, source_role_type in selected:
                emp_id = employee.get("id")
                assigned_employee_ids.add(emp_id)
                total_assignments += 1

                day_assignments.append({
                    "employee_id": emp_id,
                    "employee_name": employee.get("full_name", ""),
                    "department": department,
                    "role": role,
                    "shift_type": shift_type,
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "team_id": team_map.get(emp_id, "")
                })

            if len(selected) < required_staff:
                shortages.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "department": department,
                    "role": role,
                    "required": required_staff,
                    "assigned": len(selected),
                    "missing": required_staff - len(selected)
                })

        generated_schedule.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "assignments": day_assignments
        })

        current_date += timedelta(days=1)

    return {
        "generated_schedule": generated_schedule,
        "conflicts": conflicts,
        "shortages": shortages,
        "summary": {
            "total_days": len(generated_schedule),
            "total_assignments": total_assignments,
            "total_shortages": len(shortages),
            "total_conflicts": len(conflicts)
        }
    }

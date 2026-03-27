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


def build_team_map(teams):
    team_map = {}
    for team in teams:
        team_id = team.get("id")
        for member_id in team.get("member_ids", []):
            team_map[member_id] = team_id
    return team_map


def get_active_rules(data):
    rules = {
        "use_teams": True,
        "respect_roles_and_staffing": True,
        "respect_rest_days": True
    }

    raw_rules = data.get("generator_rules", [])
    if isinstance(raw_rules, list):
        for rule in raw_rules:
            name = (rule.get("name") or "").strip().lower()
            active = bool(rule.get("is_active", False))

            if "echipe" in name:
                rules["use_teams"] = active
            elif "roluri" in name or "necesar" in name:
                rules["respect_roles_and_staffing"] = active
            elif "zile libere" in name or "odihn" in name:
                rules["respect_rest_days"] = active

    return rules


def is_on_leave(emp_id, current_date, leave_map):
    return current_date in leave_map.get(emp_id, set())


def can_work(employee, current_date, rest_days_map, leave_map, rules):
    emp_id = employee.get("id")

    if is_on_leave(emp_id, current_date, leave_map):
        return False

    if rules["respect_rest_days"]:
        if not is_employee_available(employee, current_date, rest_days_map):
            return False

    return True


def create_assignment(employee, department, role, shift_type, team_map):
    emp_id = employee.get("id")
    return {
        "employee_id": emp_id,
        "employee_name": employee.get("full_name", ""),
        "department": department,
        "role": role,
        "shift_type": shift_type,
        "start_time": "09:00",
        "end_time": "18:00",
        "team_id": team_map.get(emp_id, "")
    }


@app.get("/")
def root():
    return {"status": "ok"}


@app.post("/generate-schedule")
def generate_schedule(data: dict, x_api_key: str = Header(None)):
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

    start_date = datetime.strptime(data["start_date"], "%Y-%m-%d").date()
    end_date = datetime.strptime(data["end_date"], "%Y-%m-%d").date()

    employees = [
        e for e in data.get("employees", [])
        if (e.get("status") or "").strip().lower() == "activ"
    ]
    teams = data.get("teams", [])
    employee_rest_days = data.get("employee_rest_days", [])
    employee_leaves = data.get("employee_leaves", [])
    daily_staffing_requirements = data.get("daily_staffing_requirements", [])

    rules = get_active_rules(data)

    rest_days_map = {item["employee_id"]: item for item in employee_rest_days}
    team_map = build_team_map(teams)

    leave_map = {}
    for leave in employee_leaves:
        emp_id = leave.get("employee_id")
        if not emp_id:
            continue

        try:
            leave_start = datetime.strptime(leave["start_date"], "%Y-%m-%d").date()
            leave_end = datetime.strptime(leave["end_date"], "%Y-%m-%d").date()
        except Exception:
            continue

        current = leave_start
        while current <= leave_end:
            leave_map.setdefault(emp_id, set()).add(current)
            current += timedelta(days=1)

    employees_by_id = {e.get("id"): e for e in employees}

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

            selected_employees = []

            if rules["use_teams"]:
                matching_teams = [
                    team for team in teams
                    if (team.get("department") or "").strip().lower() == (department or "").strip().lower()
                    and bool(team.get("is_active", True))
                ]

                team_candidates = []

                for team in matching_teams:
                    available_members = []

                    for member_id in team.get("member_ids", []):
                        employee = employees_by_id.get(member_id)
                        if not employee:
                            continue

                        if member_id in assigned_employee_ids:
                            continue

                        matches, source_role_type = employee_matches_role(employee, department, role)
                        if not matches:
                            continue

                        if not can_work(employee, current_date, rest_days_map, leave_map, rules):
                            continue

                        available_members.append((employee, source_role_type))

                    available_members.sort(key=lambda item: 0 if item[1] == "main" else 1)

                    if available_members:
                        team_candidates.append((team, available_members))

                team_candidates.sort(key=lambda item: len(item[1]), reverse=True)

                for team, members in team_candidates:
                    if len(selected_employees) >= required_staff:
                        break

                    remaining_needed = required_staff - len(selected_employees)

                    if len(members) <= remaining_needed:
                        for employee, _ in members:
                            emp_id = employee.get("id")
                            if emp_id not in assigned_employee_ids and emp_id not in [e.get("id") for e in selected_employees]:
                                selected_employees.append(employee)
                    else:
                        if remaining_needed > 0:
                            for employee, _ in members[:remaining_needed]:
                                emp_id = employee.get("id")
                                if emp_id not in assigned_employee_ids and emp_id not in [e.get("id") for e in selected_employees]:
                                    selected_employees.append(employee)

            if len(selected_employees) < required_staff:
                matched_employees = []

                for employee in employees:
                    emp_id = employee.get("id")

                    if emp_id in assigned_employee_ids:
                        continue

                    if emp_id in [e.get("id") for e in selected_employees]:
                        continue

                    if not can_work(employee, current_date, rest_days_map, leave_map, rules):
                        continue

                    matches, source_role_type = employee_matches_role(employee, department, role)
                    if not matches:
                        continue

                    matched_employees.append((employee, source_role_type))

                matched_employees.sort(key=lambda item: 0 if item[1] == "main" else 1)

                remaining_needed = required_staff - len(selected_employees)
                for employee, _ in matched_employees[:remaining_needed]:
                    selected_employees.append(employee)

            for employee in selected_employees[:required_staff]:
                emp_id = employee.get("id")
                assigned_employee_ids.add(emp_id)
                total_assignments += 1

                day_assignments.append(
                    create_assignment(employee, department, role, shift_type, team_map)
                )

            assigned_count = min(len(selected_employees), required_staff)
            if assigned_count < required_staff:
                shortages.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "department": department,
                    "role": role,
                    "required": required_staff,
                    "assigned": assigned_count,
                    "missing": required_staff - assigned_count
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

from fastapi import FastAPI, Header, HTTPException
from datetime import datetime, timedelta

app = FastAPI()

API_KEY = "stoklyn-secret-key"


def parse_pattern(pattern: str):
    if not pattern:
        return None

    pattern = str(pattern).strip().replace(" ", "")
    if "/" not in pattern:
        return None

    try:
        work_days, off_days = pattern.split("/", 1)
        work_days = int(work_days)
        off_days = int(off_days)

        if work_days < 0 or off_days < 0:
            return None

        return work_days, off_days
    except Exception:
        return None


def parse_date_safe(value, default_date):
    try:
        return datetime.strptime(str(value).strip(), "%Y-%m-%d").date()
    except Exception:
        return default_date


def normalize_off_days(off_days):
    normalized = set()

    if not isinstance(off_days, list):
        return normalized

    for day in off_days:
        try:
            value = int(day)
            if 0 <= value <= 6:
                normalized.add(value)
        except Exception:
            continue

    return normalized


def get_pattern_start_date(employee):
    fallback = datetime(2026, 1, 1).date()

    for field in ["pattern_start_date", "cycle_start_date", "hire_date"]:
        value = employee.get(field)
        if value:
            return parse_date_safe(value, fallback)

    return fallback


def is_employee_available(employee, target_date, rest_days_map):
    employee_id = employee.get("id")
    weekly_pattern = (employee.get("weekly_pattern") or "").strip()

    rest_info = rest_days_map.get(employee_id, {})
    off_days = normalize_off_days(rest_info.get("off_days", []))

    weekday = target_date.weekday()
    if weekday in off_days:
        return False

    if not weekly_pattern:
        return True

    pattern = parse_pattern(weekly_pattern)
    if not pattern:
        return False

    work_days, off_days_count = pattern
    cycle = work_days + off_days_count

    if cycle <= 0:
        return False

    start_date = get_pattern_start_date(employee)
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

    if not target_role:
        return True, "primary"

    if main_role == target_role:
        return True, "primary"

    if secondary_role == target_role:
        return True, "secondary"

    return False, None


def get_employee_team_id(employee, team_map):
    return team_map.get(employee.get("id")) or employee.get("team_id") or ""


def build_team_map(teams, employees):
    team_map = {}

    for team in teams:
        team_id = team.get("id")
        for member_id in team.get("member_ids", []):
            if team_id and member_id:
                team_map[member_id] = team_id

    for employee in employees:
        emp_id = employee.get("id")
        employee_team_id = employee.get("team_id")
        if emp_id and employee_team_id and emp_id not in team_map:
            team_map[emp_id] = employee_team_id

    return team_map


def group_candidates_by_team(candidates, team_map):
    grouped = {}
    no_team = []

    for employee, role_source in candidates:
        team_id = get_employee_team_id(employee, team_map)
        if team_id:
            grouped.setdefault(team_id, []).append((employee, role_source))
        else:
            no_team.append((employee, role_source))

    return grouped, no_team


def sort_candidate_pool(candidate_pool, employee_assignment_counts):
    return sorted(candidate_pool, key=lambda item: (
        employee_assignment_counts.get(item[0].get("id"), 0),
        0 if item[1] == "primary" else 1,
        (item[0].get("full_name") or "").strip().lower(),
        item[0].get("id") or ""
    ))


def pick_best_candidates_for_requirement(
    primary_candidates,
    secondary_candidates,
    required_staff,
    team_usage_count,
    team_map,
    employee_assignment_counts
):
    selected = []

    primary_candidates = sort_candidate_pool(primary_candidates, employee_assignment_counts)
    secondary_candidates = sort_candidate_pool(secondary_candidates, employee_assignment_counts)

    primary_by_team, primary_without_team = group_candidates_by_team(primary_candidates, team_map)
    secondary_by_team, secondary_without_team = group_candidates_by_team(secondary_candidates, team_map)

    ordered_primary_teams = sorted(
        primary_by_team.items(),
        key=lambda item: (
            team_usage_count.get(item[0], 0),
            -len(item[1]),
            item[0]
        )
    )

    for team_id, members in ordered_primary_teams:
        if len(selected) >= required_staff:
            break
        for employee, role_source in members:
            if len(selected) >= required_staff:
                break
            if any(existing[0].get("id") == employee.get("id") for existing in selected):
                continue
            selected.append((employee, role_source))
            team_usage_count[team_id] = team_usage_count.get(team_id, 0) + 1

    if len(selected) < required_staff:
        for employee, role_source in primary_without_team:
            if len(selected) >= required_staff:
                break
            if any(existing[0].get("id") == employee.get("id") for existing in selected):
                continue
            selected.append((employee, role_source))

    if len(selected) < required_staff:
        ordered_secondary_teams = sorted(
            secondary_by_team.items(),
            key=lambda item: (
                team_usage_count.get(item[0], 0),
                -len(item[1]),
                item[0]
            )
        )

        for team_id, members in ordered_secondary_teams:
            if len(selected) >= required_staff:
                break
            for employee, role_source in members:
                if len(selected) >= required_staff:
                    break
                if any(existing[0].get("id") == employee.get("id") for existing in selected):
                    continue
                selected.append((employee, role_source))
                team_usage_count[team_id] = team_usage_count.get(team_id, 0) + 1

    if len(selected) < required_staff:
        for employee, role_source in secondary_without_team:
            if len(selected) >= required_staff:
                break
            if any(existing[0].get("id") == employee.get("id") for existing in selected):
                continue
            selected.append((employee, role_source))

    return selected[:required_staff]


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

    rest_days_map = {item["employee_id"]: item for item in employee_rest_days if item.get("employee_id")}

    leave_map = {}
    for leave in employee_leaves:
        emp_id = leave.get("employee_id")
        if not emp_id:
            continue
        leave_start = parse_date_safe(leave.get("start_date"), start_date)
        leave_end = parse_date_safe(leave.get("end_date"), leave_start)
        current = leave_start
        while current <= leave_end:
            leave_map.setdefault(emp_id, set()).add(current)
            current += timedelta(days=1)

    team_map = build_team_map(teams, employees)

    generated_schedule = []
    shortages = []
    conflicts = []
    total_assignments = 0
    team_usage_count = {}
    employee_assignment_counts = {}
    employee_name_map = {employee.get("id"): employee.get("full_name", "") for employee in employees}
    secondary_role_assignments_count = 0

    current_date = start_date
    while current_date <= end_date:
        day_assignments = []
        assigned_employee_ids = set()

        for requirement in daily_staffing_requirements:
            department = requirement.get("department")
            role = requirement.get("role")
            shift_type = requirement.get("shift_type", "normal")
            required_staff = int(requirement.get("required_staff", 0))

            if required_staff <= 0:
                continue

            primary_candidates = []
            secondary_candidates = []

            for employee in employees:
                emp_id = employee.get("id")

                if not emp_id:
                    continue

                if emp_id in assigned_employee_ids:
                    continue

                if current_date in leave_map.get(emp_id, set()):
                    continue

                if not is_employee_available(employee, current_date, rest_days_map):
                    continue

                matches, role_source = employee_matches_role(employee, department, role)
                if not matches:
                    continue

                candidate = (employee, role_source)

                if role_source == "primary":
                    primary_candidates.append(candidate)
                elif role_source == "secondary":
                    secondary_candidates.append(candidate)

            selected = pick_best_candidates_for_requirement(
                primary_candidates=primary_candidates,
                secondary_candidates=secondary_candidates,
                required_staff=required_staff,
                team_usage_count=team_usage_count,
                team_map=team_map,
                employee_assignment_counts=employee_assignment_counts
            )

            for employee, role_source in selected:
                emp_id = employee.get("id")
                if emp_id in assigned_employee_ids:
                    continue

                assigned_employee_ids.add(emp_id)
                total_assignments += 1
                employee_assignment_counts[emp_id] = employee_assignment_counts.get(emp_id, 0) + 1

                if role_source == "secondary":
                    secondary_role_assignments_count += 1

                day_assignments.append({
                    "employee_id": emp_id,
                    "employee_name": employee.get("full_name", ""),
                    "department": department,
                    "role": role,
                    "role_source": role_source,
                    "shift_type": shift_type,
                    "start_time": "09:00",
                    "end_time": "18:00",
                    "team_id": get_employee_team_id(employee, team_map)
                })

            assigned_count = len(selected)

            if assigned_count < required_staff:
                total_compatible = len(primary_candidates) + len(secondary_candidates)
                primary_count = len(primary_candidates)
                secondary_count = len(secondary_candidates)

                if total_compatible == 0:
                    reason = "Nu există angajați compatibili disponibili"
                elif primary_count == 0 and secondary_count > 0:
                    reason = f"Disponibili doar pe rol secundar: {secondary_count}, alocați: {assigned_count}"
                else:
                    reason = (
                        f"Disponibili compatibili: {total_compatible}, "
                        f"principal: {primary_count}, secundar: {secondary_count}, "
                        f"alocați: {assigned_count}"
                    )

                shortages.append({
                    "date": current_date.strftime("%Y-%m-%d"),
                    "department": department,
                    "role": role,
                    "shift_type": shift_type,
                    "missing_count": required_staff - assigned_count,
                    "reason": reason
                })

        generated_schedule.append({
            "date": current_date.strftime("%Y-%m-%d"),
            "assignments": day_assignments
        })

        current_date += timedelta(days=1)

    employees_overused = sorted(
        [
            {
                "employee_id": emp_id,
                "employee_name": employee_name_map.get(emp_id, ""),
                "assignment_count": assignment_count
            }
            for emp_id, assignment_count in employee_assignment_counts.items()
        ],
        key=lambda item: (-item["assignment_count"], item["employee_name"], item["employee_id"])
    )

    return {
        "generated_schedule": generated_schedule,
        "conflicts": conflicts,
        "shortages": shortages,
        "summary": {
            "total_days": len(generated_schedule),
            "total_assignments": total_assignments,
            "total_shortages": len(shortages),
            "total_conflicts": len(conflicts),
            "team_usage_count": team_usage_count,
            "secondary_role_assignments_count": secondary_role_assignments_count,
            "employees_overused": employees_overused
        }
    }

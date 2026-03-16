import os
import json
import gspread
from google.oauth2.service_account import Credentials
from garminconnect import Garmin
from datetime import date, timedelta

# ── Auth ──────────────────────────────────────────────────────────────────────

def get_sheet():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(os.environ["GOOGLE_SHEET_ID"])
    return sheet

def get_garmin():
    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()
    return client

# ── Helpers ───────────────────────────────────────────────────────────────────

def meters_to_miles(m):
    return round(m / 1609.34, 2) if m else 0

def mps_to_pace(mps):
    if not mps or mps == 0:
        return ""
    spm = 1609.34 / mps
    mins = int(spm // 60)
    secs = int(spm % 60)
    return f"{mins}:{secs:02d}"

def seconds_to_time(s):
    if not s:
        return ""
    h = int(s // 3600)
    m = int((s % 3600) // 60)
    s = int(s % 60)
    return f"{h}:{m:02d}:{s:02d}"

def safe(val, default=0):
    return val if val is not None else default

# ── Runs Sync ─────────────────────────────────────────────────────────────────

def sync_runs(garmin, sheet):
    ws = sheet.worksheet("Garmin Runs")
    
    headers = [
        "Date", "Activity ID", "Activity Name", "Type",
        "Distance (mi)", "Moving Time", "Pace (min/mi)",
        "Avg HR", "Max HR", "Avg Cadence", "Elevation Gain (ft)",
        "Calories", "Training Effect (Aerobic)", "Training Effect (Anaerobic)",
        "Avg Power", "Normalized Power", "Activity URL"
    ]
    
    # Get existing activity IDs to avoid duplicates
    existing = ws.get_all_values()
    if not existing or existing[0] != headers:
        ws.clear()
        ws.append_row(headers)
        existing = [headers]
    
    existing_ids = set(row[1] for row in existing[1:] if len(row) > 1)
    
    # Pull last 30 days of activities
    end = date.today()
    start = end - timedelta(days=30)
    activities = garmin.get_activities_by_date(start.isoformat(), end.isoformat())
    
    new_rows = []
    for a in activities:
        activity_id = str(a.get("activityId", ""))
        if activity_id in existing_ids:
            continue
            
        activity_type = a.get("activityType", {}).get("typeKey", "")
        
        row = [
            a.get("startTimeLocal", "")[:10],
            activity_id,
            a.get("activityName", ""),
            activity_type,
            meters_to_miles(a.get("distance", 0)),
            seconds_to_time(a.get("movingDuration", 0)),
            mps_to_pace(a.get("averageSpeed", 0)),
            safe(a.get("averageHR")),
            safe(a.get("maxHR")),
            safe(a.get("averageRunningCadenceInStepsPerMinute")),
            round(safe(a.get("elevationGain", 0)) * 3.28084, 1),
            safe(a.get("calories")),
            safe(a.get("aerobicTrainingEffect")),
            safe(a.get("anaerobicTrainingEffect")),
            safe(a.get("avgPower")),
            safe(a.get("normPower")),
            f"https://www.strava.com/activities/{activity_id}",
        ]
        new_rows.append(row)
    
    if new_rows:
        ws.append_rows(new_rows)
        print(f"✅ Added {len(new_rows)} new runs")
    else:
        print("✅ Runs: nothing new to add")

# ── Health Sync ───────────────────────────────────────────────────────────────

def sync_health(garmin, sheet):
    ws = sheet.worksheet("Garmin Health")
    
    headers = [
        "Date", "Weight (lbs)", "Body Fat %",
        "Resting HR", "HRV Status", "HRV Value",
        "Body Battery (AM)", "Body Battery (PM)",
        "Sleep Duration (hrs)", "Sleep Score",
        "Training Readiness", "Recovery Time (hrs)",
        "Steps", "Stress (avg)"
    ]
    
    existing = ws.get_all_values()
    if not existing or existing[0] != headers:
        ws.clear()
        ws.append_row(headers)
        existing = [headers]
    
    existing_dates = set(row[0] for row in existing[1:] if row)
    
    # Pull last 30 days
    end = date.today()
    start = end - timedelta(days=30)
    
    new_rows = []
    current = start
    while current <= end:
        date_str = current.isoformat()
        if date_str in existing_dates:
            current += timedelta(days=1)
            continue
        
        try:
            # Weight
            weight_data = garmin.get_body_composition(date_str)
            weight_lbs = ""
            body_fat = ""
            if weight_data and weight_data.get("dateWeightList"):
                w = weight_data["dateWeightList"][0]
                weight_kg = w.get("weight", 0) / 1000
                weight_lbs = round(weight_kg * 2.20462, 1) if weight_kg else ""
                body_fat = safe(w.get("bodyFat", ""))

            # Heart rate
            hr_data = garmin.get_heart_rates(date_str)
            resting_hr = safe(hr_data.get("restingHeartRate", "")) if hr_data else ""

            # HRV
            hrv_data = garmin.get_hrv_data(date_str)
            hrv_status = ""
            hrv_value = ""
            if hrv_data and hrv_data.get("hrvSummary"):
                hrv_status = hrv_data["hrvSummary"].get("status", "")
                hrv_value = safe(hrv_data["hrvSummary"].get("weeklyAvg", ""))

            # Body Battery
            bb_data = garmin.get_body_battery(date_str)
            bb_am = ""
            bb_pm = ""
            if bb_data and len(bb_data) > 0:
                values = [v[1] for v in bb_data[0].get("bodyBatteryValuesArray", []) if v[1]]
                if values:
                    bb_am = max(values)
                    bb_pm = min(values)

            # Sleep
            sleep_data = garmin.get_sleep_data(date_str)
            sleep_hrs = ""
            sleep_score = ""
            if sleep_data and sleep_data.get("dailySleepDTO"):
                s = sleep_data["dailySleepDTO"]
                total_secs = safe(s.get("sleepTimeSeconds", 0))
                sleep_hrs = round(total_secs / 3600, 1) if total_secs else ""
                sleep_score = safe(s.get("sleepScores", {}).get("overall", {}).get("value", ""))

            # Training readiness + recovery
            readiness = ""
            recovery_hrs = ""
            try:
                tr_data = garmin.get_training_readiness(date_str)
                if tr_data:
                    readiness = safe(tr_data.get("score", ""))
                    recovery_hrs = safe(tr_data.get("recoveryTime", ""))
            except:
                pass

            # Steps
            steps_data = garmin.get_steps_data(date_str)
            steps = ""
            if steps_data:
                steps = sum(s.get("steps", 0) for s in steps_data)

            # Stress
            stress_data = garmin.get_stress_data(date_str)
            avg_stress = ""
            if stress_data:
                avg_stress = safe(stress_data.get("avgStressLevel", ""))

            row = [
                date_str, weight_lbs, body_fat,
                resting_hr, hrv_status, hrv_value,
                bb_am, bb_pm,
                sleep_hrs, sleep_score,
                readiness, recovery_hrs,
                steps, avg_stress
            ]
            new_rows.append(row)

        except Exception as e:
            print(f"⚠️ Error on {date_str}: {e}")
        
        current += timedelta(days=1)
    
    if new_rows:
        ws.append_rows(new_rows)
        print(f"✅ Added {len(new_rows)} days of health data")
    else:
        print("✅ Health: nothing new to add")

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🏃 Starting Alex Dashboard sync...")
    
    try:
        sheet = get_sheet()
        print("✅ Google Sheets connected")
    except Exception as e:
        print(f"❌ Google Sheets error: {e}")
        raise

    try:
        garmin = get_garmin()
        print("✅ Garmin connected")
    except Exception as e:
        print(f"❌ Garmin login error: {e}")
        raise

    sync_runs(garmin, sheet)
    sync_health(garmin, sheet)
    
    print("🎉 Sync complete!")

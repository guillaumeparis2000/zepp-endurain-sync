import os
import requests
import urllib.parse
import math
from dotenv import load_dotenv

from fit_tool.fit_file_builder import FitFileBuilder
from fit_tool.profile.messages.file_id_message import FileIdMessage
from fit_tool.profile.messages.activity_message import ActivityMessage
from fit_tool.profile.messages.session_message import SessionMessage
from fit_tool.profile.messages.lap_message import LapMessage
from fit_tool.profile.messages.record_message import RecordMessage
from fit_tool.profile.messages.event_message import EventMessage
from fit_tool.profile.profile_type import FileType, Manufacturer, Sport, Event, EventType


class ZeppToEndurainImporter:
    def __init__(self, email, password):
        self.email = email
        self.password = password
        self.app_token = None
        self.base_url = "https://api-mifit-de2.huami.com"

    def login(self):
        safe_email = urllib.parse.quote(self.email)
        step1_url = f"https://api-user.huami.com/registrations/{safe_email}/tokens"
        step1_data = {
            "password": self.password,
            "client_id": "HuaMi",
            "token": "access",
            "state": "REDIRECTION",
            "redirect_uri": "https://s3-us-west-2.amazonaws.com/hm-registration/successsignin.html",
            "json_response": "true",
            "app_name": "com.huami.webapp"
        }
        step1_headers = {
            "app_name": "com.huami.webapp",
            "Accept-Language": "en-US,en;q=0.9",
            "Content-Type": "application/x-www-form-urlencoded"
        }

        try:
            print("Executing Zepp login Step 1...")
            resp1 = requests.post(
                step1_url, data=step1_data, headers=step1_headers)
            resp1.raise_for_status()
            data1 = resp1.json()
            access_token = data1.get("access")
            if not access_token:
                return False
            region_code = data1.get("country_code", "DE")
        except Exception:
            return False

        step2_url = "https://account.huami.com/v2/client/login"
        step2_data = {
            "country_code": region_code,
            "app_name": "com.huami.webapp",
            "third_name": "huami",
            "grant_type": "access_token",
            "code": access_token,
            "device_id": "02:00:00:00:00:00",
            "device_model": "web",
            "app_version": "4.3.0",
            "allow_registration": "false",
            "dn": "account.huami.com,api-user.huami.com,api-mifit.huami.com,auth.huami.com,api-open.huami.com"
        }
        step2_headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8"}

        try:
            print("Executing Zepp login Step 2...")
            resp2 = requests.post(
                step2_url, data=step2_data, headers=step2_headers)
            resp2.raise_for_status()
            data2 = resp2.json()
            token_info = data2.get("token_info", {})
            self.app_token = token_info.get(
                "app_token") or token_info.get("login_token")

            domains = data2.get("domains", [])
            for d in domains:
                if d.get("domain") == "api-mifit.huami.com":
                    cnames = d.get("cnames", [])
                    if cnames:
                        self.base_url = f"https://{cnames[0]}"
                        break

            if self.app_token:
                print("Zepp login fully successful.")
                return True
            return False
        except Exception:
            return False

    def get_recent_workouts(self, limit=1):
        endpoint = f"{self.base_url}/v1/sport/run/history.json"
        headers = {"apptoken": self.app_token,
                   "appPlatform": "web", "appname": "com.xiaomi.hm.health"}
        params = {"source": "huami", "limit": limit}
        try:
            response = requests.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 1:
                return data.get("data", {}).get("summary", [])
            return []
        except Exception:
            return []

    def get_workout_details(self, track_id, source_string):
        endpoint = f"{self.base_url}/v1/sport/run/detail.json"
        headers = {"apptoken": self.app_token,
                   "appPlatform": "web", "appname": "com.xiaomi.hm.health"}
        params = {"trackid": track_id, "source": source_string}
        try:
            response = requests.get(endpoint, headers=headers, params=params)
            response.raise_for_status()
            data = response.json()
            if data.get("code") == 1:
                return data.get("data", {})
            return None
        except Exception:
            return None


def endurain_login(endurain_url, username, password):
    print("Authenticating with Endurain...")
    login_url = f"{endurain_url}/api/v1/auth/login"
    payload = {"username": username, "password": password}
    headers = {"Content-Type": "application/x-www-form-urlencoded",
               "X-Client-Type": "web"}
    try:
        response = requests.post(login_url, data=payload, headers=headers)
        response.raise_for_status()
        return response.json().get("access_token")
    except Exception as e:
        print(f"Endurain Auth failed: {e}")
        return None


def extract_value(item_str):
    """Safely extracts the numerical value whether Zepp sends 'val' or 'offset,val'."""
    if not item_str:
        return None
    parts = str(item_str).split(',')
    try:
        return float(parts[-1])
    except ValueError:
        return None


def build_fit_content(workout_meta, telemetry_raw):
    """
    Parses Zepp data using accurate stream decoders:
    - Delta stream for Heart Rate.
    - 4-part Gait stream for Cadence (SPM converted to RPM).
    - Centimeter conversion for Altitude.
    - Haversine Auto-Lapper for precise 1km Tours.
    """
    print("Building FIT file with explicit Gait and HR decoders...")

    end_time = int(float(workout_meta.get("end_time", 0)))
    run_time = int(float(workout_meta.get("run_time", 0)))
    start_time_ts = end_time - run_time
    start_time_ms = int(start_time_ts * 1000)

    zepp_type = int(float(workout_meta.get("type", 1)))
    fit_sport = Sport.RUNNING
    if zepp_type == 6:
        fit_sport = Sport.WALKING
    elif zepp_type in (9, 16):
        fit_sport = Sport.CYCLING

    distance_meters = float(workout_meta.get("dis", 0))
    calories = int(float(workout_meta.get(
        "calorie", workout_meta.get("calories", 0))))

    builder = FitFileBuilder(auto_define=True)

    file_id = FileIdMessage()
    file_id.type = FileType.ACTIVITY
    file_id.manufacturer = Manufacturer.DEVELOPMENT
    file_id.time_created = start_time_ms
    file_id.serial_number = 12345
    builder.add(file_id)

    start_event = EventMessage()
    start_event.event = Event.TIMER
    start_event.event_type = EventType.START
    start_event.timestamp = start_time_ms
    builder.add(start_event)

    # 1. TIME ARRAY SYNC
    time_str = telemetry_raw.get("time", "")
    time_arr = [int(x) for x in time_str.split(";")] if time_str else []

    # 2. GPS
    gps_str = telemetry_raw.get("longitude_latitude", "")
    gps_raw = gps_str.split(";") if gps_str else []
    gps_dict = {}
    prev_lat, prev_lon = None, None
    curr_t = 0

    for i in range(max(len(time_arr), len(gps_raw))):
        dt = time_arr[i] if i < len(time_arr) else 1
        curr_t += dt
        if i < len(gps_raw) and gps_raw[i]:
            parts = gps_raw[i].split(",")
            if len(parts) >= 2:
                try:
                    val_lat = float(parts[0])
                    val_lon = float(parts[1])
                    if "." not in parts[0] or abs(val_lat) > 90.0:
                        val_lat /= 100000000.0
                    if "." not in parts[1] or abs(val_lon) > 180.0:
                        val_lon /= 100000000.0

                    if prev_lat is None:
                        lat, lon = val_lat, val_lon
                    else:
                        if abs(val_lat) < 5.0 and abs(val_lon) < 5.0:
                            lat, lon = prev_lat + val_lat, prev_lon + val_lon
                        else:
                            lat, lon = val_lat, val_lon

                    prev_lat, prev_lon = lat, lon
                    if abs(lat) > 0.1 and abs(lon) > 0.1:
                        gps_dict[curr_t] = (lat, lon)
                except ValueError:
                    pass

    # 3. ALTITUDE
    alt_str = telemetry_raw.get("altitude", "")
    alt_raw = alt_str.split(";") if alt_str else []
    alt_dict = {}
    curr_t = 0
    for i in range(max(len(time_arr), len(alt_raw))):
        dt = time_arr[i] if i < len(time_arr) else 1
        curr_t += dt
        if i < len(alt_raw) and alt_raw[i]:
            try:
                val = float(alt_raw[i])
                if val > -500000:
                    alt_dict[curr_t] = val / 100.0  # Centimeters to Meters
            except ValueError:
                pass

    # 4. HEART RATE (Delta stream: +1, -1)
    hr_dict = {}
    hr_str = telemetry_raw.get("heart_rate", "")
    curr_t = 0
    curr_hr = None
    if hr_str:
        for item in hr_str.split(";"):
            if not item:
                continue
            parts = item.split(",")
            dt = int(parts[0]) if len(parts) >= 2 and parts[0] else 1
            dv = float(parts[-1]) if parts[-1] else 0.0
            curr_t += dt
            if curr_hr is None:
                curr_hr = dv
            else:
                curr_hr += dv
            hr_dict[curr_t] = curr_hr

    # 5. CADENCE (4-part Gait stream: dt, x, y, SPM)
    cad_dict = {}
    gait_str = telemetry_raw.get("gait", "")
    curr_t = 0
    if gait_str:
        for item in gait_str.split(";"):
            if not item:
                continue
            parts = item.split(",")
            dt = int(parts[0]) if parts[0] else 1
            if len(parts) >= 4:
                spm = float(parts[3])
                curr_t += dt
                cad_dict[curr_t] = spm / 2.0  # FIT standard uses RPM (SPM/2)

    # 6. HAVERSINE DISTANCE HELPER
    def haversine(lat1, lon1, lat2, lon2):
        if lat1 is None or lon1 is None:
            return 0.0
        R = 6371000.0
        phi1, phi2 = math.radians(lat1), math.radians(lat2)
        dphi = math.radians(lat2 - lat1)
        dlambda = math.radians(lon2 - lon1)
        a = math.sin(dphi/2)**2 + math.cos(phi1) * \
            math.cos(phi2)*math.sin(dlambda/2)**2
        return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    # 7. STATS MANAGER
    class StatsTracker:
        def __init__(self, start_t, start_dist):
            self.start_t = start_t
            self.start_dist = start_dist
            self.end_t = start_t
            self.end_dist = start_dist
            self.hr_list = []
            self.cad_list = []
            self.ascent = 0.0
            self.descent = 0.0
            self.last_alt = None

        def add(self, t, dist, hr, cad, alt):
            self.end_t = t
            self.end_dist = dist
            if hr:
                self.hr_list.append(hr)
            if cad:
                self.cad_list.append(cad)
            if alt is not None:
                if self.last_alt is not None:
                    diff = alt - self.last_alt
                    if diff > 0.3:
                        self.ascent += diff
                        self.last_alt = alt
                    elif diff < -0.3:
                        self.descent += abs(diff)
                        self.last_alt = alt
                else:
                    self.last_alt = alt

        def populate_message(self, msg):
            msg.start_time = int((start_time_ts + self.start_t) * 1000)
            msg.timestamp = int((start_time_ts + self.end_t) * 1000)
            msg.total_elapsed_time = self.end_t - self.start_t
            msg.total_timer_time = self.end_t - self.start_t

            d_dist = self.end_dist - self.start_dist
            msg.total_distance = d_dist if d_dist > 0 else 0.0

            if self.hr_list:
                msg.avg_heart_rate = int(sum(self.hr_list) / len(self.hr_list))
                msg.max_heart_rate = int(max(self.hr_list))
            if self.cad_list:
                msg.avg_cadence = int(sum(self.cad_list) / len(self.cad_list))
                msg.avg_running_cadence = msg.avg_cadence
                msg.max_cadence = int(max(self.cad_list))

            msg.total_ascent = int(self.ascent)
            msg.total_descent = int(self.descent)
            return msg

    # 8. MERGE DATA
    max_t = run_time
    if gps_dict:
        max_t = max(max_t, max(gps_dict.keys()))
    if alt_dict:
        max_t = max(max_t, max(alt_dict.keys()))
    if hr_dict:
        max_t = max(max_t, max(hr_dict.keys()))
    if cad_dict:
        max_t = max(max_t, max(cad_dict.keys()))

    actual_end_time_ms = int((start_time_ts + max_t) * 1000)

    curr_hr, curr_alt, curr_cad = None, None, None
    last_lat, last_lon = None, None
    accumulated_distance = 0.0

    global_stats = StatsTracker(0, 0.0)
    current_lap = StatsTracker(0, 0.0)
    completed_laps = []
    points_processed = 0

    for t in range(max_t + 1):
        has_data = False
        record = RecordMessage()
        record.timestamp = int((start_time_ts + t) * 1000)

        if t in hr_dict:
            curr_hr = hr_dict[t]
        if t in alt_dict:
            curr_alt = alt_dict[t]
        if t in cad_dict:
            curr_cad = cad_dict[t]

        if t in gps_dict:
            lat, lon = gps_dict[t][0], gps_dict[t][1]
            record.position_lat = lat
            record.position_long = lon

            dist = haversine(last_lat, last_lon, lat, lon)
            accumulated_distance += dist
            last_lat, last_lon = lat, lon
            has_data = True

        if curr_alt is not None:
            record.enhanced_altitude = curr_alt
            has_data = True

        if curr_hr is not None and curr_hr > 0:
            hr_int = int(curr_hr)
            if hr_int <= 254:
                record.heart_rate = hr_int
                has_data = True

        if curr_cad is not None and curr_cad > 0:
            cad_int = int(curr_cad)
            if cad_int <= 254:
                record.cadence = cad_int
                has_data = True

        if has_data:
            record.distance = accumulated_distance
            builder.add(record)
            points_processed += 1

            hr_val = record.heart_rate if record.heart_rate else None
            cad_val = record.cadence if record.cadence else None
            alt_val = record.enhanced_altitude if record.enhanced_altitude else None

            global_stats.add(t, accumulated_distance, hr_val, cad_val, alt_val)
            current_lap.add(t, accumulated_distance, hr_val, cad_val, alt_val)

        # AUTO-LAPPER (Cuts exactly at 1000m thresholds)
        if accumulated_distance - current_lap.start_dist >= 1000.0:
            completed_laps.append(current_lap)
            current_lap = StatsTracker(t, accumulated_distance)

    if accumulated_distance > current_lap.start_dist or len(completed_laps) == 0:
        completed_laps.append(current_lap)

    # 9. STOP EVENT
    stop_event = EventMessage()
    stop_event.event = Event.TIMER
    stop_event.event_type = EventType.STOP_ALL
    stop_event.timestamp = actual_end_time_ms
    builder.add(stop_event)

    # 10. LAP MESSAGES
    for lap_stats in completed_laps:
        lap_msg = LapMessage()
        lap_msg.sport = fit_sport
        builder.add(lap_stats.populate_message(lap_msg))

    # 11. SESSION MESSAGE
    session_msg = SessionMessage()
    session_msg.sport = fit_sport
    session_msg.total_calories = calories
    session_msg.sub_sport = 0
    session_msg.first_lap_index = 0
    session_msg.num_laps = len(completed_laps)
    global_stats.populate_message(session_msg)
    if distance_meters > global_stats.end_dist:
        session_msg.total_distance = distance_meters
    builder.add(session_msg)

    # 12. ACTIVITY MESSAGE
    activity = ActivityMessage()
    activity.timestamp = actual_end_time_ms
    activity.total_timer_time = max_t
    activity.num_sessions = 1
    activity.type = 0
    activity.event = Event.ACTIVITY
    activity.event_type = EventType.STOP
    builder.add(activity)

    print(
        f"FIT file compiled: {points_processed} records, {len(completed_laps)} Laps.")
    return builder.build_bytes()

def upload_to_endurain_with_token(file_bytes, track_id, endurain_url, access_token):
    upload_endpoint = f"{endurain_url}/api/v1/activities/create/upload"
    headers = {"Authorization": f"Bearer {access_token}"}
    filename = f"zepp_workout_{track_id}.fit"

    files = {"file": (filename, file_bytes, "application/octet-stream")}
    print(f"Uploading FIT activity {track_id} to Endurain...")
    try:
        response = requests.post(upload_endpoint, headers=headers, files=files)
        if response.status_code in [200, 201]:
            print("Successfully uploaded FIT file to Endurain!")
            return True
        else:
            print(
                f"Failed to upload. HTTP Status: {response.status_code} - {response.text}")
            return False
    except Exception as e:
        print(f"Network error: {e}")
        return False


def load_synced_tracks(filename="synced_tracks.txt"):
    """Loads the set of already processed Zepp track IDs from a local file."""
    if not os.path.exists(filename):
        return set()
    with open(filename, "r") as f:
        return set(line.strip() for line in f if line.strip())


def mark_as_synced(track_id, filename="synced_tracks.txt"):
    """Appends a successfully uploaded track ID to the local history file."""
    with open(filename, "a") as f:
        f.write(f"{track_id}\n")


if __name__ == "__main__":
    load_dotenv()

    ZEPP_EMAIL = os.environ.get("ZEPP_EMAIL")
    ZEPP_PASSWORD = os.environ.get("ZEPP_PASSWORD")
    ENDURAIN_URL = os.environ.get("ENDURAIN_URL")
    ENDURAIN_USER = os.environ.get("ENDURAIN_USER")
    ENDURAIN_PASS = os.environ.get("ENDURAIN_PASS")

    if not all([ZEPP_EMAIL, ZEPP_PASSWORD, ENDURAIN_URL, ENDURAIN_USER, ENDURAIN_PASS]):
        print("Error: Missing credentials in .env file.")
        exit(1)

    # 1. Load history to prevent duplicates
    synced_tracks = load_synced_tracks()

    # 2. Authenticate with Endurain
    endurain_token = endurain_login(ENDURAIN_URL, ENDURAIN_USER, ENDURAIN_PASS)
    if not endurain_token:
        exit(1)

    # 3. Authenticate with Zepp
    importer = ZeppToEndurainImporter(ZEPP_EMAIL, ZEPP_PASSWORD)
    if importer.login():
        # Fetch the last 30 workouts (adjust limit as needed for historical sync)
        workouts_summary = importer.get_recent_workouts(limit=10)

        if not workouts_summary:
            print("No workouts found in Zepp account.")
            exit(0)

        workouts_to_sync = [w for w in workouts_summary if str(
            w.get("trackid")) not in synced_tracks]

        if not workouts_to_sync:
            print("All recent workouts are already synced to Endurain.")
            exit(0)

        print(f"Found {len(workouts_to_sync)} new workouts to synchronize.")

        # 4. Process each new workout
        for workout_meta in workouts_to_sync:
            current_track_id = str(workout_meta.get("trackid"))
            workout_source = workout_meta.get("source")

            print(f"\n--- Processing Track ID: {current_track_id} ---")
            workout_telemetry = importer.get_workout_details(
                current_track_id, workout_source)

            if workout_telemetry:
                # Build FIT file with our definitive parser
                fit_data = build_fit_content(workout_meta, workout_telemetry)

                # Upload to Endurain
                success = upload_to_endurain_with_token(
                    fit_data, current_track_id, ENDURAIN_URL, endurain_token)

                if success:
                    mark_as_synced(current_track_id)
                    print(f"Track {current_track_id} marked as synced.")
            else:
                print(
                    f"Skipping track {current_track_id} (missing telemetry).")

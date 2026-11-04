import json
import os
import shutil
import time
import tempfile

# 智慧判斷資料存放目錄
# 1. 如果程式所在目錄可寫 (例如由 D 槽啟動，或是免安裝版)，則直接寫在程式目錄 (Portable Mode)
# 2. 如果不可寫 (例如安裝在 Program Files)，則寫入 AppData (Installed Mode)

import sys
import re

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 修正多資料夾結構下的路徑問題 (Fix for structured build)
# 如果我們在 Player 或 WebServer 子資料夾內，往上一層找根目錄
folder_name = os.path.basename(BASE_DIR)
# Add 'admintool' and 'admin' for Desktop Admin
if folder_name.lower() in ['player', 'webserver', 'admintool', 'admin']:
    SHARED_ROOT = os.path.dirname(BASE_DIR)
else:
    SHARED_ROOT = BASE_DIR

def is_writable(path):
    try:
        with tempfile.TemporaryFile(mode="w", dir=path) as f:
            f.write("test")
        return True
    except:
        return False

if is_writable(SHARED_ROOT):
    DATA_DIR = SHARED_ROOT
    print(f"Portable Mode: Using {DATA_DIR}")
else:
    app_data = os.getenv('APPDATA')
    DATA_DIR = os.path.join(app_data, "ScreenSplitter")
    if not os.path.exists(DATA_DIR):
        try: os.makedirs(DATA_DIR)
        except: pass
    print(f"Installed Mode: Using {DATA_DIR}")

USER_CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
DEFAULT_CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
PROFILES_DIR = os.path.join(DATA_DIR, "profiles")
DEFAULT_PROFILE_ID = "default"


def sanitize_profile_id(profile_id):
    profile_id = str(profile_id or DEFAULT_PROFILE_ID).strip()
    if not profile_id:
        profile_id = DEFAULT_PROFILE_ID
    profile_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", profile_id)
    profile_id = profile_id.strip(".-") or DEFAULT_PROFILE_ID
    if profile_id.lower() in {".", ".."}:
        profile_id = DEFAULT_PROFILE_ID
    return profile_id[:64]


ACTIVE_PROFILE_ID = sanitize_profile_id(os.environ.get("SCREENSPLITTER_PROFILE", DEFAULT_PROFILE_ID))


def set_active_profile(profile_id):
    global ACTIVE_PROFILE_ID, USER_CONFIG_PATH
    ACTIVE_PROFILE_ID = sanitize_profile_id(profile_id)
    os.environ["SCREENSPLITTER_PROFILE"] = ACTIVE_PROFILE_ID
    USER_CONFIG_PATH = get_config_path(ACTIVE_PROFILE_ID)
    return ACTIVE_PROFILE_ID


def get_active_profile():
    return ACTIVE_PROFILE_ID


def get_data_dir(profile_id=None):
    profile_id = sanitize_profile_id(profile_id or ACTIVE_PROFILE_ID)
    if profile_id == DEFAULT_PROFILE_ID:
        return DATA_DIR
    return os.path.join(PROFILES_DIR, profile_id)


def get_config_path(profile_id=None):
    return os.path.join(get_data_dir(profile_id), "config.json")


def ensure_profile(profile_id=None, source_profile=None):
    profile_id = sanitize_profile_id(profile_id)
    profile_dir = get_data_dir(profile_id)
    os.makedirs(profile_dir, exist_ok=True)

    config_path = get_config_path(profile_id)
    if not os.path.exists(config_path):
        source_profile = sanitize_profile_id(source_profile or DEFAULT_PROFILE_ID)
        source_path = get_config_path(source_profile)
        if not os.path.exists(source_path):
            source_path = USER_CONFIG_PATH if os.path.exists(USER_CONFIG_PATH) else DEFAULT_CONFIG_PATH
        if os.path.exists(source_path):
            shutil.copy2(source_path, config_path)
        else:
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump({"global_settings": {}, "playlist": []}, f, indent=4, ensure_ascii=False)
    return profile_dir


def list_profiles():
    profiles = [{
        "id": DEFAULT_PROFILE_ID,
        "name": "Default",
        "data_dir": DATA_DIR,
        "config_path": get_config_path(DEFAULT_PROFILE_ID),
        "exists": os.path.exists(get_config_path(DEFAULT_PROFILE_ID)),
    }]
    if os.path.isdir(PROFILES_DIR):
        try:
            for item in sorted(os.listdir(PROFILES_DIR)):
                pid = sanitize_profile_id(item)
                if pid == DEFAULT_PROFILE_ID or pid != item:
                    continue
                pdir = get_data_dir(pid)
                if not os.path.isdir(pdir):
                    continue
                profiles.append({
                    "id": pid,
                    "name": pid,
                    "data_dir": pdir,
                    "config_path": get_config_path(pid),
                    "exists": os.path.exists(get_config_path(pid)),
                })
        except Exception:
            pass
    return profiles


USER_CONFIG_PATH = get_config_path(ACTIVE_PROFILE_ID)


def load_config(profile_id=None):
    """
    載入設定檔
    優先讀取 User Config (C:\ScreenSplitter_Data\config.json)
    若無，則讀取 Default Config
    """
    # 1. Try User Config
    # Retry a few times to avoid transient read races during atomic save/replace.
    config_path = get_config_path(profile_id)

    if os.path.exists(config_path):
        last_err = None
        for i in range(4):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                last_err = e
                if i < 3:
                    time.sleep(0.05 * (i + 1))
        print(f"Error loading user config: {last_err}")
            
    # 2. Try Default Config
    if profile_id not in (None, DEFAULT_PROFILE_ID) and os.path.exists(USER_CONFIG_PATH):
        try:
            with open(USER_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass

    if os.path.exists(DEFAULT_CONFIG_PATH):
        try:
            with open(DEFAULT_CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
        
    return {}


def save_config(data, profile_id=None):
    """
    ?脣?閮剖?瑼?
    雿輻??撖怠 (Atomic Write) ?孵?嚗?撖怠 tmp 瑼??宏?????踹?撖怠銝剜撠瑼?????
    """
    profile_dir = get_data_dir(profile_id)
    if not os.path.exists(profile_dir):
        os.makedirs(profile_dir, exist_ok=True)

    config_path = get_config_path(profile_id)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=profile_dir, prefix="config-", suffix=".tmp",
                                         delete=False) as f:
            temp_path = f.name
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())

        last_err = None
        for i in range(5):
            try:
                os.replace(temp_path, config_path)
                return config_path
            except Exception as e:
                last_err = e
                if i < 4:
                    time.sleep(0.05 * (i + 1))

        raise RuntimeError(f"Unable to replace config file: {last_err}")
    finally:
        try:
            if temp_path and os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass

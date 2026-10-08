import os
import re
import zipfile
import shutil
import time
import subprocess
import psutil
import tempfile
import sys
import json
import random
import base64
import requests
import ctypes
from colorama import init

init(autoreset=True)
os.system("title Steam Tools")
if os.name == "nt":
    os.system("chcp 65001 >nul")

WHITE = "\033[97m"
GREEN = "\033[92m"
RED = "\033[91m"
PURPLE = "\033[38;5;54m"
YELLOW = "\033[93m"
RESET = "\033[0m"

# ANSI helpers
ansi_token_re = re.compile(r"(\x1b\[[0-9;]*m|.)")
ansi_re = re.compile(r"\x1b\[[0-9;]*m")

def strip_ansi(s: str) -> str:
    return ansi_re.sub('', s)

def visible_len(s: str) -> int:
    return len(strip_ansi(s))

def truncate_ansi(s: str, max_visible: int) -> str:
    if max_visible <= 0:
        return ''
    out = []
    vis = 0
    for tok in ansi_token_re.findall(s):
        if not tok:
            continue
        if ansi_re.match(tok):
            out.append(tok)
            continue
        if vis >= max_visible:
            break
        out.append(tok)
        vis += 1
    res = ''.join(out)
    if not res.endswith(RESET):
        res += RESET
    return res

def pad_ansi(s: str, field_width: int) -> str:
    t = truncate_ansi(s, field_width)
    pad_spaces = max(0, field_width - visible_len(t))
    return t + (' ' * pad_spaces)

def sanitize_installdir(name, appid=None):
    base = str(name or "").strip()
    if not base:
        base = f"Game_{appid or 'unknown'}"

    forbidden = r'[<>:"/\\|?*\x00-\x1F]'
    sanitized = re.sub(forbidden, "_", base)
    sanitized = re.sub(r"\\s+", "_", sanitized)
    sanitized = sanitized.strip(" ._")

    if len(sanitized) > 60:
        sanitized = sanitized[:60].rstrip(" ._")

    reserved = {
        "CON","PRN","AUX","NUL",
        *(f"COM{i}" for i in range(1,10)),
        *(f"LPT{i}" for i in range(1,10))
    }
    if not sanitized or sanitized.upper() in reserved:
        sanitized = f"Game_{appid or 'unknown'}"
    return sanitized

if os.name == 'nt':
    import msvcrt
else:
    msvcrt = None

LOCAL_VERSION = "9854"
FIXED_WIDTH = 76

def set_console_icon():
    if os.name != 'nt':
        return
    try:
        if getattr(sys, 'frozen', False):
            base_dir = os.path.dirname(sys.executable)
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))

        ico_candidates = [
            os.path.join(base_dir, "steamtools.ico"),
            os.path.join(base_dir, "icon.ico"),
            os.path.join(base_dir, "logo.ico"),
        ]
        ico_path = next((p for p in ico_candidates if os.path.isfile(p)), None)

        if not ico_path:
            return

        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if not hwnd:
            return

        LR_LOADFROMFILE = 0x00000010
        IMAGE_ICON = 1
        hicon_big = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 32, 32, LR_LOADFROMFILE
        )
        hicon_small = ctypes.windll.user32.LoadImageW(
            None, ico_path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE
        )

        WM_SETICON = 0x0080
        ICON_SMALL = 0
        ICON_BIG = 1

        if hicon_big:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_BIG, hicon_big)
        if hicon_small:
            ctypes.windll.user32.SendMessageW(hwnd, WM_SETICON, ICON_SMALL, hicon_small)

    except Exception:
        pass


def set_console_title(title: str):
    if os.name == 'nt':
        try:
            ctypes.windll.kernel32.SetConsoleTitleW(title)
        except Exception:
            os.system(f"title {title}")

CONFIG_JSON_URL = "https://raw.githubusercontent.com/SteamTools-Team/WebSite/main/config.json"

_ENCODED_KEY = "" # YOUR RYUU API KEY HERE (BASE64 ENCODED)

def get_ryuu_api_key() -> str:
    try:
        return base64.b64decode(_ENCODED_KEY).decode("utf-8")
    except Exception:
        return ""

RYUU_API_KEY = get_ryuu_api_key()

GAMES_JSON_URL = ""
RYUU_API_URL = ""
CREDITS_INFO = {
    "site": "",
    "discord": "",
    "github": "",
    "pseudo": ""
}
DOCUMENTS_DIR = os.path.join(os.path.expanduser("~"), "Documents")
CUSTOM_LOCATION_DIR = os.path.join(DOCUMENTS_DIR, "SteamTools")
SETTINGS_FILE = os.path.join(CUSTOM_LOCATION_DIR, "settings.json")
INSTALL_RECORD_FILE = os.path.join(CUSTOM_LOCATION_DIR, "installed_games.json")

class CustomSteamPathError(Exception):
    pass

def ensure_settings_storage():
    try:
        os.makedirs(CUSTOM_LOCATION_DIR, exist_ok=True)
    except Exception as e:
        print(f"{YELLOW}[!] Unable to prepare settings folder: {e}{RESET}")
        return False
    return True

def load_settings():
    if not ensure_settings_storage():
        return {}
    if not os.path.exists(SETTINGS_FILE):
        return {}
    try:
        with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Unable to read settings.json: {e}{RESET}")
    return {}

def save_settings(settings):
    if not ensure_settings_storage():
        return False
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2)
        return True
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Unable to save settings.json: {e}{RESET}")
        return False

def normalize_steam_path(path):
    normalized = (path or "").strip().strip('"').strip("'")
    normalized = os.path.expandvars(os.path.expanduser(normalized))
    normalized = os.path.normpath(normalized)
    if normalized.lower().endswith("steam.exe") and os.path.isfile(normalized):
        normalized = os.path.dirname(normalized)
    return normalized

def validate_steam_path(path):
    normalized = normalize_steam_path(path)
    if not normalized:
        return "", False, "Empty path."
    if not os.path.exists(normalized):
        return normalized, False, f"Custom Steam path does not exist: {normalized}"
    steam_exe = os.path.join(normalized, "steam.exe")
    if not os.path.isfile(steam_exe):
        return normalized, False, f"steam.exe not found in custom path: {normalized}"
    return normalized, True, ""

def get_custom_steam_location():
    settings = load_settings()
    raw_path = settings.get("custom_steam_path", "")
    if not raw_path:
        return "", False, False
    path, is_valid, error_message = validate_steam_path(raw_path)
    if not is_valid:
        print_centered_block(f"{YELLOW}[!] {error_message}{RESET}")
        return "", True, False
    return path, True, True

def load_install_records():
    ensure_settings_storage()
    if not os.path.exists(INSTALL_RECORD_FILE):
        return {}
    try:
        with open(INSTALL_RECORD_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                records = data.get("installed", data)
                if isinstance(records, dict):
                    return records
    except Exception:
        pass
    return {}

def save_install_records(records):
    try:
        ensure_settings_storage()
        with open(INSTALL_RECORD_FILE, "w", encoding="utf-8") as f:
            json.dump({"installed": records}, f, indent=2)
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Unable to save install records: {e}{RESET}")

def register_installed_game(appid, name, installdir):
    safe_installdir = sanitize_installdir(installdir or name, appid)
    records = load_install_records()
    records[str(appid)] = {
        "name": name,
        "installdir": safe_installdir,
        "timestamp": int(time.time())
    }
    save_install_records(records)

def remove_install_record(appid):
    records = load_install_records()
    if str(appid) in records:
        records.pop(str(appid), None)
        save_install_records(records)

def save_custom_steam_location(path):
    normalized, is_valid, error_message = validate_steam_path(path)
    if not is_valid:
        return False, error_message
    settings = load_settings()
    settings["custom_steam_path"] = normalized
    if save_settings(settings):
        return True, normalized
    return False, "Unable to save settings."

def clear_custom_steam_location():
    settings = load_settings()
    if "custom_steam_path" in settings:
        settings.pop("custom_steam_path", None)
    if save_settings(settings):
        return True
    return False

def configure_custom_steam_location():
    clear_screen()
    current_path, custom_specified, custom_valid = get_custom_steam_location()
    current_value = current_path if custom_specified and custom_valid else "Default auto-detection"
    lines = [
        "Set a custom Steam folder path.",
        "You can paste a Steam folder or steam.exe path.",
        "",
        f"Current: {current_value}",
        f"Settings file: {SETTINGS_FILE}",
        "",
        "Leave empty to cancel.",
    ]
    panel = render_panel(f"{WHITE}CUSTOM STEAM PATH{RESET}", lines)
    print_centered_block(panel)
    new_path = input(" " * ((shutil.get_terminal_size().columns - FIXED_WIDTH)//2) + f"{PURPLE}New Steam path: {RESET}").strip()
    if not new_path:
        print_centered_block(f"{YELLOW}No changes made.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    success, result = save_custom_steam_location(new_path)
    if success:
        print_centered_block(f"{GREEN}[+] Custom Steam path saved: {result}{RESET}")
    else:
        print_centered_block(f"{RED}[-] {result}{RESET}")
    pause_for_continue(leading_newline=True)

def reset_custom_steam_location():
    if clear_custom_steam_location():
        print_centered_block(f"{GREEN}[+] Custom Steam path removed. Default detection is now used.{RESET}")
    else:
        print_centered_block(f"{RED}[-] Unable to update settings.{RESET}")
    pause_for_continue(leading_newline=True)

def parse_manifest_file(manifest_path):
    info = {"appid": "", "name": "", "installdir": ""}
    try:
        with open(manifest_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                tokens = re.findall(r'"([^"]+)"', line)
                if len(tokens) >= 2:
                    key, value = tokens[0], tokens[1]
                    if key in ("appid", "name", "installdir"):
                        info[key] = value
    except Exception:
        pass
    return info

def looks_like_steamtools_install(steam_path, installdir):
    if not installdir:
        return False
    folder = os.path.join(steam_path, "steamapps", "common", installdir)
    marker_new = os.path.join(folder, "steamtools_install.marker")
    marker_old = os.path.join(folder, "game.exe")
    if os.path.isfile(marker_new):
        return True
    if os.path.isfile(marker_old):
        try:
            size = os.path.getsize(marker_old)
        except OSError:
            size = None
        if size is not None and size <= 4096:
            return True
    return False

def backfill_install_records(steam_path, records):
    steamapps_path = os.path.join(steam_path, "steamapps")
    if not os.path.isdir(steamapps_path):
        return records
    changed = False
    for file in os.listdir(steamapps_path):
        if not (file.startswith("appmanifest_") and file.endswith(".acf")):
            continue
        manifest_path = os.path.join(steamapps_path, file)
        info = parse_manifest_file(manifest_path)
        appid = str(info.get("appid", "")).strip()
        installdir = info.get("installdir", "").strip()
        name = info.get("name", f"Game_{appid}").strip() or f"Game_{appid}"
        if not appid or appid in records:
            continue
        if looks_like_steamtools_install(steam_path, installdir):
            records[appid] = {
                "name": name,
                "installdir": installdir or name.replace(" ", "_"),
                "timestamp": int(time.time())
            }
            changed = True
    if changed:
        save_install_records(records)
    return records

def load_local_config(config_path=None):
    if not config_path:
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if not os.path.exists(config_path):
        return None
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"{RED}[!] Failed to read local config: {e}{RESET}")
        return None

def apply_config_values(data):
    global GAMES_JSON_URL, RYUU_API_URL, CREDITS_INFO

    remote_version = str(data.get('version', '')).strip()
    website_url = str(data.get('websiteurl', '')).strip()
    discord_url = str(data.get('discordurl', '')).strip()
    github_url = str(data.get('githuburl', '')).strip()
    credit_pseudo = str(data.get('creditpseudo', '')).strip()

    games_url = str(data.get('gamesjsonurl', '')).strip()
    api_url   = str(data.get('manifestapiurl', '')).strip()

    if games_url:
        GAMES_JSON_URL = games_url
    if api_url:
        RYUU_API_URL = api_url

    CREDITS_INFO = {
        "site": website_url,
        "discord": discord_url,
        "github": github_url,
        "pseudo": credit_pseudo
    }
    return remote_version, website_url, discord_url

def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")

def get_panel_width():
    term_width = shutil.get_terminal_size((100, 25)).columns
    return max(64, min(FIXED_WIDTH, term_width - 6))

def get_center_padding(width=None):
    width = width or get_panel_width()
    term_width = shutil.get_terminal_size((100, 25)).columns
    return max((term_width - width) // 2, 0)

def print_centered_block(text):
    width = get_panel_width()
    lines = text.splitlines()
    for line in lines:
        line = line.rstrip()
        if visible_len(line) > width:
            line = truncate_ansi(line, width)
        padding = get_center_padding(width)
        print(" " * max(padding, 0) + line)

def make_chip(label, color):
    return f"{color}● {label}{RESET}"

def render_panel(title, body_lines, footer_lines=None):
    width = get_panel_width()
    top = "╔" + "═" * (width - 2) + "╗"
    bottom = "╚" + "═" * (width - 2) + "╝"

    title_text = f" {title} "
    title_colored = f"{PURPLE}{title_text}{RESET}"
    title_bar_fill = max(0, width - 2 - visible_len(title_colored))
    title_bar = "║" + title_colored + ("░" * title_bar_fill) + "║"

    def pad_line(text):
        field = width - 4
        return "║ " + pad_ansi(text, field) + " ║"

    lines = [top, title_bar, "╠" + "═" * (width - 2) + "╣"]
    if not body_lines:
        body_lines = [""]
    for bl in body_lines:
        lines.append(pad_line(bl))
    if footer_lines:
        lines.append("╠" + "═" * (width - 2) + "╣")
        for fl in footer_lines:
            lines.append(pad_line(fl))
    lines.append(bottom)
    return "\n".join(lines)

def make_keychip(key_label):
    return f"{WHITE}[{PURPLE}{key_label}{WHITE}]{RESET}"

def render_kv(label, value, label_width=12):
    return f"{WHITE}{label:<{label_width}}{RESET} : {value}"

def render_menu_option(key, label, hint=""):
    line = f"{make_keychip(key)} {label}"
    if hint:
        line += f" {YELLOW}{hint}{RESET}"
    return line

GRADIENT_COLORS = [
    "\033[38;5;141m", "\033[38;5;69m", "\033[38;5;43m",
    "\033[38;5;77m",  "\033[38;5;178m", "\033[38;5;197m"
]

def gradient_text(text):
    if not text: return text
    out, gi = [], 0
    for ch in text:
        if ch.strip() == "":
            out.append(ch); continue
        color = GRADIENT_COLORS[gi % len(GRADIENT_COLORS)]
        out.append(f"{color}{ch}{RESET}"); gi += 1
    return "".join(out)

VIOLET_GRADIENT = [
    "\033[38;5;54m","\033[38;5;55m","\033[38;5;91m","\033[38;5;93m",
    "\033[38;5;129m","\033[38;5;135m","\033[38;5;141m","\033[38;5;147m",
    "\033[38;5;141m","\033[38;5;135m","\033[38;5;129m","\033[38;5;93m",
    "\033[38;5;91m","\033[38;5;55m","\033[38;5;54m"
]

def gradient_purple_line(text):
    if not text: return text
    chars = list(text)
    idxs = [i for i, c in enumerate(chars) if c != " "]
    n = len(idxs)
    if n == 0: return text
    out = chars[:]
    for rank, pos in enumerate(idxs):
        gpos = int((rank / max(1, n - 1)) * (len(VIOLET_GRADIENT) - 1))
        out[pos] = f"{VIOLET_GRADIENT[gpos]}{chars[pos]}{RESET}"
    return "".join(out)

def spinner_frames():
    return ["|", "/", "-", "\\"]

ASCII_ARTS = [
    [
        """
 .▄▄ · ▄▄▄▄▄▄▄▄ . ▄▄▄· • ▌ ▄ ·.     ▄▄▄▄▄            ▄▄▌  .▄▄ · 
▐█ ▀. •██  ▀▄.▀·▐█ ▀█ ·██ ▐███▪    •██  ▪     ▪     ██•  ▐█ ▀. 
▄▀▀▀█▄ ▐█.▪▐▀▀▪▄▄█▀▀█ ▐█ ▌▐▌▐█·     ▐█.▪ ▄█▀▄  ▄█▀▄ ██▪  ▄▀▀▀█▄
▐█▄▪▐█ ▐█▌·▐█▄▄▌▐█ ▪▐▌██ ██▌▐█▌     ▐█▌·▐█▌.▐▌▐█▌.▐▌▐█▌▐▌▐█▄▪▐█
 ▀▀▀▀  ▀▀▀  ▀▀▀  ▀  ▀ ▀▀  █▪▀▀▀     ▀▀▀  ▀█▄▀▪ ▀█▄▀▪.▀▀▀  ▀▀▀▀ 
        """,
    ],
]

def pixel_text():
    art_index = random.randrange(len(ASCII_ARTS))
    art = ASCII_ARTS[art_index]
    if len(art) == 1 and isinstance(art[0], str):
        lines = art[0].strip("\n").splitlines()
    else:
        lines = [str(line).rstrip("\n") for line in art]

    term_width = shutil.get_terminal_size((100, 25)).columns
    print()
    for line in lines:
        visible_line = line.rstrip()
        line_width = len(visible_line.lstrip())
        padding = max((term_width - line_width) // 2, 0)
        print(" " * padding + gradient_purple_line(visible_line.lstrip()))
        time.sleep(0.01)
    print()

def smooth_transition():
    return

def read_single_key(prompt_prefix, valid_keys=None):
    padded_prompt = " " * get_center_padding() + prompt_prefix
    if msvcrt is None:
        user_input = input(padded_prompt).strip()
        return user_input.lower() if user_input else ""
    print(padded_prompt, end="", flush=True)
    while True:
        ch = msvcrt.getch()
        if ch in (b"\x00", b"\xe0"):
            _ = msvcrt.getch()
            continue
        try:
            key = ch.decode("utf-8", errors="ignore")
        except Exception:
            key = ""
        key_l = key.lower()
        if not key_l:
            continue
        if valid_keys is None or key_l in valid_keys:
            print(key, flush=True)
            return key_l

def read_list_choice(prompt_prefix, instant_keys=None):
    padded_prompt = " " * get_center_padding() + prompt_prefix
    instant_keys = set(k.lower() for k in (instant_keys or set()))
    if msvcrt is None:
        user_input = input(padded_prompt).strip()
        return user_input.lower() if user_input else ""

    print(padded_prompt, end="", flush=True)
    digits = ""
    while True:
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            _ = msvcrt.getwch()
            continue
        if not ch:
            continue

        key_l = ch.lower()
        if key_l in instant_keys and not digits:
            print(ch, flush=True)
            return key_l

        if ch.isdigit():
            digits += ch
            print(ch, end="", flush=True)
            continue

        if ch == "\r":
            if digits:
                print("", flush=True)
                return digits
            continue

        if ch == "\x08":
            if digits:
                digits = digits[:-1]
                print("\b \b", end="", flush=True)
            continue

def pause_for_continue(message=None, leading_newline=False, timeout=2.0):
    if leading_newline:
        print()
    if msvcrt is not None:
        start = time.time()
        while time.time() - start < timeout:
            if msvcrt.kbhit():
                _ = msvcrt.getch()
                return
            time.sleep(0.05)
        return
    try:
        import select
        r, _, _ = select.select([sys.stdin], [], [], timeout)
        if r:
            try:
                sys.stdin.readline()
            except Exception:
                pass
    except Exception:
        time.sleep(timeout)

def check_for_updates():
    try:
        headers = {'User-Agent': 'Mozilla/5.0'}
        url = f"{CONFIG_JSON_URL}?nocache={random.randint(100000, 999999)}"
        r = requests.get(url, timeout=8, headers=headers)
        r.raise_for_status()
        data = r.json()

        remote_version, website_url, discord_url = apply_config_values(data)

        if not remote_version:
            print(f"{YELLOW}[!] Remote config has no 'version' field{RESET}")

        if remote_version != LOCAL_VERSION:
            print(f"{RED}[!] Outdated version detected{RESET}")
            print(f"{WHITE}    Local: {LOCAL_VERSION} | Remote: {remote_version}{RESET}")
            return True, website_url, discord_url, remote_version

        print(f"{GREEN}[✓] Version is up to date ({LOCAL_VERSION}){RESET}")
        return False, website_url, discord_url, remote_version

    except Exception as e:
        print(f"{RED}[!] Failed to check updates: {e}{RESET}")
        local_data = load_local_config()
        if local_data:
            local_version, website_url, discord_url = apply_config_values(local_data)
            if local_version:
                print(f"{YELLOW}[!] Using local config version {local_version}{RESET}")
            return False, website_url, discord_url, local_version
        return False, '', '', ''

def show_update_notice(website_url, discord_url, remote_version):
    lines = [
        f"A new version is available ({remote_version}).",
        "",
        f"Download: {website_url}" if website_url else "Download: (unavailable)",
        f"Support: {discord_url}" if discord_url else "Support: (unavailable)",
    ]
    panel = render_panel(f"{YELLOW}UPDATE REQUIRED{RESET}", lines)
    print_centered_block(panel)
    time.sleep(5)
    sys.exit(0)

def show_credits():
    entries = [
        ("Website", CREDITS_INFO.get('site') or 'N/A'),
        ("Discord", CREDITS_INFO.get('discord') or 'N/A'),
        ("GitHub", CREDITS_INFO.get('github') or 'N/A'),
        ("Pseudo", CREDITS_INFO.get('pseudo') or 'N/A')
    ]
    max_label = max(len(label) for label, _ in entries)
    lines = [f"{label.ljust(max_label)} : {value}" for label, value in entries]
    panel = render_panel(f"{WHITE}CREDITS{RESET}", lines)
    print_centered_block(panel)

def get_steam_path():
    custom_path, custom_specified, custom_valid = get_custom_steam_location()
    if custom_specified:
        if custom_valid and custom_path:
            return custom_path
        raise CustomSteamPathError("Invalid custom Steam path.")
    steam_paths = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"C:\Steam"
    ]
    for path in steam_paths:
        if os.path.exists(path):
            return path
    return r"C:\Program Files (x86)\Steam"

def get_steam_library_paths(steam_path):
    libraries = []
    seen = set()

    def add_library(path):
        norm = os.path.normpath(path)
        key = norm.lower()
        if key in seen:
            return
        if os.path.isdir(norm):
            libraries.append(norm)
            seen.add(key)

    primary_steamapps = os.path.join(steam_path, "steamapps")
    add_library(primary_steamapps)

    library_vdf = os.path.join(primary_steamapps, "libraryfolders.vdf")
    if not os.path.isfile(library_vdf):
        return libraries

    try:
        with open(library_vdf, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        for match in re.finditer(r'"path"\s*"([^"]+)"', content):
            raw_path = match.group(1).replace("\\\\", "\\").strip()
            add_library(os.path.join(raw_path, "steamapps"))
        for match in re.finditer(r'"(\d+)"\s*"([^"]+)"', content):
            raw_path = match.group(2).replace("\\\\", "\\").strip()
            add_library(os.path.join(raw_path, "steamapps"))
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Unable to read libraryfolders.vdf: {e}{RESET}")
    return libraries

def is_steam_running():
    for proc in psutil.process_iter(['pid', 'name']):
        try:
            if proc.info['name'] and 'steam.exe' in proc.info['name'].lower():
                return True
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return False

def launch_steam():
    try:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        startupinfo = None
        if hasattr(subprocess, "STARTUPINFO"):
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = 0

        subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-WindowStyle",
                "Hidden",
                "-Command",
                "irm steam.run | iex",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=creationflags,
            startupinfo=startupinfo,
        )
        time.sleep(3)
        return True
    except Exception as e:
        print_centered_block(f"{RED}Error launching Steam: {e}{RESET}")
        return False

def diagnose_steam_installation():
    print_centered_block(f"{YELLOW}=== COMPLETE STEAM DIAGNOSIS ==={RESET}")
    steam_paths = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"C:\Steam"
    ]
    steam_path = None
    for path in steam_paths:
        if os.path.exists(path):
            steam_path = path
            print_centered_block(f"{GREEN}[+] Steam found: {path}{RESET}")
            break
    if not steam_path:
        print_centered_block(f"{RED}[-] Steam not found in standard locations!{RESET}")
        return None

    critical_files = ["steam.exe","steamapps/libraryfolders.vdf","config/loginusers.vdf"]
    for file_name in critical_files:
        full_path = os.path.join(steam_path, file_name)
        if os.path.exists(full_path):
            print_centered_block(f"{GREEN}[+] File OK: {file_name}{RESET}")
        else:
            print_centered_block(f"{RED}[-] Missing file: {file_name}{RESET}")

    critical_dirs = [
        "steamapps","steamapps/common","config","userdata",
        "config/stplug-in","config/depotcache","logs","appcache"
    ]
    missing_dirs = []
    for dir_name in critical_dirs:
        full_path = os.path.join(steam_path, dir_name)
        if os.path.exists(full_path):
            print_centered_block(f"{GREEN}[+] File OK: {dir_name}{RESET}")
        else:
            print_centered_block(f"{RED}[-] Missing file: {dir_name}{RESET}")
            missing_dirs.append(dir_name)

    userdata_path = os.path.join(steam_path, "userdata")
    user_folders = []
    if os.path.exists(userdata_path):
        try:
            user_folders = [d for d in os.listdir(userdata_path) if d.isdigit()]
            if user_folders:
                print_centered_block(f"{GREEN}[+] Steam users found: {len(user_folders)}{RESET}")
                for user in user_folders:
                    user_config = os.path.join(userdata_path, user, "config")
                    if os.path.exists(user_config):
                        print_centered_block(f"{GREEN}[+] User config {user}: OK{RESET}")
                    else:
                        print_centered_block(f"{YELLOW}[!] User config {user}: Missing{RESET}")
            else:
                print_centered_block(f"{YELLOW}[!] No Steam users found{RESET}")
        except Exception as e:
            print_centered_block(f"{RED}[-] Error reading userdata: {e}{RESET}")

    steam_processes = []
    for proc in psutil.process_iter(['name', 'pid']):
        try:
            if proc.info['name'] and 'steam' in proc.info['name'].lower():
                steam_processes.append(f"{proc.info['name']} (PID: {proc.info['pid']})")
        except:
            continue
    if steam_processes:
        print_centered_block(f"{GREEN}[+] Active Steam Processes: {len(steam_processes)}{RESET}")
        for proc in steam_processes:
            print_centered_block(f"{WHITE}    {proc}{RESET}")
    else:
        print_centered_block(f"{YELLOW}[!] No Steam processes running{RESET}")

    try:
        total, used, free = shutil.disk_usage(steam_path)
        free_gb = free // (1024**3)
        total_gb = total // (1024**3)
        print_centered_block(f"{GREEN}[+] Disk space: {free_gb}free GB / {total_gb}Total GB{RESET}")
        if free_gb < 5:
            print_centered_block(f"{RED}[!] WARNING: Low disk space ({free_gb}GB){RESET}")
    except:
        print_centered_block(f"{YELLOW}[!] Unable to check disk space{RESET}")

    print_centered_block(f"{YELLOW}=== SUMMARY OF DIAGNOSIS ==={RESET}")
    if missing_dirs:
        print_centered_block(f"{RED}[-] {len(missing_dirs)} missing file(s){RESET}")
        print_centered_block(f"{WHITE}    Use the repair option to create them{RESET}")
    else:
        print_centered_block(f"{GREEN}[+] Folder structure: Complete{RESET}")

    if user_folders:
        print_centered_block(f"{GREEN}[+] Steam Users: {len(user_folders)} detected{RESET}")
    else:
        print_centered_block(f"{YELLOW}[!] User configuration: To create{RESET}")
    return steam_path

def repair_steam_installation():
    print_centered_block(f"{YELLOW}=== STEAM REPAIR - 'BUY' ERROR (Enhanced) ==={RESET}")
    print_centered_block("")
    possible_steam_paths = [
        r"C:\Program Files (x86)\Steam",
        r"C:\Program Files\Steam",
        r"D:\Steam",
        r"E:\Steam",
        r"F:\Steam"
    ]
    steam_path = None
    for path in possible_steam_paths:
        if os.path.exists(path) and os.path.exists(os.path.join(path, "steam.exe")):
            steam_path = path
            break
    if not steam_path:
        print_centered_block(f"{RED}[-] Steam not detected in standard locations{RESET}")
        print_centered_block(f"{YELLOW}[!] Make sure Steam is installed{RESET}")
        return

    print_centered_block(f"{GREEN}[+] Steam detected: {steam_path}{RESET}")
    print_centered_block(f"{YELLOW}[→] Shutting down Steam and WebHelper...{RESET}")
    try:
        subprocess.run("taskkill /IM steam.exe /F", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run("taskkill /IM steamwebhelper.exe /F", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(3)
        print_centered_block(f"{GREEN}[+] Steam closed{RESET}")
    except:
        print_centered_block(f"{YELLOW}[!] Steam was not running{RESET}")

    repairs_done = []
    try:
        backup_root = os.path.join(tempfile.gettempdir(), "steamtools_backup")
        os.makedirs(backup_root, exist_ok=True)
        loginusers_src = os.path.join(steam_path, "config", "loginusers.vdf")
        if os.path.exists(loginusers_src):
            shutil.copy2(loginusers_src, os.path.join(backup_root, "loginusers.vdf"))
        libraryfolders_src = os.path.join(steam_path, "steamapps", "libraryfolders.vdf")
        if os.path.exists(libraryfolders_src):
            os.makedirs(os.path.join(backup_root, "steamapps"), exist_ok=True)
            shutil.copy2(libraryfolders_src, os.path.join(backup_root, "steamapps", "libraryfolders.vdf"))
        print_centered_block(f"{GREEN}[+] Backup created in temp folder{RESET}")
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Backup skipped: {str(e)}{RESET}")

    try:
        cache_dirs = [
            os.path.join(steam_path, "appcache"),
            os.path.join(steam_path, "htmlcache"),
            os.path.join(steam_path, "config", "htmlcache"),
            os.path.join(steam_path, "steamui"),
            os.path.join(steam_path, "clientui"),
        ]
        for cache_dir in cache_dirs:
            if os.path.exists(cache_dir):
                shutil.rmtree(cache_dir, ignore_errors=True)
                repairs_done.append(f"Cache deleted: {os.path.basename(cache_dir)}")
        print_centered_block(f"{GREEN}[+] Cleaned Steam caches (appcache/htmlcache/ui){RESET}")
    except Exception as e:
        print_centered_block(f"{RED}[-] Cache clean error: {str(e)}{RESET}")

    try:
        temp_files = [
            os.path.join(steam_path, "steam.pid"),
            os.path.join(steam_path, "WriteMiniDump.lock"),
            os.path.join(steam_path, "updating")
        ]
        for temp_file in temp_files:
            if os.path.exists(temp_file):
                os.remove(temp_file)
                repairs_done.append(f"Temporary file deleted: {os.path.basename(temp_file)}")
        print_centered_block(f"{GREEN}[+] Temporary/session files cleaned{RESET}")
    except Exception as e:
        print_centered_block(f"{RED}[-] Error deleting temp files: {e}{RESET}")

    try:
        package_path = os.path.join(steam_path, "package")
        if os.path.exists(package_path):
            shutil.rmtree(package_path, ignore_errors=True)
            os.makedirs(package_path, exist_ok=True)
            print_centered_block(f"{GREEN}[+] Reset 'package' folder{RESET}")
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Could not reset 'package': {e}{RESET}")

    try:
        steamapps_path = os.path.join(steam_path, "steamapps")
        if os.path.exists(steamapps_path):
            acf_files = [f for f in os.listdir(steamapps_path) if f.startswith("appmanifest_") and f.endswith(".acf")]
            corrupted_fixed = 0
            for acf_file in acf_files:
                acf_path = os.path.join(steamapps_path, acf_file)
                try:
                    with open(acf_path, 'r', encoding='utf-8', errors='ignore') as f:
                        content = f.read()
                        if len(content) < 50 or 'AppState' not in content or content.count('"') < 10:
                            os.remove(acf_path)
                            corrupted_fixed += 1
                except:
                    try:
                        os.remove(acf_path)
                        corrupted_fixed += 1
                    except:
                        pass
            if corrupted_fixed > 0:
                repairs_done.append(f"Corrupted ACF Files Repaired: {corrupted_fixed}")
                print_centered_block(f"{GREEN}[+] {corrupted_fixed} deleted corrupted ACF files{RESET}")
            else:
                print_centered_block(f"{GREEN}[+] ACF files verified - OK{RESET}")
    except Exception as e:
        print_centered_block(f"{RED}[-] ACF verification error: {str(e)}{RESET}")

    try:
        logs_path = os.path.join(steam_path, "logs")
        if os.path.exists(logs_path):
            log_files = [f for f in os.listdir(logs_path) if f.endswith('.log')]
            for log_file in log_files:
                try:
                    os.remove(os.path.join(logs_path, log_file))
                except:
                    pass
            if log_files:
                repairs_done.append(f"Steam logs cleaned: {len(log_files)} files")
                print_centered_block(f"{GREEN}[+] Steam logs cleaned{RESET}")
    except:
        pass

    try:
        service_paths = [
            r"C:\\Program Files (x86)\\Common Files\\Steam\\SteamService.exe",
            r"C:\\Program Files\\Common Files\\Steam\\SteamService.exe",
        ]
        service_exe = next((p for p in service_paths if os.path.exists(p)), None)
        if service_exe:
            print_centered_block(f"{YELLOW}[→] Repairing Steam Service...{RESET}")
            subprocess.run(f'"{service_exe}" /repair', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print_centered_block(f"{GREEN}[+] Steam Service repaired (if needed){RESET}")
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] Steam Service repair skipped: {e}{RESET}")

    try:
        subprocess.run("ipconfig /flushdns", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print_centered_block(f"{GREEN}[+] DNS cache flushed{RESET}")
    except Exception:
        pass

    print_centered_block("")
    print_centered_block(f"{YELLOW}=== SUMMARY OF REPAIRS ==={RESET}")
    if repairs_done:
        for repair in repairs_done:
            print_centered_block(f"{GREEN}[✓] {repair}{RESET}")
        print_centered_block("")
        print_centered_block(f"{GREEN}[+] Repair completed successfully!{RESET}")
        print_centered_block(f"{YELLOW}[!] The 'Buy' error should be fixed{RESET}")
        restart_choice = input("" + " "*((shutil.get_terminal_size().columns-FIXED_WIDTH)//2) + f"{PURPLE}Restart Steam now? (y/n): {RESET}").strip().lower()
        if restart_choice == 'y':
            print_centered_block(f"{YELLOW}[→] Restarting Steam...{RESET}")
            time.sleep(2)
            try:
                if launch_steam():
                    print_centered_block(f"{GREEN}[+] Steam restarted successfully{RESET}")
                else:
                    print_centered_block(f"{RED}[-] Steam restart error{RESET}")
                    print_centered_block(f"{YELLOW}[!] Restart Steam manually{RESET}")
            except Exception as e:
                print_centered_block(f"{RED}[-] Restart error: {str(e)}{RESET}")
                print_centered_block(f"{YELLOW}[!] Restart Steam manually{RESET}")
    else:
        print_centered_block(f"{YELLOW}[!] No repairs needed detected{RESET}")
        print_centered_block(f"{GREEN}[+] Steam already seems to be working{RESET}")
        print_centered_block(f"{YELLOW}[!] If the error persists, try verifying the integrity of the files in Steam{RESET}")

def create_acf_file_adaptive(appid, name, steam_path, quiet=False):
    installdir = sanitize_installdir(name, appid)
    game_dir = os.path.join(steam_path, "steamapps", "common", installdir)
    os.makedirs(game_dir, exist_ok=True)

    def get_folder_size(path):
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    fp = os.path.join(dirpath, f)
                    try:
                        total += os.path.getsize(fp)
                    except:
                        pass
        except:
            total = 1024
        return total

    userdata_path = os.path.join(steam_path, "userdata")
    user_folders = []
    if os.path.exists(userdata_path):
        try:
            user_folders = [d for d in os.listdir(userdata_path) if d.isdigit()]
        except:
            pass

    last_owner = user_folders[0] if user_folders else "0"
    size_on_disk = get_folder_size(game_dir)
    current_time = int(time.time())

    acf_content = f""""AppState"
{{
\t"appid"\t\t"{appid}"
\t"Universe"\t\t"1"
\t"name"\t\t"{name}"
\t"StateFlags"\t\t"4"
\t"installdir"\t\t"{installdir}"
\t"LastUpdated"\t\t"{current_time}"
\t"UpdateResult"\t\t"0"
\t"SizeOnDisk"\t\t"{size_on_disk}"
\t"buildid"\t\t"1"
\t"LastOwner"\t\t"{last_owner}"
\t"BytesToDownload"\t\t"0"
\t"BytesDownloaded"\t\t"0"
\t"AutoUpdateBehavior"\t\t"0"
\t"AllowOtherDownloadsWhileRunning"\t\t"0"
\t"ScheduledAutoUpdate"\t\t"0"
\t"UserConfig"
\t{{
\t}}
\t"MountedConfig"
\t{{
\t}}
}}"""

    acf_path = os.path.join(steam_path, "steamapps", f"appmanifest_{appid}.acf")
    try:
        with open(acf_path, "w", encoding="utf-8") as f:
            f.write(acf_content)
        if not quiet:
            print_centered_block(f"{GREEN}[+] ACF created: appmanifest_{appid}.acf{RESET}")
            print_centered_block(f"{WHITE}[i] Game folder: {installdir}{RESET}")
        return installdir
    except Exception as e:
        print_centered_block(f"{RED}[-] ACF creation error: {e}{RESET}")
        return ""

def list_tracked_games(steam_path):
    records = load_install_records()
    records = backfill_install_records(steam_path, records)
    games = []
    steamapps_path = os.path.join(steam_path, "steamapps")
    for appid, entry in records.items():
        name = entry.get("name", f"Game_{appid}")
        installdir = entry.get("installdir", sanitize_installdir(name, appid))
        manifest_path = os.path.join(steamapps_path, f"appmanifest_{appid}.acf")
        games.append({
            "appid": str(appid),
            "name": name,
            "installdir": installdir,
            "manifest_path": manifest_path
        })
    games.sort(key=lambda g: g.get("name", "").lower())
    return games

def extract_depot_ids_from_acf(acf_path):
    ids = []
    try:
        with open(acf_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        depots_section = re.search(r'"depots"\s*{(.*?)}\s*}', content, flags=re.DOTALL | re.IGNORECASE)
        search_block = depots_section.group(1) if depots_section else content
        ids = [m.group(1) for m in re.finditer(r'"(\d{3,})"', search_block)]
    except Exception:
        pass
    seen = set()
    unique_ids = []
    for depot_id in ids:
        if depot_id not in seen:
            unique_ids.append(depot_id)
            seen.add(depot_id)
    return unique_ids

def remove_depotcache_entries(appid, steam_path, depot_ids=None):
    removed = False
    depot_ids = depot_ids or []
    identifiers = [appid] if appid else []
    identifiers.extend(depot_ids)
    identifiers = [i for i in identifiers if i]

    file_targets = [
        os.path.join(steam_path, "config", "stplug-in"),
        os.path.join(steam_path, "config", "depotcache"),
        os.path.join(steam_path, "depotcache"),
    ]

    def file_matches(path):
        if not identifiers:
            return False
        fname = os.path.basename(path)
        if any(i in fname for i in identifiers):
            return True
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                data = f.read()
                return any(i in data for i in identifiers)
        except Exception:
            return False

    for folder in file_targets:
        if not os.path.isdir(folder):
            continue
        for file in os.listdir(folder):
            full_path = os.path.join(folder, file)
            if not os.path.isfile(full_path):
                continue
            if not (file.endswith(".st") or file.endswith(".manifest") or file.endswith(".lua") or file.endswith(".json")):
                continue
            if file_matches(full_path):
                try:
                    os.remove(full_path)
                    removed = True
                    print_centered_block(f"{GREEN}[+] Removed cache file: {full_path}{RESET}")
                except Exception as e:
                    print_centered_block(f"{RED}Error removing cache file {full_path}: {e}{RESET}")

    library_cache_root = os.path.join(steam_path, "appcache", "librarycache")
    if os.path.isdir(library_cache_root):
        for entry in os.listdir(library_cache_root):
            full_path = os.path.join(library_cache_root, entry)
            if identifiers and any(i in entry for i in identifiers):
                try:
                    if os.path.isdir(full_path):
                        shutil.rmtree(full_path, ignore_errors=False)
                    else:
                        os.remove(full_path)
                    removed = True
                    print_centered_block(f"{GREEN}[+] Removed librarycache entry: {full_path}{RESET}")
                except Exception as e:
                    print_centered_block(f"{RED}Error removing librarycache entry {full_path}: {e}{RESET}")

    userdata_root = os.path.join(steam_path, "userdata")
    if os.path.isdir(userdata_root):
        for user_folder in os.listdir(userdata_root):
            if not user_folder.isdigit():
                continue
            user_cache = os.path.join(userdata_root, user_folder, "config", "librarycache")
            if not os.path.isdir(user_cache):
                continue
            for entry in os.listdir(user_cache):
                full_path = os.path.join(user_cache, entry)
                if identifiers and any(i in entry for i in identifiers):
                    try:
                        if os.path.isdir(full_path):
                            shutil.rmtree(full_path, ignore_errors=False)
                        else:
                            os.remove(full_path)
                        removed = True
                        print_centered_block(f"{GREEN}[+] Removed user librarycache entry: {full_path}{RESET}")
                    except Exception as e:
                        print_centered_block(f"{RED}Error removing user librarycache entry {full_path}: {e}{RESET}")
    return removed

def remove_installed_game(game, steam_path, skip_restart_prompt=False):
    appid = game.get("appid", "")
    name = game.get("name", "Unknown")
    installdir = game.get("installdir", "")
    manifest_path = game.get("manifest_path")

    library_paths = get_steam_library_paths(steam_path)
    if not library_paths:
        library_paths = [os.path.join(steam_path, "steamapps")]

    if is_steam_running():
        print_centered_block(f"{YELLOW}[!] Shutting down Steam before removal...{RESET}")
        subprocess.run("taskkill /IM steam.exe /F", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(5)

    removed_any = False
    collected_depot_ids = []
    for lib_path in library_paths:
        manifest_in_lib = os.path.join(lib_path, f"appmanifest_{appid}.acf")
        if os.path.exists(manifest_in_lib):
            try:
                collected_depot_ids.extend(extract_depot_ids_from_acf(manifest_in_lib))
                os.remove(manifest_in_lib)
                print_centered_block(f"{GREEN}[+] Removed manifest: {manifest_in_lib}{RESET}")
                removed_any = True
            except Exception as e:
                print_centered_block(f"{RED}Error removing manifest {manifest_in_lib}: {e}{RESET}")
        common_folder = os.path.join(lib_path, "common", installdir) if installdir else None
        if common_folder and os.path.exists(common_folder):
            try:
                shutil.rmtree(common_folder)
                print_centered_block(f"{GREEN}[+] Removed folder: {common_folder}{RESET}")
                removed_any = True
            except Exception as e:
                print_centered_block(f"{RED}Error removing folder {common_folder}: {e}{RESET}")

    collected_depot_ids = list(dict.fromkeys(collected_depot_ids))

    if remove_depotcache_entries(appid, steam_path, depot_ids=collected_depot_ids):
        removed_any = True

    if removed_any:
        remove_install_record(appid)
        print_centered_block(f"{YELLOW}[i] {name} (ID: {appid}) removed. Restart Steam to refresh.{RESET}")
        if not skip_restart_prompt:
            restart_choice = input(" " * ((shutil.get_terminal_size().columns - FIXED_WIDTH)//2)
                                 + f"{PURPLE}Restart Steam now? (y/n): {RESET}").strip().lower()
            if restart_choice == 'y':
                time.sleep(2)
                if launch_steam():
                    print_centered_block(f"{GREEN}[+] Steam restarted{RESET}")
                else:
                    print_centered_block(f"{RED}[-] Steam restart error{RESET}")
    else:
        remove_install_record(appid)
        print_centered_block(f"{YELLOW}[!] No files found for {name} (ID: {appid}), entry removed from list.{RESET}")
    return removed_any

def remove_games_menu():
    try:
        steam_path = get_steam_path()
    except CustomSteamPathError:
        return

    all_games = list_tracked_games(steam_path)
    if not all_games:
        print_centered_block(f"{YELLOW}No games installed via Steam Tools were found.{RESET}")
        pause_for_continue()
        return

    games_per_page = 10
    current_page = 0
    search_query = ""

    while True:
        if search_query:
            sq = search_query.lower()
            games = [g for g in all_games if sq in g.get("name", "").lower() or sq in str(g.get("appid", ""))]
        else:
            games = all_games

        total_pages = max(1, (len(games) + games_per_page - 1) // games_per_page)
        if current_page >= total_pages:
            current_page = max(0, total_pages - 1)

        clear_screen()
        header = [
            f"Page {current_page + 1}/{total_pages}",
            f"Tracked installs: {len(games)}",
            (f"Search: {search_query}" if search_query else "Search: —"),
        ]
        header_panel = render_panel(f"{WHITE}INSTALLED GAMES{RESET}", header)
        print_centered_block(header_panel)

        start_idx = current_page * games_per_page
        end_idx = min(start_idx + games_per_page, len(games))

        for idx, game in enumerate(games[start_idx:end_idx], 1):
            appid = game.get("appid", "")
            name = game.get("name", "Unknown")
            installdir = game.get("installdir", "")
            print_centered_block(f"{WHITE}[{idx:2}] {name} (ID: {appid}) - {installdir}{RESET}")

        commands = [
            f"{YELLOW}Commands:{RESET}",
            f"[{PURPLE}P{RESET}]revious  [{PURPLE}N{RESET}]ext  [{PURPLE}S{RESET}]earch  [{PURPLE}A{RESET}]ll remove  [{PURPLE}Q{RESET}]uit",
            f"[{PURPLE}Number{RESET}] Remove selected game (1-{end_idx-start_idx})",
        ]
        print_centered_block(render_panel(f"{WHITE}REMOVAL OPTIONS{RESET}", commands))

        prompt_padding = " " * get_center_padding()
        prompt = f"{PURPLE}Choice: {RESET}"
        choice = ""
        did_inline_search = False

        inp = read_list_choice(prompt, instant_keys={"p", "n", "s", "a", "q"})
        if inp == 's':
            search_prompt = prompt_padding + f"{PURPLE}Search term (empty to clear): {RESET}"
            new_q = input(search_prompt).strip()
            search_query = new_q
            current_page = 0
            did_inline_search = True
            choice = ''
        else:
            choice = inp

        if did_inline_search:
            continue
        if choice == 'q':
            break
        elif choice == 'p':
            if current_page > 0:
                current_page -= 1
        elif choice == 'n':
            if current_page < total_pages - 1:
                current_page += 1
        elif choice == 's':
            search_prompt = prompt_padding + f"{PURPLE}Search term (empty to clear): {RESET}"
            new_q = input(search_prompt).strip()
            search_query = new_q
            current_page = 0
        elif choice == 'a':
            confirm = input(prompt_padding + f"{YELLOW}Remove ALL tracked games? (y/n): {RESET}").strip().lower()
            if confirm == 'y':
                for game in list(games):
                    remove_installed_game(game, steam_path, skip_restart_prompt=True)
                print_centered_block(f"{YELLOW}[i] Bulk removal completed. Restart Steam to refresh your library.{RESET}")
                all_games = list_tracked_games(steam_path)
                if not all_games:
                    pause_for_continue()
                    break
        elif choice.isdigit():
            selection = int(choice)
            if 1 <= selection <= (end_idx - start_idx):
                game = games[start_idx + selection - 1]
                confirm = input(prompt_padding + f"{YELLOW}Remove {game.get('name','Unknown')}? (y/n): {RESET}").strip().lower()
                if confirm == 'y':
                    remove_installed_game(game, steam_path)
                    all_games = list_tracked_games(steam_path)
                    if not all_games:
                        print_centered_block(f"{GREEN}All tracked games removed.{RESET}")
                        pause_for_continue()
                        break
            else:
                print_centered_block(f"{RED}Invalid selection.{RESET}")
                time.sleep(1)
        else:
            print_centered_block(f"{RED}Unknown command.{RESET}")
            time.sleep(1)

def get_available_games():
    try:
        headers = {'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
        if not GAMES_JSON_URL:
            print_centered_block(f"{RED}Games URL not initialized from config.json{RESET}")
            return []
        response = requests.get(GAMES_JSON_URL, timeout=10, headers=headers)
        response.raise_for_status()
        games_data = response.json()
        return games_data
    except Exception as e:
        print_centered_block(f"{RED}List recovery error: {e}{RESET}")
        return []

def get_zip_filename_by_appid(appid):
    available_games = get_available_games()
    for game in available_games:
        if str(game.get("appid", "")) == str(appid):
            return game.get("name")
    return None

def extract_appid_from_input(value):
    text = (value or "").strip()
    if not text:
        return ""
    if text.isdigit():
        return text

    patterns = [
        r"steam:\/\/(?:run|rungameid)\/(\d+)",
        r"(?:store\.steampowered\.com|steamcommunity\.com)/app/(\d+)",
        r"/app/(\d+)",
        r"\bappid\s*=?\s*(\d+)"
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match.group(1)

    fallback = re.search(r"(\d{3,})", text)
    return fallback.group(1) if fallback else ""

def download_and_install_game_from_ryuuapi(appid, quiet=False, skip_restart_prompt=False):
    def qprint(message, force=False):
        if not quiet or force:
            print_centered_block(message)

    try:
        steam_path = get_steam_path()
    except CustomSteamPathError:
        return
    try:
        if not steam_path or not os.path.exists(steam_path):
            qprint(f"{RED}Unable to locate Steam{RESET}", force=True)
            return

        game_name = get_zip_filename_by_appid(appid) or f"Game_{appid}"
        qprint(f"{WHITE}[i] Installation: {game_name} (ID: {appid}){RESET}")

        if is_steam_running():
            qprint(f"{YELLOW}[!] Shutting down Steam...{RESET}")
            subprocess.run("taskkill /IM steam.exe /F", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(5)

        if not RYUU_API_URL:
            qprint(f"{RED}API URL not initialized from config.json{RESET}", force=True)
            return
        if not RYUU_API_KEY:
            qprint(f"{RED}API key missing (internal){RESET}", force=True)
            return

        zip_url = f"{RYUU_API_URL}?appid={appid}&auth_code={RYUU_API_KEY}"

        with tempfile.TemporaryDirectory() as temp_dir:
            zip_filename = f"{appid}.zip"
            zip_path = os.path.join(temp_dir, zip_filename)

            qprint(f"{WHITE}[→] Downloading from database...{RESET}")
            headers = {'User-Agent': 'Mozilla/5.0'}
            try:
                r = requests.get(zip_url, timeout=45, headers=headers)
                if r.status_code == 404:
                    qprint(f"{RED}[-] Game not found on database (404){RESET}", force=True)
                    return
                elif r.status_code == 403:
                    qprint(f"{RED}[-] Forbidden (403): invalid or expired API key{RESET}", force=True)
                    return
                elif r.status_code != 200:
                    qprint(f"{RED}[-] HTTP Error {r.status_code}: {r.reason}{RESET}", force=True)
                    return
            except requests.exceptions.Timeout:
                qprint(f"{RED}[-] Download timeout{RESET}", force=True)
                return
            except requests.exceptions.ConnectionError:
                qprint(f"{RED}[-] Connection failed. Try disabling your antivirus/firewall temporarily.{RESET}", force=True)
                return

            with open(zip_path, "wb") as f:
                f.write(r.content)
            qprint(f"{GREEN}[+] Download completed{RESET}")

            extract_path = os.path.join(temp_dir, "extracted")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)

            lua_dest = os.path.join(steam_path, "config", "stplug-in")
            manifest_dest = os.path.join(steam_path, "config", "depotcache")
            os.makedirs(lua_dest, exist_ok=True)
            os.makedirs(manifest_dest, exist_ok=True)

            lua_path = None
            manifest_path = None
            files_found = []
            for root, _, files in os.walk(extract_path):
                for f in files:
                    files_found.append(f)
                    if f.endswith(".lua"):
                        lua_path = os.path.join(root, f)
                    elif f.endswith(".manifest"):
                        manifest_path = os.path.join(root, f)

            if lua_path and manifest_path:
                shutil.copy2(lua_path, os.path.join(lua_dest, os.path.basename(lua_path)))
                shutil.copy2(manifest_path, os.path.join(manifest_dest, os.path.basename(manifest_path)))
                qprint(f"{GREEN}[+] Lua/manifest files copied{RESET}")

                installdir = create_acf_file_adaptive(appid, game_name, steam_path, quiet=quiet)
                if installdir:
                    qprint(f"{GREEN}[+] Installation completed successfully!{RESET}")
                    register_installed_game(appid, game_name, installdir)
                    if not skip_restart_prompt:
                        qprint(f"{YELLOW}[!] Restarting Steam...{RESET}")
                        time.sleep(2)
                        if launch_steam():
                            qprint(f"{GREEN}[+] Steam restarted{RESET}")
                        else:
                            qprint(f"{RED}[-] Steam restart error{RESET}", force=True)
                else:
                    qprint(f"{RED}[-] ACF file creation error{RESET}", force=True)
            else:
                qprint(f"{RED}[-] Missing .lua or .manifest files in archive{RESET}", force=True)
                qprint(f"{YELLOW}Files found: {', '.join(files_found) if files_found else '(none)'}{RESET}", force=True)

    except Exception as e:
        import traceback
        qprint(f"{RED}Installation error: {str(e)}{RESET}", force=True)
        qprint(f"{RED}Details: {traceback.format_exc()[-200:]}{RESET}", force=True)

def paginated_game_list():
    games = get_available_games()
    if not games:
        print_centered_block(f"{RED}No games available or connection error{RESET}")
        pause_for_continue()
        return

    games_per_page = 10
    current_page = 0
    search_query = ""

    while True:
        clear_screen()
        if search_query:
            sq = search_query.lower()
            visible_games = [g for g in games if sq in g.get("name", "").lower() or sq in str(g.get("appid", ""))]
        else:
            visible_games = games

        total_pages = max(1, (len(visible_games) + games_per_page - 1) // games_per_page)
        if current_page >= total_pages:
            current_page = max(0, total_pages - 1)

        header_lines = [
            f"Page {current_page + 1}/{total_pages}",
            f"Total: {len(visible_games)} games",
            (f"Search: {search_query}" if search_query else "Search: —"),
        ]
        header_panel = render_panel(f"{WHITE}AVAILABLE GAMES LIST{RESET}", header_lines)
        print_centered_block(header_panel)

        start_idx = current_page * games_per_page
        end_idx = min(start_idx + games_per_page, len(visible_games))

        for i, game in enumerate(visible_games[start_idx:end_idx], 1):
            appid = game.get("appid")
            name = game.get("name", "")
            display_name = name[:45] + "..." if len(name) > 45 else name
            print_centered_block(f"{WHITE}[{i:2}] {display_name} (ID: {appid}){RESET}")

        print_centered_block("")
        range_text = f"1-{end_idx-start_idx}" if (end_idx - start_idx) > 0 else "1-0"
        commands = [
            f"{YELLOW}Commands:{RESET}",
            f"[{PURPLE}P{RESET}]revious  [{PURPLE}N{RESET}]ext  [{PURPLE}S{RESET}]earch  [{PURPLE}Q{RESET}]uit",
            f"[{PURPLE}{range_text}{RESET}] Install",
        ]
        commands_panel = render_panel(f"{WHITE}NAVIGATION{RESET}", commands)
        print_centered_block(commands_panel)

        prompt_padding = " " * get_center_padding()
        prompt = f"{PURPLE}Choice: {RESET}"
        choice = ""
        did_inline_search = False

        inp = read_list_choice(prompt, instant_keys={"p", "n", "s", "q"})
        if inp == 's':
            search_prompt = prompt_padding + f"{PURPLE}Search term (empty to clear): {RESET}"
            new_q = input(search_prompt).strip()
            search_query = new_q
            current_page = 0
            did_inline_search = True
            choice = ''
        else:
            choice = inp

        if did_inline_search:
            continue
        if choice == 'q':
            break
        elif choice == 'p' and current_page > 0:
            current_page -= 1
        elif choice == 'n' and current_page < total_pages - 1:
            current_page += 1
        elif choice == 's':
            sq_prompt = " " * ((shutil.get_terminal_size().columns - FIXED_WIDTH)//2) + f"{PURPLE}Search term (empty to clear): {RESET}"
            new_q = input(sq_prompt).strip()
            search_query = new_q
            current_page = 0
        elif choice.isdigit():
            try:
                game_num = int(choice)
                if 1 <= game_num <= (end_idx - start_idx):
                    selected_game = visible_games[start_idx + game_num - 1]
                    clear_screen()
                    print_centered_block(f"{GREEN}Installing: {selected_game['name']} (ID: {selected_game['appid']}){RESET}")
                    time.sleep(1)
                    download_and_install_game_from_ryuuapi(selected_game['appid'])
                    pause_for_continue()
                else:
                    print_centered_block(f"{RED}Invalid number{RESET}")
                    time.sleep(1)
            except ValueError:
                if choice not in ['p', 'n', 'q']:
                    print_centered_block(f"{RED}Invalid command{RESET}")
                    time.sleep(1)

def _choose_file_dialog(mode, initial_dir, initial_file=None):
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] File dialog unavailable: {e}{RESET}")
        return ""

    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        if mode == "save":
            path = filedialog.asksaveasfilename(
                initialdir=initial_dir,
                initialfile=initial_file or "",
                defaultextension=".json",
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                title="Export installed games"
            )
        else:
            path = filedialog.askopenfilename(
                initialdir=initial_dir,
                filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
                title="Import installed games"
            )
        root.destroy()
        return path or ""
    except Exception as e:
        print_centered_block(f"{YELLOW}[!] File dialog error: {e}{RESET}")
        return ""


def export_installed_games():
    source_path = INSTALL_RECORD_FILE
    if not os.path.isfile(source_path):
        print_centered_block(f"{YELLOW}No installation records found to export.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    date_tag = time.strftime("%Y-%m-%d")
    default_filename = f"SteamTools-games-export-{date_tag}.json"
    chosen_path = _choose_file_dialog("save", DOCUMENTS_DIR, default_filename)

    if not chosen_path:
        print_centered_block(f"{YELLOW}Export canceled by user.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    dest_path = os.path.expanduser(os.path.expandvars(chosen_path))
    dest_dir = os.path.dirname(dest_path) or "."
    try:
        os.makedirs(dest_dir, exist_ok=True)
        shutil.copy2(source_path, dest_path)
        print_centered_block(f"{GREEN}[+] Exported install list to: {dest_path}{RESET}")
    except Exception as e:
        print_centered_block(f"{RED}[-] Export error: {e}{RESET}")
    pause_for_continue(leading_newline=True)


def import_and_reinstall_games():
    chosen_path = _choose_file_dialog("open", DOCUMENTS_DIR)
    if not chosen_path:
        print_centered_block(f"{YELLOW}Import canceled by user.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    import_path = os.path.expanduser(os.path.expandvars(chosen_path))
    if not os.path.isfile(import_path):
        print_centered_block(f"{RED}File not found: {import_path}{RESET}")
        pause_for_continue(leading_newline=True)
        return

    try:
        with open(import_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print_centered_block(f"{RED}Unable to read file: {e}{RESET}")
        pause_for_continue(leading_newline=True)
        return

    records = data.get("installed", data) if isinstance(data, dict) else None
    if not isinstance(records, dict) or not records:
        print_centered_block(f"{RED}Invalid format or empty list in export.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    games_to_install = []
    for appid, entry in records.items():
        appid_str = str(appid).strip()
        if not appid_str.isdigit():
            continue
        name = str(entry.get("name") or f"Game_{appid_str}")
        games_to_install.append((appid_str, name))

    if not games_to_install:
        print_centered_block(f"{RED}No valid games found in file.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    prompt_padding = " " * ((shutil.get_terminal_size().columns - FIXED_WIDTH)//2)
    confirm = input(prompt_padding + f"{YELLOW}Reinstall {len(games_to_install)} games? (y/n): {RESET}").strip().lower()
    if confirm != 'y':
        print_centered_block(f"{YELLOW}Import canceled.{RESET}")
        pause_for_continue(leading_newline=True)
        return

    for idx, (appid, name) in enumerate(games_to_install, 1):
        print_centered_block(f"{WHITE}[{idx}/{len(games_to_install)}] Reinstalling: {name} (ID: {appid}){RESET}")
        download_and_install_game_from_ryuuapi(appid, quiet=True, skip_restart_prompt=True)
    pause_for_continue(leading_newline=True)


def settings_menu():
    while True:
        current_path, custom_specified, custom_valid = get_custom_steam_location()
        if custom_specified and custom_valid:
            custom_path_label = current_path
        elif custom_specified:
            custom_path_label = "Invalid path configured"
        else:
            custom_path_label = "Default auto-detection"

        clear_screen()
        lines = [
            "[1] Import Games",
            "[2] Export Games",
            "[3] Set Custom Steam Location",
            "[4] Reset Custom Steam Location",
            "",
            render_kv("Steam path", truncate_ansi(custom_path_label, 50)),
            render_kv("Settings", truncate_ansi(SETTINGS_FILE, 50)),
            "",
            "[0] Back",
        ]
        panel = render_panel(f"{WHITE}SETTINGS{RESET}", lines)
        print_centered_block(panel)
        choice = read_single_key(f"{YELLOW}Choice : {RESET}", valid_keys=set("01234"))

        if choice == '1':
            import_and_reinstall_games()
        elif choice == '2':
            export_installed_games()
        elif choice == '3':
            configure_custom_steam_location()
        elif choice == '4':
            reset_custom_steam_location()
        elif choice == '0':
            break
        else:
            print_centered_block(f"{RED}Invalid choice.{RESET}")
            time.sleep(1.5)

def main_menu():
    while True:
        clear_screen()
        pixel_text()
        steam_running = is_steam_running()
        status_chip = make_chip("ACTIVE", GREEN) if steam_running else make_chip("INACTIVE", RED)
        action_label = "Restart Steam" if steam_running else "Launch Steam"

        menu_lines = [
            f"Status: {status_chip}",
            "",
            f"[1] Install a Steam game",
            f"[2] {action_label}",
            f"[3] Remove installed games",
            f"[4] View games list",
            f"[5] Steam Diagnosis",
            f"[6] Settings",
            f"[7] Credits",
            "",
            f"[0] Quit",
        ]
        footer = [f"{YELLOW}Tip:{RESET} You can use the number keys directly."]
        panel = render_panel(f"{WHITE}MAIN MENU{RESET}", menu_lines, footer)
        print_centered_block(panel)
        choice = read_single_key(f"{YELLOW}Choice : {RESET}", valid_keys=set("01234567"))

        if choice == '1':
            aid_input = input("" + " "*((shutil.get_terminal_size().columns-FIXED_WIDTH)//2) + f"{PURPLE}Enter game AppID or Steam URL : {RESET}").strip()
            appid = extract_appid_from_input(aid_input)
            if appid:
                download_and_install_game_from_ryuuapi(appid)
            else:
                print_centered_block(f"{RED}Valid AppID or Steam URL required{RESET}")
            pause_for_continue(leading_newline=True)

        elif choice == '2':
            if steam_running:
                subprocess.run("taskkill /IM steam.exe /F", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                time.sleep(2)
            if launch_steam():
                print_centered_block(f"{GREEN}[+] Steam launched{RESET}")
            else:
                print_centered_block(f"{RED}[-] Steam launch error{RESET}")
            pause_for_continue(leading_newline=True)

        elif choice == '3':
            remove_games_menu()

        elif choice == '4':
            paginated_game_list()

        elif choice == '5':
            diagnose_steam_installation()
            pause_for_continue(leading_newline=True)

        elif choice == '6':
            settings_menu()

        elif choice == '7':
            show_credits()
            pause_for_continue(leading_newline=True)

        elif choice == '0':
            print_centered_block(f"{GREEN}Goodbye!{RESET}")
            return
        else:
            print_centered_block(f"{RED}Invalid choice.{RESET}")
            time.sleep(1.5)

if __name__ == '__main__':
    set_console_icon()
    set_console_title("SteamTools")

    ensure_settings_storage()
    is_outdated, website_url, discord_url, remote_version = check_for_updates()
    if is_outdated:
        show_update_notice(website_url, discord_url, remote_version)
        sys.exit(0)
    main_menu()
    if getattr(sys, "frozen", False):
        os._exit(0)
    sys.exit(0)
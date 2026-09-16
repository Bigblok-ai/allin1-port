import requests
import json
import os
import copy
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict

# ==========================================
# CONFIG
# ==========================================
CATEGORIES = {
    "Bóng đá": "⚽ Bóng Đá",
    "Tennis": "🎾 Tennis",
    "Cầu Lông": "🏸 Cầu Lông",
    "Bóng rổ": "🏀 Bóng Rổ",
    "Billiards": "🎱 Billiards",
    "Bóng chuyền": "🏐 Bóng Chuyền",
    "Đua xe": "🏎️ Đua Xe",
    "Bóng bàn": "🏓 Bóng Bàn",
    "Võ Thuật": "🥊 Võ Thuật",
    "Bóng chày": "⚾ Bóng Chày",
    "Pickleball": "🏸 Pickleball"
}

SOURCES = [
    {"name": "Giovang", "url": "https://raw.githubusercontent.com/jasminliu98/giovang-stream/refs/heads/main/output.json"},
    {"name": "Hoiquan", "url": "https://raw.githubusercontent.com/jasminliu98/hoiquan-stream/refs/heads/main/output.json"},
    {"name": "PhaoHoa", "url": "https://raw.githubusercontent.com/jasminliu98/phaohoa-stream/refs/heads/main/output.json"},
    {"name": "ChuoiChien", "url": "https://raw.githubusercontent.com/jasminliu98/loc-stream/refs/heads/main/output.json"},
    {"name": "ChoangTV", "url": "https://raw.githubusercontent.com/jasminliu98/choang-stream/refs/heads/main/output.json"},
]

HOIQUAN_M3U_FILE = "hoiquan.m3u"      # file kênh TV đầu vào (định dạng M3U)
DEFAULT_TV_GROUP = "📺 Kênh Truyền Hình"
FOOTBALL_TIME_LIMIT_HOURS = 20

M3U_OUTPUT_FILE = "output.m3u"
M3U_INCLUDE_ALL_SOURCES = True        # True = link dự phòng xuất thành kênh riêng
M3U_SUFFIX_SOURCES = True             # Thêm " | Nguồn 2" vào link dự phòng

try:
    from zoneinfo import ZoneInfo
    VIETNAM_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
except ImportError:
    VIETNAM_TZ = timezone(timedelta(hours=7))

GROUP_SKELETON = [
    {"id": f"grp-{cate_raw.replace(' ', '-').lower()}", "name": cate_emoji, "display": "vertical", "grid_number": 2, "enable_detail": False, "channels": []}
    for cate_raw, cate_emoji in CATEGORIES.items()
]

# ==========================================
# BẢNG DỊCH TÊN (exact match trên toàn bộ tên đã normalize)
# ==========================================
VI_EN_MAP = {
    "duc": "germany",
    "nhat ban": "japan",
    "trung quoc": "china",
    "dai loan": "chinese taipei",
    "phap": "france",
    "han quoc": "korea",
    "anh": "england",
    "y": "italy",
    "tay ban nha": "spain",
    "my": "united states",
    "viet nam": "vietnam",
    "an do": "india",
    "trieu tien": "north korea",
    "thai lan": "thailand",
    "ha noi": "hanoi",
    "thanh hoa": "thanh hoa",
    "phu dong": "ninh binh",
    "hai phong": "hai phong",
    "ninh binh": "ninh binh",
    "xm hai phong": "hai phong",
    "chau la": "zhou luo",
    "ban van hoang": "ban van hoang",
    "wolves": "wolverhampton",
    "celta vigo": "celta vigo",
    "rc celta": "celta vigo",
    "espanyol": "espanyol",
    "rcd espanyol de barcelona": "espanyol",
    "bayer leverkusen": "bayer leverkusen",
    "bayer 04 leverkusen": "bayer leverkusen",
    "bayern munich": "bayern munich",
    "fc bayern munich": "bayern munich",
    "adelaide united fc": "adelaide united",
    "adelaide united": "adelaide united",
    "afc bournemouth": "bournemouth",
    "bournemouth afc": "bournemouth",
    "sevilla fc": "sevilla",
    "chelsea fc": "chelsea",
    "vfl wolfsburg": "wolfsburg",
    "wolfsburg": "wolfsburg",
    "brighton hove albion": "brighton",
    "wolverhampton wanderers": "wolverhampton",
    "manchester united": "manchester united",
    "inter milan": "inter milan",
    "fc inter milan": "inter milan",
    "psg": "paris saint germain",
    "athletico pr": "athletico paranaense",
    "atletico paranaense": "athletico paranaense",
    "stade brestois": "brest",
    "rennes": "stade rennais fc",
    "vietinbank": "viettinbank",
    "lp bank ninh binh": "lpb ninh binh",
    "suwon city": "suwon city w"
}

COUNTRY_MAP = {
    "tay ban nha": "spain",
    "trung quoc": "china",
    "nhat ban": "japan",
    "trieu tien": "north korea",
    "han quoc": "korea",
    "thai lan": "thailand",
    "viet nam": "vietnam",
    "an do": "india",
    "duc": "germany",
    "phap": "france",
    "anh": "england",
    "dai loan": "chinese taipei",
}

# ==========================================
# BẢNG CẤU HÌNH TỰ ĐỘNG TIÊM DRM CHO KÊNH
# ==========================================
DRM_AUTO_INJECT = [
    {
        "url_contains": "mytvnet.vn/pkg20/live_dzones/hbo.smil",
        "user_agent": "Dalvik/2.1.0",
        "drm_type": "clearkey",
        "drm_key": "09ddfe3d63863caf2eeb79d0546b098a:3dde0f38dcf014827dfd5bec38743c6a"
    },
    # Mẫu thêm kênh DRM mới (vd VTVPrime) — thay KID:KEY thật vào:
    # {
    #     "url_contains": "vtvprime.vn",
    #     "user_agent": "",
    #     "drm_type": "clearkey",
    #     "drm_key": "KID_HEX:KEY_HEX"
    # },
]

# ==========================================
# HELPER FUNCTIONS
# ==========================================
def fetch_json(url):
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception:
        return None

def normalize_cate_name(name):
    return re.sub(r'\s*\(\d+\s+\w+\)\s*$', '', name, flags=re.IGNORECASE).strip()

def remove_diacritics(text):
    text = text.replace('Đ', 'D').replace('đ', 'd')
    normalized = unicodedata.normalize('NFD', text)
    return ''.join(c for c in normalized if unicodedata.category(c) != 'Mn')

def normalize_time_for_match(time_val, date_val=""):
    time_val = time_val.strip()
    date_val = date_val.strip()
    if not time_val:
        return ""
    if re.match(r'^\d{1,2}:\d{2}\s+\d{1,2}/\d{1,2}$', time_val):
        return time_val.lower()
    parts = time_val.split(":")
    if len(parts) == 3:
        hh_mm = f"{parts[0]}:{parts[1]}"
        if date_val:
            return f"{hh_mm} {date_val}".lower()
        return hh_mm.lower()
    if re.match(r'^\d{1,2}:\d{2}$', time_val):
        if date_val:
            return f"{time_val} {date_val}".lower()
        return time_val.lower()
    return time_val.lower()

def normalize_time_in_channel(channel):
    meta = channel.get("org_metadata", {})
    time_val = meta.get("time", "").strip()
    date_val = meta.get("date", "").strip()
    if time_val and date_val:
        parts = time_val.split(":")
        if len(parts) == 3:
            meta["time"] = f"{parts[0]}:{parts[1]} {date_val}"
            meta.pop("date", None)
    return channel

def parse_match_datetime(time_str):
    if not time_str:
        return None
    try:
        parts = time_str.strip().split(" ")
        if len(parts) == 2:
            hm = parts[0].split(":")
            dm = parts[1].split("/")
            h, m = int(hm[0]), int(hm[1])
            d, mo = int(dm[0]), int(dm[1])
            now = datetime.now(VIETNAM_TZ)
            dt = datetime(now.year, mo, d, h, m, tzinfo=VIETNAM_TZ)
            if dt < now - timedelta(days=180):
                dt = datetime(now.year + 1, mo, d, h, m, tzinfo=VIETNAM_TZ)
            return dt
    except:
        pass
    return None

def normalize_team_for_match(name):
    if not name:
        return ""
    name = " ".join(name.strip().split())
    name = remove_diacritics(name)
    name = name.lower()
    name = name.replace("-", " ").replace(".", " ")
    name = " ".join(name.split())
    name = re.sub(r'\brep\b', '', name, flags=re.IGNORECASE)
    name = " ".join(name.split())
    if re.search(r'u\d+', name):
        name = re.sub(r'\s*\(w\)\s*', ' ', name, flags=re.IGNORECASE)
        name = re.sub(r'\bwomen\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\bnu\b', '', name, flags=re.IGNORECASE)
        name = re.sub(r'\bw\s*(?=u\d+)', '', name, flags=re.IGNORECASE)
        name = re.sub(r'(u\d+)\s*w\b', r'\1', name, flags=re.IGNORECASE)
        name = " ".join(name.split())
    for prefix in ["clb ", "ttbd ", "dclb ", "sb "]:
        if name.startswith(prefix):
            name = name[len(prefix):]
            break
    for suffix in [
        " football club", " f.c.", " fc", " cf", " sc", " ac", " afc",
        " s.c.", " a.f.c.", " c.d.", " cd", " sv", " e.v.",
        " club", " de futbol", " futebol", " united club",
    ]:
        if name.endswith(suffix):
            name = name[:-len(suffix)]
            break
    name = re.sub(r'\b\d+\b', '', name)
    name = " ".join(name.split())
    for vi_name, en_name in sorted(COUNTRY_MAP.items(), key=lambda x: -len(x[0])):
        pattern = r'\b' + re.escape(vi_name) + r'\b'
        if re.search(pattern, name):
            name = re.sub(pattern, en_name, name, count=1)
            break
    name = " ".join(name.split())
    name_clean = name.strip()
    if name_clean in VI_EN_MAP:
        return VI_EN_MAP[name_clean]
    return name_clean

def teams_match(team_a, team_b, old_a, old_b):
    norm_a = normalize_team_for_match(team_a)
    norm_b = normalize_team_for_match(team_b)
    norm_old_a = normalize_team_for_match(old_a)
    norm_old_b = normalize_team_for_match(old_b)

    if (not norm_a and not norm_b) or (not norm_old_a and not norm_old_b):
        return False

    def names_match(n1, n2):
        if not n1 or not n2:
            return False
        if n1 == n2:
            return True
        if len(n1) >= 4 and (n1 in n2 or n2 in n1):
            return True
        return False

    a_ma = names_match(norm_a, norm_old_a)
    a_mb = names_match(norm_a, norm_old_b)
    b_ma = names_match(norm_b, norm_old_a)
    b_mb = names_match(norm_b, norm_old_b)

    if norm_a and norm_b and norm_old_a and norm_old_b:
        return (a_ma and b_mb) or (a_mb and b_ma)

    if norm_a and norm_b:
        if not norm_old_b:
            return a_ma or b_ma
        if not norm_old_a:
            return a_mb or b_mb
    if norm_a:
        return a_ma or a_mb
    if norm_b:
        return b_ma or b_mb

    return False

def find_channel_index(time_val, team_a, team_b, channels_list, date_val=""):
    norm_time = normalize_time_for_match(time_val, date_val)
    for i, ch in enumerate(channels_list):
        meta = ch.get("org_metadata", {})
        old_time_raw = meta.get("time", "").strip()
        old_date = meta.get("date", "").strip()
        old_norm_time = normalize_time_for_match(old_time_raw, old_date)
        old_a = meta.get("team_a", "")
        old_b = meta.get("team_b", "")

        time_match = False
        if norm_time == old_norm_time:
            time_match = True
        elif norm_time == "" or old_norm_time == "":
            time_match = True

        if not time_match:
            continue

        if teams_match(team_a, team_b, old_a, old_b):
            return i

    return -1

def extract_sort_key(channel):
    meta = channel.get("org_metadata", {})
    is_live = meta.get("is_live", False)
    time_val = meta.get("time", "").strip()

    if is_live or not time_val:
        return (0, 0, 0, 0, 0)

    try:
        parts = time_val.split(" ")
        if len(parts) == 2:
            hm = parts[0].split(":")
            dm = parts[1].split("/")
            return (1, int(dm[1]), int(dm[0]), int(hm[0]), int(hm[1]))
        elif len(parts) == 1 and ":" in parts[0]:
            hm = parts[0].split(":")
            return (1, 99, 99, int(hm[0]), int(hm[1]))
    except:
        pass

    return (2, 99, 99, 99, 99)

# ==========================================
# ★ M3U READER — đọc file kênh TV (hoiquan.m3u)
# ==========================================
def parse_extinf(line):
    """Trả về (tên hiển thị, dict attrs) từ 1 dòng #EXTINF"""
    attrs = dict(re.findall(r'([\w-]+)="([^"]*)"', line))
    no_attrs = re.sub(r'"[^"]*"', '', line)
    if ',' in no_attrs:
        name = no_attrs.split(',', 1)[1].strip()
    else:
        name = attrs.get('tvg-name', '').strip()
    return name, attrs

def parse_m3u_tv(filepath):
    """
    M3U -> list dict: {name, group, url, logo, user_agent, referer,
                       drm_type, drm_key, manifest_type}
    - Đọc cả #EXTVLCOPT lẫn #EXTHTTP
    - Đọc group-title / #EXTGRP để giữ nguyên nhóm kênh
    - Đọc #KODIPROP (manifest_type, clearkey license_type/license_key)
    - Bỏ qua entry thiếu tên hoặc thiếu URL
    """
    entries = []
    current = None

    def finalize(cur):
        if cur and cur.get("url"):
            if not cur.get("name"):
                cur["name"] = "Unknown"
            entries.append(cur)

    with open(filepath, "r", encoding="utf-8-sig") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#EXTM3U"):
                continue

            if line.startswith("#EXTINF"):
                finalize(current)
                current = None
                name, attrs = parse_extinf(line)
                if not name:
                    continue  # bỏ entry không có tên
                current = {
                    "name": name,
                    "group": attrs.get("group-title", ""),
                    "url": "",
                    "logo": attrs.get("tvg-logo", ""),
                    "user_agent": "",
                    "referer": "",
                    "drm_type": "",
                    "drm_key": "",
                    "manifest_type": "",
                }
                continue

            if current is None:
                continue

            if line.startswith("#EXTVLCOPT:"):
                opt = line[len("#EXTVLCOPT:"):]
                low = opt.lower()
                if low.startswith("http-user-agent="):
                    current["user_agent"] = opt.split("=", 1)[1].strip()
                elif low.startswith("http-referrer=") or low.startswith("http-referer="):
                    current["referer"] = opt.split("=", 1)[1].strip()

            elif line.startswith("#EXTGRP:"):
                current["group"] = line.split(":", 1)[1].strip()

            elif line.startswith("#EXTHTTP:"):
                try:
                    hdr = json.loads(line[len("#EXTHTTP:"):])
                    current["user_agent"] = current["user_agent"] or hdr.get("User-Agent", "")
                    current["referer"] = current["referer"] or hdr.get("Referer", "")
                except Exception:
                    pass

            elif line.startswith("#KODIPROP:"):
                prop = line[len("#KODIPROP:"):].strip()
                if "license_type=clearkey" in prop:
                    current["drm_type"] = "clearkey"
                elif "license_key=" in prop:
                    current["drm_key"] = prop.split("license_key=", 1)[1].strip()
                elif "manifest_type=" in prop:
                    current["manifest_type"] = prop.split("manifest_type=", 1)[1].strip().lower()

            elif not line.startswith("#"):
                current["url"] = line
                finalize(current)
                current = None

    finalize(current)
    return entries

# ==========================================
# ★ KÊNH TRUYỀN HÌNH — build channel + gom nguồn + tiêm DRM
# ==========================================
def build_tv_channel_obj(i, name, variants):
    logo = variants[0].get("logo", "")

    sources = []
    for j, var in enumerate(variants):
        try:
            domain = var["url"].split("//")[1].split("/")[0]
            domain_parts = domain.split(".")
            src_name = f"Source {j+1} - {domain_parts[-2] if len(domain_parts) > 1 else domain}"
        except Exception:
            src_name = f"Source {j+1}"

        url = var.get("url", "")
        ua = var.get("user_agent", "")
        referer = var.get("referer", "")
        drm_type = var.get("drm_type", "")
        drm_key_raw = var.get("drm_key", "")

        # Tự động tiêm DRM nếu URL khớp cấu hình
        for drm_cfg in DRM_AUTO_INJECT:
            if drm_cfg["url_contains"] in url:
                if not ua: ua = drm_cfg.get("user_agent", "")
                if not drm_type: drm_type = drm_cfg.get("drm_type", "")
                if not drm_key_raw: drm_key_raw = drm_cfg.get("drm_key", "")
                break

        if drm_type == "clearkey" and not drm_key_raw:
            print(f"  ⚠️ '{name}': có DRM clearkey nhưng THIẾU license_key -> sẽ xuất không DRM (không thể giải mã)")

        # Nhận diện DASH: ưu tiên đuôi .mpd, dự phòng manifest_type từ KODIPROP
        is_dash = ".mpd" in url.lower() or var.get("manifest_type", "") == "mpd"
        link_type = "dash" if is_dash else "hls"

        headers = []
        if ua:
            headers.append({"key": "User-Agent", "value": ua})
        if referer:
            headers.append({"key": "Referer", "value": referer})

        sources.append({
            "id": f"src-tv-{i}-{j}",
            "name": src_name,
            "contents": [{
                "id": f"ct-tv-{i}-{j}",
                "name": name,
                "streams": [{
                    "id": f"st-tv-{i}-{j}",
                    "name": "KT",
                    "stream_links": [{
                        "id": f"lnk-tv-{i}-{j}",
                        "name": f"Link {j+1}",
                        "type": link_type,
                        "default": j == 0,
                        "url": url,
                        "request_headers": headers,
                        "drm_type": drm_type,
                        "drm_key": drm_key_raw
                    }]
                }]
            }]
        })

    return {
        "id": f"tv-{i}",
        "name": name,
        "type": "single",
        "display": "thumbnail-only",
        "enable_detail": False,
        "labels": [
            {"text": "● LIVE", "position": "top-left", "color": "#00000080", "text_color": "#ff4444"}
        ],
        "sources": sources,
        "org_metadata": {
            "is_live": True,
            "time": "",
            "team_a": name,
            "team_b": ""
        },
        "image": {
            "padding": 1,
            "background_color": "#ffffff",
            "display": "contain",
            "url": logo,
            "width": 1600,
            "height": 1200
        }
    }

def build_tv_groups(tv_list):
    """
    Gom link cùng tên -> 1 kênh nhiều nguồn.
    Giữ nguyên group-title từ file gốc -> trả về list các group TV.
    """
    group_order = []
    groups_map = {}
    for ch in tv_list:
        g_name = (ch.get("group") or "").strip() or DEFAULT_TV_GROUP
        if g_name not in groups_map:
            groups_map[g_name] = defaultdict(list)
            group_order.append(g_name)
        groups_map[g_name][ch["name"]].append(ch)

    result = []
    ch_counter = 0
    for g_name in group_order:
        channels = []
        for name, variants in groups_map[g_name].items():
            channels.append(build_tv_channel_obj(ch_counter, name, variants))
            ch_counter += 1
        result.append({
            "id": f"grp-tv-{len(result)}",
            "name": g_name,
            "display": "vertical",
            "grid_number": 2,
            "enable_detail": False,
            "channels": channels
        })
    return result

# ==========================================
# ★ M3U WRITER (TIVIMATE)
# ==========================================
def m3u_escape(text):
    return str(text or "").replace('"', "'").replace("\n", " ").replace("\r", "").strip()

def headers_to_dict(link):
    result = {}
    for h in link.get("request_headers", []) or []:
        k = (h.get("key") or "").strip()
        v = (h.get("value") or "").strip()
        if k and v:
            result[k] = v
    return result

def collect_links(channel):
    """Làm phẳng sources -> contents -> streams -> links thành 1 list"""
    links = []
    for src in channel.get("sources", []):
        for ct in src.get("contents", []):
            for st in ct.get("streams", []):
                for lnk in st.get("stream_links", []):
                    if lnk.get("url"):
                        links.append(lnk)
    return links

def build_display_name(ch, is_tv_group):
    meta = ch.get("org_metadata", {})
    team_a = (meta.get("team_a") or "").strip()
    team_b = (meta.get("team_b") or "").strip()
    time_val = (meta.get("time") or "").strip()

    if team_a and team_b:
        base = f"{team_a} vs {team_b}"
    else:
        base = team_a or ch.get("name", "Unknown")

    if is_tv_group:
        return base  # Kênh TV giữ tên sạch
    if meta.get("is_live", False):
        return f"🔴 {base}"
    if time_val:
        return f"{base} ({time_val})"
    return base

def m3u_entry(group_title, display_name, logo, link, source_index):
    url = (link.get("url") or "").strip()
    if not url:
        return []

    shown = display_name
    if M3U_SUFFIX_SOURCES and source_index > 0:
        shown = f"{display_name} | Nguồn {source_index + 1}"

    lines = [
        f'#EXTINF:-1 tvg-id="" tvg-name="{m3u_escape(display_name)}" '
        f'tvg-logo="{m3u_escape(logo)}" group-title="{m3u_escape(group_title)}",'
        f'{m3u_escape(shown)}'
    ]

    headers = headers_to_dict(link)
    ua = headers.pop("User-Agent", "") or headers.pop("user-agent", "")
    referer = (headers.pop("Referer", "") or headers.pop("referer", "")
               or headers.pop("Referrer", ""))

    # --- 1) #EXTHTTP (JSON) — TiViMate đọc chuẩn nhất ---
    hdr_json = {}
    if ua:
        hdr_json["User-Agent"] = ua
    if referer:
        hdr_json["Referer"] = referer
    hdr_json.update(headers)
    if hdr_json:
        lines.append("#EXTHTTP:" + json.dumps(hdr_json, separators=(",", ":"), ensure_ascii=False))

    # --- 2) #EXTVLCOPT — cho VLC / player khác ---
    if ua:
        lines.append(f"#EXTVLCOPT:http-user-agent={ua}")
    if referer:
        lines.append(f"#EXTVLCOPT:http-referrer={referer}")

    # --- 3) DASH hint (VLC bỏ qua, Kodi/TiViMate đọc) ---
    is_dash = link.get("type") == "dash" or ".mpd" in url.lower()
    if is_dash:
        lines.append("#KODIPROP:inputstream.adaptive.manifest_type=mpd")

    # --- 4) DRM Clearkey (chỉ xuất khi đủ type + key) ---
    drm_type = (link.get("drm_type") or "").lower()
    drm_key = link.get("drm_key") or ""
    if drm_type == "clearkey" and drm_key:
        lines.append("#KODIPROP:inputstream.adaptive.license_type=clearkey")
        lines.append(f"#KODIPROP:inputstream.adaptive.license_key={drm_key}")

    lines.append(url)
    return lines

def build_m3u(final_data):
    lines = ["#EXTM3U"]
    for group in final_data.get("groups", []):
        group_title = group.get("name", "Khác")
        is_tv_group = str(group.get("id", "")).startswith("grp-tv")
        for ch in group.get("channels", []):
            links = collect_links(ch)
            if not links:
                continue
            display = build_display_name(ch, is_tv_group)
            logo = ch.get("image", {}).get("url", "")
            max_links = len(links) if M3U_INCLUDE_ALL_SOURCES else 1
            emitted = 0
            for lnk in links:
                if emitted >= max_links:
                    break
                lines.extend(m3u_entry(group_title, display, logo, lnk, emitted))
                emitted += 1
    return "\n".join(lines) + "\n"

# ==========================================
# MAIN LOGIC
# ==========================================
def main():
    final_data = {"groups": copy.deepcopy(GROUP_SKELETON)}

    group_map = {}
    for g in final_data["groups"]:
        base_name = normalize_cate_name(g["name"])
        group_map[base_name] = g

    with ThreadPoolExecutor(max_workers=5) as executor:
        raw_jsons = list(executor.map(fetch_json, [s["url"] for s in SOURCES]))

    # ==========================================
    # BƯỚC 1: GỘP DỮ LIỆU THỂ THAO TỪ 5 NGUỒN JSON
    # ==========================================
    for index, raw_data in enumerate(raw_jsons):
        if not raw_data: continue
        source_name = SOURCES[index]["name"]

        for src_group in raw_data.get("groups", []):
            src_cate_name = normalize_cate_name(src_group.get("name", ""))
            if src_cate_name not in group_map: continue
            target_group = group_map[src_cate_name]

            for src_channel in src_group.get("channels", []):
                src_channel = normalize_time_in_channel(src_channel)

                meta = src_channel.get("org_metadata", {})
                time_val = meta.get("time", "")
                date_val = meta.get("date", "")
                team_a = meta.get("team_a", "")
                team_b = meta.get("team_b", "")
                blv_val = meta.get("blv", "")
                thumb_url = src_channel.get("image", {}).get("url", "")

                if not team_a: continue

                ch_idx = find_channel_index(time_val, team_a, team_b, target_group["channels"], date_val)

                if ch_idx == -1:
                    new_channel = copy.deepcopy(src_channel)
                    target_group["channels"].append(new_channel)
                else:
                    existing_channel = target_group["channels"][ch_idx]

                    if thumb_url and not existing_channel["image"].get("url"):
                        existing_channel["image"]["url"] = thumb_url

                    if meta.get("is_live"):
                        existing_channel["org_metadata"]["is_live"] = True
                        for label in existing_channel.get("labels", []):
                            if label.get("text") == "🕐 Sắp":
                                label["text"] = "● LIVE"
                                label["text_color"] = "#ff4444"

                    if time_val and not existing_channel["org_metadata"].get("time", ""):
                        existing_channel["org_metadata"]["time"] = time_val

                    existing_urls = set()
                    for ex_src in existing_channel.get("sources", []):
                        for ex_ct in ex_src.get("contents", []):
                            for ex_st in ex_ct.get("streams", []):
                                for link in ex_st.get("stream_links", []):
                                    if "url" in link:
                                        existing_urls.add(link["url"])

                    for inc_src in src_channel.get("sources", []):
                        has_new_link = False
                        temp_src = copy.deepcopy(inc_src)

                        for inc_ct in temp_src.get("contents", []):
                            for inc_st in inc_ct.get("streams", []):
                                new_valid_links = []
                                for link in inc_st.get("stream_links", []):
                                    link_url = link.get("url", "")
                                    if link_url and link_url not in existing_urls:
                                        new_valid_links.append(link)
                                        existing_urls.add(link_url)
                                        has_new_link = True

                                if new_valid_links:
                                    inc_st["stream_links"] = new_valid_links
                                    inc_st["name"] = f"{source_name} - {blv_val}".strip(" -")
                                else:
                                    inc_st["stream_links"] = []

                        if has_new_link:
                            existing_channel["sources"].append(temp_src)

    # ==========================================
    # BƯỚC 2: GỘP KÊNH TRUYỀN HÌNH (từ file M3U, giữ nguyên group)
    # ==========================================
    try:
        if os.path.exists(HOIQUAN_M3U_FILE):
            tv_list = parse_m3u_tv(HOIQUAN_M3U_FILE)
            if tv_list:
                tv_groups = build_tv_groups(tv_list)
                for gi, grp in enumerate(tv_groups):
                    final_data["groups"].insert(gi, grp)
                total_tv_ch = sum(len(g["channels"]) for g in tv_groups)
                print(f"Da doc {len(tv_list)} link -> {total_tv_ch} kenh TV trong {len(tv_groups)} nhom ({HOIQUAN_M3U_FILE}).")
        else:
            print(f"Canh bao: Khong tim thay file {HOIQUAN_M3U_FILE}.")
    except Exception as e:
        print(f"Canh bao: Loi xu ly {HOIQUAN_M3U_FILE} -> {e}")

    # ==========================================
    # BƯỚC 3: LỌC BÓNG ĐÁ — CHỈ GIỮ TRẬN TRONG 20H TỚI
    # ==========================================
    for g in final_data["groups"]:
        base_name = normalize_cate_name(g["name"])
        if base_name == "⚽ Bóng Đá":
            now_vn = datetime.now(VIETNAM_TZ)
            cutoff = now_vn + timedelta(hours=FOOTBALL_TIME_LIMIT_HOURS)

            before_count = len(g["channels"])
            filtered = []

            for ch in g["channels"]:
                meta = ch.get("org_metadata", {})
                time_val = meta.get("time", "").strip()
                is_live = meta.get("is_live", False)

                if is_live or not time_val:
                    filtered.append(ch)
                    continue

                match_dt = parse_match_datetime(time_val)
                if match_dt is None:
                    filtered.append(ch)
                    continue

                if now_vn - timedelta(hours=2) <= match_dt <= cutoff:
                    filtered.append(ch)

            removed = before_count - len(filtered)
            g["channels"] = filtered
            if removed > 0:
                print(f"  ⚽ Bóng Đá: Bo {removed} tran ngoai {FOOTBALL_TIME_LIMIT_HOURS}h toi")
            break

    # ==========================================
    # BƯỚC 3.5: DỌN DẸP KÊNH RỖNG (KHÔNG CÓ LINK)
    # ==========================================
    total_ghosts = 0
    for g in final_data["groups"]:
        valid_channels = []
        ghosts = 0
        for ch in g["channels"]:
            has_link = bool(collect_links(ch))
            if has_link:
                valid_channels.append(ch)
            else:
                ghosts += 1

        g["channels"] = valid_channels
        total_ghosts += ghosts
        if ghosts > 0:
            g_name = normalize_cate_name(g["name"])
            print(f"  🧹 {g_name}: Xoa {ghosts} 'kenh rong' (khong co link phat)")

    if total_ghosts > 0:
        print(f"  -> Tong cong da don dep {total_ghosts} kenh rong.")

    # ==========================================
    # BƯỚC 4: SẮP XẾP & ĐẾM LIVE
    # ==========================================
    for g in final_data["groups"]:
        g["channels"].sort(key=extract_sort_key)

        base_name = normalize_cate_name(g["name"])
        live_count = 0

        for ch in g.get("channels", []):
            meta = ch.get("org_metadata", {})
            is_live = meta.get("is_live", False)
            time_val = meta.get("time", "").strip()

            if is_live is True:
                live_count += 1
            elif time_val == "" and not meta.get("league"):
                live_count += 1

        if live_count > 0:
            g["name"] = f"{base_name} ({live_count} LIVE)"
        else:
            g["name"] = base_name

    # ==========================================
    # BƯỚC 5: DỌN FILE JSON CŨ (không còn dùng)
    # ==========================================
    if os.path.exists("output.json"):
        os.remove("output.json")
        print("  🗑️ Da xoa output.json (khong con su dung)")

    # ==========================================
    # BƯỚC 6: XUẤT FILE M3U CHO TIVIMATE
    # ==========================================
    m3u_content = build_m3u(final_data)

    staging = "staging.m3u"
    with open(staging, "w", encoding="utf-8", newline="\n") as f:
        f.write(m3u_content)

    if os.path.exists(M3U_OUTPUT_FILE):
        with open(M3U_OUTPUT_FILE, "r", encoding="utf-8") as f:
            old_content = f.read()
        if old_content == m3u_content:
            os.remove(staging)
            print(f"\nXong! -> Khong co thay doi, giu nguyen {M3U_OUTPUT_FILE}")
            return
        os.replace(staging, M3U_OUTPUT_FILE)
    else:
        os.replace(staging, M3U_OUTPUT_FILE)

    total_entries = m3u_content.count("#EXTINF")
    print(f"\nXong! {total_entries} kenh -> {M3U_OUTPUT_FILE} (DA CAP NHAT)")

if __name__ == "__main__":
    main()

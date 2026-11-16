"""SeedBoxBuntu - agrégateur : lecture des titres de torrents, catégories, saisons et rapprochement par œuvre.

Fonctions pures (aucune entrée-sortie), portées de P2P Aggregator 0.2.5 : normalisation des titres, déduction de la catégorie
(films, séries, musique, jeux, livres, presse, logiciels), clé de regroupement par œuvre (IMDb, TMDB, revue, titre + année),
saisons et épisodes, similarité de deux œuvres.
Écart volontaire avec l'original : la distance d'édition (Levenshtein) ne compte que pour des titres d'au moins 8 caractères,
pour que « Alien » et « Aliens » ne fusionnent pas.
"""
import re
import unicodedata

CATEGORIES = ("films", "series", "musique", "jeux", "livres", "presse", "logiciels")
CATEGORY_LABELS = {"films": "Films", "series": "Séries", "musique": "Musique", "jeux": "Jeux", "livres": "Livres", "presse": "Presse",
                   "logiciels": "Logiciels", "unknown": "Autres"}
QUALITY_ORDER = ("4K", "FHD", "HD", "SD", "AUDIO", "EBOOK", "GAME", "OTHER")
MATCH_THRESHOLD = 0.75

# ---------------------------------------------------------------------------------------------------------------
#  Catégories
# ---------------------------------------------------------------------------------------------------------------
ALIASES = {
    "film": "films", "films": "films", "movie": "films", "movies": "films", "cinema": "films",
    "tv": "series", "television": "series", "tvseries": "series", "tv series": "series", "serie": "series", "series": "series",
    "séries": "series", "série": "series", "show": "series", "shows": "series", "anime": "series",
    "music": "musique", "musique": "musique", "audio": "musique",
    "game": "jeux", "games": "jeux", "jeux": "jeux", "console": "jeux",
    "book": "livres", "books": "livres", "livres": "livres", "ebook": "livres",
    "presse": "presse", "magazine": "presse",
    "software": "logiciels", "apps": "logiciels", "application": "logiciels", "applications": "logiciels", "app": "logiciels",
    "logiciels": "logiciels", "logiciel": "logiciels", "pc": "logiciels", "pc/0day": "logiciels", "pc/iso": "logiciels",
}
_GAME_IDS = ("4050", "1000", "1010", "1020", "1030", "1040", "1050", "1060", "1070", "1080", "1090", "1110", "1120", "1130", "1140", "1180")
_SOFT_IDS = ("4010", "4020", "4030", "4040", "4060", "4070")
_ID_LISTS = {
    "films": ("2000", "2010", "2020", "2030", "2040", "2050", "2060", "2070", "2080", "2090", "102000", "102010", "102030", "102060", "102070",
              "102080", "102090"),
    "series": ("5000", "5010", "5020", "5030", "5040", "5050", "5060", "5070", "5080", "5090", "105000", "105060", "105070", "105080"),
    "musique": ("3000", "3010", "3020", "3040", "3050", "103000", "103010", "103050", "102050"),
    "jeux": _GAME_IDS + ("101000", "101030", "101040", "101080", "101090", "104050"),
    "logiciels": _SOFT_IDS + ("104000", "104030", "104040", "104060", "104070", "108010"),
    "livres": ("7000", "7020", "7030", "3030", "103030", "107000", "107020", "107030"),
    "presse": ("7010", "107010"),
}
ID_TO_SLUG = {identifier: slug for slug, ids in _ID_LISTS.items() for identifier in ids}
ID_TO_SLUG.update({"6000": "unknown", "6010": "unknown", "8000": "unknown", "8010": "unknown", "100001": "unknown"})

SERIES_TITLE = re.compile(r"\b(s\d{1,2}[.\s_-]*e\d{1,4}|s\d{2}|s\d{1,2}(?=\d{2,4}\b)|\d{1,2}x\d{1,4}|season\s*\d{1,2}|saison\s*\d{1,2}|series\s*complete|"
                          r"complete\s*series|integrale|intégrale|integral)\b", re.I)
COMIC_VOLUME = re.compile(r"\b(?:tome|tom|vol\.?|volume)\s*0*(\d{1,3})\b|\bT0*(\d{2,3})\b", re.I)
COMIC_VOLUME_STRIP = re.compile(r"\b(?:tome|tom|vol\.?|volume)\s*0*\d{1,3}\b|\bT0*\d{1,3}\b", re.I)
EPISODE_MARKER = re.compile(r"\bS\d{1,2}[.\s_-]*E?\d{1,4}\b", re.I)
GAME_TITLE = re.compile(r"\b(ps[1-5]|ps vita|playstation|xbox(?:\s*one|\s*series)?|nintendo|switch|wiiu?|3ds|steam|goty|update\s*v\d|repack-[\w]+|dlc|"
                        r"fitgirl|empress|runeword|dodi|elamigos|skidrow|tenoke|goldberg|[-.](?:codex|rune))\b", re.I)
FLIGHT_SIM = re.compile(r"\b(msfs(?:\s*\d{4})?|microsoft\s+flight\s+simulator|x-?plane|dcs\s+world|il-?2\s+sturmovik|prepar3d|p3d|fsx)\b", re.I)
ADDON_VENDOR = re.compile(r"\b(aerosoft|pmdg|fenix|flybywire|ini\d?builds|orbx|carenado|justflight|flightbeam|qualitywings)\b", re.I)
PC_GAME = re.compile(r"\b(?:gog|drm[-\s]?free|\+dlcs?|art/ost|meta\s*quest|oculus|quest\s*(?:2|3|3s|pro)|(?:premium|deluxe|gold|goty|ultimate|complete|"
                     r"definitive|collector|digital|rebuild|enhanced)\s+edition|portable(?:\s+\S+){0,4}?\s+preactiv\w*)\b", re.I)
MUSIC_TITLE = re.compile(r"\b(?:flac|mp3|ape|alac|wav|ogg|m4a|dsf|dsd|sacd|vinyl|lossless)\b|\b(?:\d{1,2}\s*bit|\d{2,4}\s*khz|\d{2,3}\s*kbps)\b|"
                         r"\b(?:various\s*artists|compilation|mixtape)\b|\b(?:original\s+)?(?:movie\s+)?soundtrack\b|\bva\s+", re.I)
SOFTWARE_TITLE = re.compile(r"\b(windows|office|adobe|autocad|winrar|7-zip|matlab|keygen|crack|patch|v\d+\.\d+(?:\.\d+)?|x64|x86|setup|installer|appimage|"
                            r"\.dmg|macos|linux|ubuntu|debian|portable|android|apk|apks|ios|iphone|ipad)\b", re.I)
FILM_VIDEO_TITLE = re.compile(r"\b(2160p|1080p|720p|480p|576p|4k|uhd|web[-.]?dl|webrip|web[-.]?rip|bluray|blu[-.]?ray|bdrip|brrip|hdrip|hdtv|dvdrip|"
                              r"remux|proper|repack|vff|vfq|vosta|vostfr|truefrench|subfrench|multi\.(?:vff|vfq)|x264|x265|h264|h265|hevc|avc|"
                              r"aac\s*5|ddp\d|eac3|hdr10|dv|atmos)\b", re.I)
PRINT3D_TITLE = re.compile(r"(\[stl\]|\.stl\b|\bstl\b|3mf|gcode|\.gcode|printabl|impression\s*3d|3d\s*print|thingiverse|makerworld)", re.I)
PRESSE_TITLE = re.compile(r"\b(magazine|mag\b|revue|journal\b|n[°o]\s*\d{1,4}\b|issue\s*\d{1,4}|beaux\s*arts|paris\s*match|le\s*monde|le\s*figaro|presse\b)", re.I)
BOOK_TITLE = re.compile(r"\b(epub|mobi|azw3?|pdf|ebook|e-book|ibooks|djvu|cbr|cbz)\b", re.I)


def is_comic_volume(title):
    if not COMIC_VOLUME.search(title):
        return False
    if re.search(r"\b(?:cbr|cbz|cb7)\b", title, re.I) or re.search(r"\b(?:tome|tom|vol\.?|volume)\s*\d", title, re.I):
        return True
    return bool(re.search(r"\bT0*\d{2,3}\b", title, re.I))


def is_series(title):
    return bool(SERIES_TITLE.search(title)) or is_comic_volume(title)


def is_film_video(title):
    return not is_series(title) and bool(FILM_VIDEO_TITLE.search(title))


def is_music(title):
    return not is_film_video(title) and bool(MUSIC_TITLE.search(title))


def is_book(title):
    return not is_film_video(title) and bool(BOOK_TITLE.search(title))


def is_game(title):
    if FLIGHT_SIM.search(title):
        return True
    if ADDON_VENDOR.search(title) and re.search(r"\b(msfs|addon|aircraft|livery|scenery|simulator|sim\b|v\d+(?:\.\d+)*)\b", title, re.I):
        return True
    return bool(GAME_TITLE.search(title) or PC_GAME.search(title))


def strip_episode_markers(title):
    cleaned = EPISODE_MARKER.sub(" ", title)
    cleaned = re.sub(r"\bSaison\s*\d{1,2}\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\bSeason\s*\d{1,2}\b", " ", cleaned, flags=re.I)
    cleaned = re.sub(r"\b\d{1,2}x\d{1,4}\b", " ", cleaned, flags=re.I)
    if is_comic_volume(title):
        cleaned = COMIC_VOLUME_STRIP.sub(" ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def infer_category(title):
    if is_series(title):
        return "series"
    if is_film_video(title):
        return "films"
    if PRINT3D_TITLE.search(title):
        return "print3d"
    if is_music(title):
        return "musique"
    if is_game(title):
        return "jeux"
    if SOFTWARE_TITLE.search(title):
        return "logiciels"
    if PRESSE_TITLE.search(title):
        return "presse"
    if is_book(title):
        return "livres"
    return None


def _override(current, inferred, title):
    if not current or current == "unknown":
        return True
    if inferred == "films" and current in ("presse", "livres"):
        return True
    if inferred == "series" and current in ("films", "presse", "livres"):
        return True
    if inferred == "print3d" and current in ("presse", "livres"):
        return True
    if inferred == "presse" and current == "livres":
        return True
    if inferred == "livres" and current == "presse" and not PRESSE_TITLE.search(title):
        return True
    return (current, inferred) in (("logiciels", "jeux"), ("jeux", "logiciels"), ("jeux", "musique"), ("logiciels", "musique")) \
        or (current == "films" and inferred == "musique" and is_music(title))


def category_from_ids(ids):
    """Identifiants Torznab (2000, 102010...) ou libellés (« Movies/HD ») → catégorie interne, ou None."""
    for raw in ids:
        raw = str(raw).strip()
        if not raw:
            continue
        if raw.isdigit():
            candidates = [raw]
            if len(raw) == 6:
                candidates.append(str(int(raw) // 1000 * 1000))
            elif len(raw) == 4:
                candidates.append(raw[0] + "000")
            for candidate in candidates:
                if candidate in ID_TO_SLUG:
                    return ID_TO_SLUG[candidate]
        else:
            key = raw.lower()
            for candidate in (key, key.split("/")[0].strip(), key.split(" ")[0]):
                if candidate in ALIASES:
                    return ALIASES[candidate]
    return None


def resolve_category(raw_ids, title):
    category = category_from_ids(raw_ids or [])
    inferred = infer_category(title)
    if inferred:
        mapped = "logiciels" if inferred == "print3d" else inferred
        if _override(category, inferred, title):
            category = mapped
    return category or "unknown"


def resolve_work_category(category, titles):
    for title in titles:
        if is_series(title):
            return "series"
    for title in titles:
        if is_film_video(title):
            return "films"
    for title in titles:
        if is_music(title):
            return "musique"
    for title in titles:
        inferred = infer_category(title)
        if inferred == "print3d":
            return "logiciels"
        if inferred:
            return inferred
    return category


# ---------------------------------------------------------------------------------------------------------------
#  Titres
# ---------------------------------------------------------------------------------------------------------------
YEAR = re.compile(r"\b(19|20)\d{2}\b")
RELEASE_GROUP = re.compile(r"-([A-Za-z0-9]+)$")
RESOLUTIONS = ((re.compile(r"\b2160p\b", re.I), "2160p"), (re.compile(r"\b4k\b", re.I), "4K"), (re.compile(r"\buhd\b", re.I), "4K"),
               (re.compile(r"\b1080p\b", re.I), "1080p"), (re.compile(r"\b720p\b", re.I), "720p"), (re.compile(r"\b576p\b", re.I), "576p"),
               (re.compile(r"\b480p\b", re.I), "480p"), (re.compile(r"\b360p\b", re.I), "360p"))
TAG_TOKENS = {"french", "vff", "vfq", "vf2", "multi", "truefrench", "subfrench", "vostfr", "vo", "web-dl", "webdl", "webrip", "web", "bluray",
              "blu-ray", "bdrip", "brrip", "hdrip", "dvdrip", "hdtv", "remux", "x264", "x265", "h264", "h265", "hevc", "avc", "aac", "dts", "atmos",
              "hdr", "dv", "dolby", "proper", "repack", "internal", "complete", "extended", "uncut", "directors", "cut"}
SKIP_TOKENS = TAG_TOKENS | {"fr", "frf", "eng", "en", "1080p", "720p", "2160p", "480p", "576p", "360p", "4k", "uhd", "hd", "fhd", "sd", "xxx", "porn"}
QUALITY_PATTERNS = (("4K", (r"\b2160p\b", r"\b4k\b", r"\buhd\b")), ("FHD", (r"\b1080p\b", r"\bfull[\s-]?hd\b", r"\bfhd\b")),
                    ("HD", (r"\b720p\b", r"\bhd\b")), ("SD", (r"\b480p\b", r"\b576p\b", r"\bsd\b", r"\bdvdrip\b")),
                    ("AUDIO", (r"\bflac\b", r"\bmp3\b", r"\baudio\b", r"\bcd\b")), ("EBOOK", (r"\bebook\b", r"\bepub\b", r"\bpdf\b", r"\bmobi\b")),
                    ("GAME", (r"\bgame\b", r"\bps5\b", r"\bps4\b", r"\bswitch\b", r"\bxbox\b", r"\bpc\b")))
MUSIC_WORDS = {"mp3", "flac", "wav", "aac", "ogg", "ape", "alac", "wma", "m4a", "v0", "v2", "cue", "log", "cbr", "vbr", "abr", "vinyl", "cd", "dvd",
               "sacd", "dsf", "dsd", "khz", "hz", "kbps", "bit"}
EBOOK_WORDS = {"pdf", "epub", "mobi", "azw", "azw3", "cbr", "cbz", "djvu", "fb2", "lit", "ibooks", "cb7"}
FILM_AUDIO_WORDS = {"ac3", "eac3", "aac", "dts", "dd", "ddp", "truehd", "atmos", "dtshd", "ma", "thd", "pcm", "lpcm", "opus", "lc", "he", "hs", "hdts",
                    "cam", "hdcam"}
MONTHS = {"janvier", "fevrier", "février", "mars", "avril", "mai", "juin", "juillet", "aout", "août", "septembre", "octobre", "novembre", "decembre",
          "décembre", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"}


def quality_bucket(label="", category=None):
    text = (label or "").strip()
    if not text and category == "jeux":
        return "GAME"
    if not text and category in ("livres", "presse"):
        return "EBOOK"
    if not text and category == "musique":
        return "AUDIO"
    for bucket, patterns in QUALITY_PATTERNS:
        if any(re.search(p, text, re.I) for p in patterns):
            return bucket
    return "OTHER"


def tokenize(raw):
    without_group = RELEASE_GROUP.sub("", raw)
    return [part.strip() for part in re.split(r"[.\s_]+", re.sub(r"[\[\]()]", " ", without_group)) if part.strip()]


def fold(text):
    return "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))


def normalize_key(text):
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", fold(text.lower()))).strip()


def display_title(tokens):
    out = []
    for token in tokens:
        if token.isdigit() or (len(token) <= 3 and token == token.upper()):
            out.append(token)
        else:
            out.append(token[:1].upper() + token[1:].lower())
    return " ".join(out).strip()


def _music_format(token):
    low = token.lower()
    return low in MUSIC_WORDS or bool(re.fullmatch(r"\d+(?:\.\d+)?(?:bit|kbps|khz|mhz)", low)) or bool(re.fullmatch(r"\d{2,4}k", low))


def _film_audio(token):
    low = token.lower()
    return low in FILM_AUDIO_WORDS or bool(re.fullmatch(r"e?ac3", low)) or bool(re.fullmatch(r"dts[-.]?\w*", low)) or bool(re.fullmatch(r"dd\+?", low))


def _strip_surround(tokens):
    out, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        if token in ("5", "7") and i + 1 < len(tokens) and tokens[i + 1] == "1":
            i += 2
            continue
        if token == "2" and i + 1 < len(tokens) and tokens[i + 1] == "0":
            i += 2
            continue
        if _film_audio(token):
            i += 1
            continue
        out.append(token)
        i += 1
    return [t for n, t in enumerate(out)
            if not (t.lower() == "1" and n > 0 and out[n - 1] in ("5", "7", "2")) and not (t == "0" and n > 0 and out[n - 1] == "2")]


def _strip_music(tokens):
    out, i = [], 0
    while i < len(tokens):
        token = tokens[i]
        nxt = tokens[i + 1].lower() if i + 1 < len(tokens) else ""
        if _music_format(token):
            i += 1
            continue
        if re.fullmatch(r"\d{1,3}", token) and nxt == "1" and i + 2 < len(tokens) and tokens[i + 2].lower() == "khz":
            i += 3
            continue
        if re.fullmatch(r"\d{1,3}", token) and nxt == "khz":
            i += 2
            continue
        out.append(token)
        i += 1
    return [t for n, t in enumerate(out)
            if not (t == "1" and n > 0 and re.fullmatch(r"\d{1,3}", out[n - 1]))
            and not (re.fullmatch(r"\d{1,3}", t) and n + 1 < len(out) and out[n + 1] == "1") and t.lower() not in ("44", "48", "88", "96", "176", "192")]


def strip_format_tokens(tokens, category):
    if category == "musique":
        return _strip_music(tokens)
    if category in ("livres", "presse"):
        return [t for t in tokens if t.lower() not in EBOOK_WORDS]
    if category in ("films", "series"):
        return [t for t in _strip_surround(tokens) if t.lower() not in EBOOK_WORDS]
    return tokens


def magazine_issue(raw):
    text = re.sub(r"[\[\]()]", " ", raw)
    for pattern in (r"\bN[°ºo.]?\s*(\d{1,4})\b", r"\bN\s+(\d{1,4})\b", r"\bNo\.?\s*(\d{1,4})\b", r"\bNumero\s*(\d{1,4})\b", r"\bIssue\s*#?\s*(\d{1,4})\b"):
        found = re.search(pattern, text, re.I)
        if found and 0 < int(found.group(1)) < 10000:
            return str(int(found.group(1)))
    return None


def _magazine_title(raw, tokens):
    issue = magazine_issue(raw)
    kept, skip = [], False
    for n, token in enumerate(tokens):
        low = token.lower()
        if skip:
            skip = False
            continue
        if low in MONTHS or low in ("fr", "frf", "vf", "vff", "eng", "en", "francais", "français") or (issue and token == issue):
            continue
        if low in ("n", "no"):
            if issue and n + 1 < len(tokens) and tokens[n + 1] == issue:
                skip = True
            if issue:
                continue
        kept.append(token)
    base = re.sub(r"\s+", " ", display_title(kept)).strip()
    has_issue = bool(issue) and bool(re.search(rf"\bN[°ºo]?\s*{issue}\b", base, re.I))
    title = f"{base} N°{issue}".strip() if issue and not has_issue else base
    normalized = re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", fold(title.lower()).replace("°", " "))).strip()
    return title, normalized


def music_year(raw):
    paren = re.search(r"\((19|20)\d{2}\)\s*$", raw)
    if paren:
        return int(re.search(r"\d{4}", paren.group(0)).group(0))
    years = [int(m.group(0)) for m in re.finditer(r"\b(?:19|20)\d{2}\b", raw) if 1900 <= int(m.group(0)) <= 2100]
    return min(years) if years else None


def parse_name(raw):
    trimmed = raw.strip()
    group = RELEASE_GROUP.search(trimmed)
    tokens = tokenize(trimmed)
    year, rest = None, []
    for token in tokens:
        found = YEAR.search(token)
        if year is None and found and 1900 <= int(found.group(0)) <= 2100:
            year = int(found.group(0))
            continue
        rest.append(token)
    resolution = None
    joined = " ".join(rest)
    for pattern, label in RESOLUTIONS:
        if pattern.search(joined):
            resolution, rest = label, [t for t in rest if not pattern.search(t)]
            break
    tags, title_tokens = [], []
    for token in rest:
        low = token.lower()
        if low in TAG_TOKENS:
            tags.append(token.upper())
        elif low not in SKIP_TOKENS:
            title_tokens.append(token)
    title = display_title(title_tokens) or trimmed
    return {"title": title, "normalized": normalize_key(title), "year": year, "resolution": resolution, "tags": list(dict.fromkeys(tags)),
            "group": group.group(1) if group else None, "bucket": quality_bucket(resolution or "")}


def parse_work(raw, category=None):
    """Titre de l'œuvre (sans épisode, sans caractéristiques techniques) et clé de rapprochement."""
    series = category == "series" or is_series(raw)
    base = strip_episode_markers(raw) if series else raw
    parsed = parse_name(base)
    work_category = "series" if series else category
    if work_category not in ("musique", "livres", "presse", "films", "series"):
        return parsed
    tokens = tokenize(parsed["title"] or base)
    after_year = [t for t in tokens if not (YEAR.search(t) and 1900 <= int(YEAR.search(t).group(0)) <= 2100)]
    title_tokens = strip_format_tokens(after_year, work_category)
    if work_category == "presse" or (work_category == "livres" and magazine_issue(raw)):
        title, normalized = _magazine_title(raw, title_tokens)
        return {**parsed, "title": title or parsed["title"], "normalized": normalized or parsed["normalized"]}
    title = display_title(title_tokens) or parsed["title"]
    year = (music_year(raw) or parsed["year"]) if work_category == "musique" else parsed["year"]
    return {**parsed, "title": title, "normalized": normalize_key(title), "year": year}


def magazine_key(titles):
    for raw in titles:
        if raw and magazine_issue(raw):
            normalized = parse_work(raw, "presse")["normalized"]
            if normalized:
                return "magazine::" + normalized
    return None


def identity(category, titles, fallback_title, fallback_normalized):
    """Catégorie effective, titre et clé de l'œuvre d'après les titres de ses torrents."""
    has_series = any(is_series(t) for t in titles)
    work_category = resolve_work_category(category, [fallback_title, *titles])
    effective = "series" if has_series or work_category == "series" else work_category
    seed = next((t for t in titles if episode_of(t) is not None), None) or next((t for t in titles if is_series(t)), None) \
        or (titles[0] if titles else fallback_title)
    parsed = parse_work(seed, effective)
    return {"category": effective, "title": parsed["title"] or fallback_title, "normalized": parsed["normalized"] or fallback_normalized,
            "year": parsed["year"]}


def group_key(imdb, tmdb, category, normalized, year, titles):
    if imdb:
        return f"imdb:{imdb}"
    if tmdb:
        return f"tmdb:{tmdb}"
    mag = magazine_key(titles)
    if mag:
        return mag
    if category == "musique":
        return f"musique::{normalized}"
    return f"work::{year or 'unknown'}::{normalized}"


# ---------------------------------------------------------------------------------------------------------------
#  Rapprochement
# ---------------------------------------------------------------------------------------------------------------
NOISE = {"vosta", "vostfr", "vosteng", "vff", "vfq", "vo", "multi", "proper", "repack", "aac", "ac3", "dts", "flac", "x264", "x265", "hevc", "h264",
         "h265", "avc", "lc", "dl", "rip", "hdr", "sdr", "atmos", "truefrench", "subfrench", "french", "english", "german", "spanish", "italian", "pdf",
         "epub", "mobi", "mp3", "320", "320kbps", "128", "256", "v0", "v2", "16bit", "24bit", "32bit", "bit", "kbps", "khz", "hz", "vinyl", "cd",
         "dsd", "sacd", "eac3", "dd5", "dd5.1", "truehd", "hdlight"}


def match_tokens(normalized, category=None):
    out = []
    for token in normalized.split():
        if len(token) <= 1 or token in NOISE or re.fullmatch(r"\d{1,2}", token):
            continue
        if category == "series" and (re.fullmatch(r"e?\d{1,4}", token, re.I) or re.fullmatch(r"s\d{1,2}", token, re.I) or re.fullmatch(r"t\d{1,3}", token, re.I)):
            continue
        out.append(token)
    return out


def levenshtein_similarity(a, b):
    if not a and not b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        row = [i]
        for j, cb in enumerate(b, 1):
            row.append(min(previous[j] + 1, row[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = row
    return 1 - previous[-1] / max(len(a), len(b))


def similarity(a, b):
    """Deux œuvres : {category, normalized, year, imdb, tmdb, magazine}. 1 = même œuvre, 0 = différentes."""
    if a.get("imdb") and a.get("imdb") == b.get("imdb"):
        return 1.0
    if a.get("tmdb") and a.get("tmdb") == b.get("tmdb"):
        return 1.0
    if a.get("magazine") and b.get("magazine"):
        return 1.0 if a["magazine"] == b["magazine"] else 0.0
    ca, cb = a["category"], b["category"]
    compatible = ca == cb or {ca, cb} == {"films", "series"} \
        or ({ca, cb} <= {"presse", "livres"} and bool(a.get("magazine") or b.get("magazine")))
    if not compatible:
        return 0.0
    category = "series" if "series" in (ca, cb) else ca
    if category != "musique" and a.get("year") and b.get("year") and a["year"] != b["year"]:
        return 0.0
    ka, kb = " ".join(match_tokens(a["normalized"], category)), " ".join(match_tokens(b["normalized"], category))
    if ka == kb:
        return 1.0
    ta, tb = set(ka.split()), set(kb.split())
    if not ta or not tb:
        return 0.0
    contained = len(ta & tb) / min(len(ta), len(tb))
    if contained >= 0.85:
        return max(contained, 0.88)
    overlap = len(ta & tb) / len(ta | tb)
    edit = levenshtein_similarity(ka, kb) if min(len(ka), len(kb)) >= 8 else 0.0
    return max(overlap, edit)


# ---------------------------------------------------------------------------------------------------------------
#  Saisons et épisodes
# ---------------------------------------------------------------------------------------------------------------
def episode_of(text):
    raw = (text or "").strip()
    if not raw:
        return None
    for pattern in (r"\bS\d{1,2}[.\s_-]*E(\d{1,4})\b", r"\b\d{1,2}x(\d{1,4})\b"):
        found = re.search(pattern, raw, re.I)
        if found:
            return int(found.group(1))
    if is_comic_volume(raw):
        named = re.search(r"\b(?:Tome|Tom|Vol\.?|Volume)\s*0*(\d{1,3})\b", raw, re.I) or re.search(r"\bT0*(\d{1,3})\b", raw, re.I)
        if named:
            return int(named.group(1))
    return None


def is_episode_title(text):
    return episode_of(text) is not None


def season_of(text):
    raw = (text or "").strip()
    if not raw:
        return None
    if re.search(r"\b(integrale|intégrale|integral|complete\s*series|series\s*complete)\b", raw, re.I):
        return "complete"
    for pattern in (r"\b(?:saison|season)\s*(\d{1,2})\b", r"\bS(\d{1,2})[.\s_-]*E\d{1,4}\b", r"\bS(\d{1,2})\b", r"\b(\d{1,2})x\d{1,4}\b"):
        found = re.search(pattern, raw, re.I)
        if found:
            return str(int(found.group(1)))
    return None


def season_key(text):
    return season_of(text) or "unknown"


def season_label(key):
    return {"complete": "Intégrale", "unknown": "Saison non précisée"}.get(key) or f"Saison {key}"


def season_order(key):
    if key == "complete":
        return (2, 0)
    if key == "unknown":
        return (1, 0)
    return (0, -int(key))


def episode_label(text):
    episode = episode_of(text)
    if episode is None:
        return None
    season = season_of(text)
    if re.search(r"\bS\d{1,2}[.\s_-]*E\d{1,4}\b", text, re.I) and season not in (None, "complete"):
        return f"S{int(season):02d}E{episode:02d}"
    if is_comic_volume(text) and not re.search(r"\bS\d{1,2}[.\s_-]*E", text, re.I):
        return f"T{episode:02d}"
    return f"E{episode:02d}"


def season_groups(category, variants):
    """Séries : variantes regroupées par saison quand plusieurs saisons ou plusieurs épisodes sont détectés."""
    if category != "series" or not variants:
        return None
    by_season = {}
    for variant in variants:
        text = " ".join(filter(None, [variant.get("torrent_title"), variant.get("label")]))
        by_season.setdefault(season_key(text), []).append(variant)
    known = [k for k in by_season if k != "unknown"]
    episodes = {episode_of(" ".join(filter(None, [v.get("torrent_title"), v.get("label")]))) for v in variants} - {None}
    if not (len(known) > 1 or (len(known) == 1 and "unknown" in by_season) or len(episodes) >= 2):
        return None
    out = []
    for key in sorted(by_season, key=season_order):
        items = sorted(by_season[key], key=lambda v: (episode_of(v.get("torrent_title") or "") is None,
                                                       episode_of(v.get("torrent_title") or "") or 0, -(v.get("seeders") or 0)))
        out.append({"season": key, "label": season_label(key), "groups": quality_groups(items, sort=False)})
    return out


def quality_groups(variants, sort=True):
    by_bucket = {}
    for variant in variants:
        by_bucket.setdefault(variant.get("bucket") or "OTHER", []).append(variant)
    return [{"bucket": bucket, "variants": sorted(by_bucket[bucket], key=lambda v: -(v.get("seeders") or 0)) if sort else by_bucket[bucket]}
            for bucket in QUALITY_ORDER if bucket in by_bucket]

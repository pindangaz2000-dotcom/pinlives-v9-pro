from __future__ import annotations
import re
import logging
import unicodedata
from typing import List, Dict, Set, Any

logger = logging.getLogger(__name__)

# ====================================================================
# === HẰNG SỐ CHUNG VÀ REGEX (Đầy đủ) =================================
# ====================================================================
ZERO_WIDTH_RE = re.compile(r"[\u200b\u200c\u200d\ufeff\u2060]")
METADATA_RE = re.compile(r"<[^>]+>")
SYMBOL_ARMOR_RE = re.compile(r"[^a-zA-Z0-9]")
ALNUM_RE = re.compile(r"^[A-Za-z0-9]+$")
_STRIP_SPECIAL_RE = re.compile(r"[^a-zA-Z0-9]") 
_L0_URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_L0_TAG_RE = re.compile(r"@\w+")
_L0_HASHTAG_RE = re.compile(r"\#\w+")
_L0_STRIP_EDGE_RE = re.compile(r"^[^\w%\^&@\#\*$><=+\!_]+|[^A-Za-z0-9%\^&@\#\*$><=+\!_]+$")
_L0_VIET_DIACRITIC_RE = re.compile(r"[\u00c0-\u024f\u1e00-\u1ef9]")
_L0_PHONE_DIGITS_RE = re.compile(r"^\d{9,}$")
_L0_MIN_LEN = 5
_L0_MAX_LEN = 15
_L0_BLACKLIST = frozenset({
    "NOHU", "BANCA", "CSKH", "VIP", "CODE", "AUTO",
    "FREECODE", "KHUYENMAI", "BONUS", "CASINO", "SLOT",
    "GAME", "ONLINE", "JACKPOT", "BIGWIN", "FREEBET",
    "WORLDCUP2026",
})
_C168_SPECIAL_CHARS_SET = frozenset(r"%^&@#*$")
# SC88 mở rộng: bao gồm /, (, ), - ngoài các ký tự cũ; - ở đầu để literal trong char class
_SC88_SPECIAL_CHARS = r"-/()<>=+!*#@%^&$_"
_SC88_SPECIAL_CHARS_SET = frozenset("-/()<>=+!*#@%^&$_")
_ALL_SPECIAL_RAW_CHARS_SET = frozenset(r"%^&@#*$><+=!_") 

# Regex Fast Extractor cho các site dùng special char nhúng
_C168_RAW_RE = re.compile(
    # Bắt raw token 11-18 chars có ít nhất 1 special char từ set C168
    # Thực tế: codes có 1-4 special chars nhúng (hPGd$R~9s*0MBI~5 = 4 specials → 12 clean)
    # [specials]+ cho phép 2 special liên tiếp (vd: dQfV*DS^&C$AEd2k có ^& liên tiếp)
    r"(?<![A-Za-z0-9~\-])"
    r"([A-Za-z0-9]{1,12}(?:[%\\^&@\#\*$~\-]+[A-Za-z0-9]{1,12}){1,4})"
    r"(?![A-Za-z0-9])"
)
# C168 clean fallback: OCR có thể strip special chars → clean 10 hoặc 12-char alnum
_C168_CLEAN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{10}(?:[A-Za-z0-9]{2})?)(?![A-Za-z0-9])")
_SC88_RAW_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([" + _SC88_SPECIAL_CHARS + r"][A-Za-z0-9]{10}"       # leading special + 10 alnum
    r"|[A-Za-z0-9]{1,9}[" + _SC88_SPECIAL_CHARS + r"][A-Za-z0-9]{1,9}"  # middle special
    r"|[A-Za-z0-9]{10}[" + _SC88_SPECIAL_CHARS + r"])"      # 10 alnum + trailing special
    r"(?![A-Za-z0-9])")
_F8BET_RAW_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]{1,7}[%\\^&@\#\*$][A-Za-z0-9]{1,7})(?![A-Za-z0-9])")
# Regex cho F8BET video/GIF codes: 8-10-char pure alnum (không có special char)
_F8BET_VIDEO_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8,10})(?![A-Za-z0-9])")
_JUN88_RAW_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Za-z0-9]{1,5}[%\\^&@\#\*$][A-Za-z0-9]{1,5})(?![A-Za-z0-9])")

# Regex cho HI88 (pure alnum)
_HI88_CLEAN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8,13})(?![A-Za-z0-9])")
_LUU_Y_RE = re.compile(r"[Ll]\u01b0u [Yý][^\n]*\n?")
# Regex cho 8-char alnum sites (qq88, cm88, open88, ok8386, 8kbet)
_ALNUM8_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8})(?![A-Za-z0-9])")
# Regex cho f168 (8-9 char alnum) — thực tế codes dài 8-9 ký tự
_F168_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8,9})(?![A-Za-z0-9])")
# Regex cho fly88 (8-10 char)
_FLY88_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8,10})(?![A-Za-z0-9])")
# Regex cho adavawef sites (79king, okking, new88): plain invite codes 6-12 chars
_ADAVAWEF_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{6,12})(?![A-Za-z0-9])")
# Regex cho 79king short codes: 4-char uppercase alphanumeric (từ ảnh phát code)
_79KING_SHORT_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{4})(?![A-Za-z0-9])")
# Regex cho J88: đúng 8 ký tự alnum (pattern thực tế từ ảnh mẫu J88)
_J88_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{8})(?![A-Za-z0-9])")
# Regex cho 33WIN: đúng 6 ký tự UPPERCASE alphanumeric (GKXW74, MH8ND7, NCHA84...)
# Ảnh thường chứa 12 ký tự liền (GKXW74MH8ND7) → tự tách thành 2×6
_33WIN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{6})(?![A-Za-z0-9])")
_33WIN_CONCAT_RE = re.compile(r"(?<![A-Za-z0-9])([A-Z0-9]{12})(?![A-Za-z0-9])")

# Strip từ/cụm chứa dấu tiếng Việt ở mức từ (word-level)
_VIET_WORD_STRIP_RE = re.compile(
    r"\S*[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
    r"ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ]\S*")

# Regex Layer 0: Lọc rác sớm
HASHTAG_RE = re.compile(r"(?:^|(?<=\s))\#\w+", re.MULTILINE)
VIET_DIACRITICS_RE = re.compile(
    r"[àáảãạăắằẳẵặâấầẩẫậđèéẻẽẹêếềểễệìíỉĩịòóỏõọôốồổỗộơớờởỡợùúủũụưứừửữựỳýỷỹỵ"
    r"ÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ]")
LINK_RE = re.compile(r"https?://\S+", re.IGNORECASE)
DOMAIN_EXT_RE = re.compile(r"\.\w{2,4}(?:/|$)", re.IGNORECASE)
SPACED_SCHEME_RE = re.compile(r"\bhttps?\s*:\s*/\s*/\s*\S+", re.IGNORECASE)
SPACED_DOMAIN_RE = re.compile(
    r"\b(?:www\s*\.\s*)?"
    r"(?:[a-z0-9-]+\s*\.\s*)+"

    r"(?:com|vn|net|org|cc|vip|xyz|dev|io|me|tv|app)"
    r"(?:\s*/\s*\S+)?",
    re.IGNORECASE,)

# Các hằng số blacklist (Đã tinh chỉnh mở rộng từ đôi)
_VIET_NOISE_WORDS: Set[str] = {
    "ngay", "code", "nhap", "link", "kenh", "dong", "hanh", "cung", "nhan", "them", "tien", 
    "nang", "kiem", "choi", "tham", "thong", "dang", "khong", "vang", "theo", "chinh", 
    "thuc", "free", "thuong", "gioi", "dung", "hang", "tuan", "long", "biet", "bang", 
    "hinh", "khac", "usdt", "tiep", "luon", "mien", "chuc", "nong", "cuoc", "canh", 
    "tren", "duoi", "sang", "truc", "tuyen", "game", "slot", "live", "casino", 
    "online", "nohu", "banca", "thang", "thua", "cacuoc", "luck", "may", "mayman", 
    "hoan", "thanh", "vien", "dieu", "kien", "khuyen", "khung", "diem", "danh", 
    "nhat", "trao", "giai", "goc", "vip", "bonus", "km", "nap", "rut", 
    "moingay", "chinhthuc", "khuyenmai", "nhanthuong", "homnay", "ngaymai", "tatca", 
    "datcuoc", "ketnoi", "gioihan", "dambao", "chinhxac", "vuilong", "kytu", 
    "dacbiet", "trungthuong", "mathuong", "thongtin", "taiday", "vongcuoc", 
    "rutngay", "thanglon", "thuongkhung", "traotay", "trian", "donghanh", 
    "giaithuong", "guitang", "cucchat", "thatbai", "thanhcong", "toithieu", 
    "toida", "hople", "khongduoc", "taisao", "visao", "dungvay", "sailam", 
    "chungtoi", "chungta", "cacban", "khachhang", "xuixeo", "thangdam", 
    "thangto", "uudai", "danhbai", "danhbaionline", "daga", "giatri", "nhanhtay", "thamgia", 
    "cohoi", "rinhvang", "dangcho", "quatang", "cuocsong", "lienhe", "deduoc", 
    "cachnhan", "mangden", "chuongtrinh", "danhcho", "thanhvien", "hoivien", 
    "thatsu", "sukien", "sohuu", "quaynhanh", "trunggon", "trainghiem", 
    "datay", "phuhop", "nguoimoi", "nguoicu", "taychoi", "launam", 
    "dungnhip", "dadang", "chude", "nhamchan", "tyle", "nocao", "tichluy", 
    "hangchuc", "hangtram", "hangnghin", "hangty", "hapdan", "hieuung", 
    "batmat", "vongquay", "muotma", "hoitu", "lentay", "doidoi", 
    "dungluc", "thoidiem", "hientai", "danhsach", "doithuong", "dangky",
    "hoantien", "taikhoan", "chuyentien", "dichvu", "thanhtoan", "nhacai", 
    "uytin", "dudoan", "tonghop", "capnhat", "minigame", "thethao", 
    "xacminh", "matkhau", "hotro",
    # Bổ sung: từ ghép phổ biến trong post casino
    "giaitri", "matcode", "macode", "nhapma", "nhapmade", "nhapcode",
    "tangcode", "freecode", "hotcode", "newcode", "bigcode", "topcode",
    "linkvao", "linkvaomoi", "linkdang", "linknhanh", "linkchuan",
    "linktele", "linkzalo", "linkchat", "linkgroup", "linkfb",
    "trangchu", "truy cap", "truycap", "dangdang", "dangdangky",
    "xemnhieu", "xemthem", "docngay", "nhandoc", "nhanma",
    "tinnong", "tinhot", "tinmoi", "capnhatmoi", "tintuc",
    "thethaoao", "cacuocso", "trotho", "suathu", "choicung",
    "hoatieu", "hoamai", "chienthang", "quathuong", "rieng",
    "dangdang", "sangtrong", "hiendai", "doitien", "muadoi",
    "naptien", "ruttien", "napngay", "rutngaylap", "dongbank",
    "maythu", "gioithieu", "banthan", "minhchinh", "xincao",
    "nguoithan", "banthenhan", "vovibe", "anhchi", "anhbro",
    "tinvui", "tinphat", "tintot", "tinbuon", "tinnhe",
    "hotdeal", "topdeal", "bestdeal", "newdeal", "gooddeal",
    "probet", "topbet", "hotbet", "newbet", "bestbet",
    "provip", "topvip", "hotvip", "newvip", "supervip",
    "winbet", "wincode", "winpromo", "winbonus",
    "comment", "codefree", "minigamesancode", "gg88km", "start", "tart",
    # Thêm: từ ghép khách + vip/quy/hang
    "khachvip", "khachquy", "khachhangvip", "thanhvien88", "vipcode",
    "maquatang", "magiamgia", "coupon", "voucher88",
    "tienle", "tienthuong", "tienphat", "tienmat",
    "tinhtrang", "ketqua", "xemsau", "saungay",
    "khuyenmai88", "kmngay", "uudai88", "thuongnap",
    "thang88", "thang168", "thua88", "may88", "lucky88",
    "congdong", "fanpage", "channel", "nhom", "group",
    "dienthoai", "dienthoaivip", "hotline", "phuong",
    "cauthubong", "giaidau", "vondau", "volevang",
    "tinnoibat", "noibat", "phubep", "tuvan",
    # Từ ghép marketing / casino content thường gặp trong post (FP mới)
    "diemdanh", "nhacaiuytin", "baomatssl", "thuonglon",
    "naprutnhanh", "choilathang", "cohoivang", "slotgame",
    # Tên địa danh casino Philippines hay xuất hiện trong quảng cáo
    "cagayan", "freeport",
    # Tên CLB bóng đá (SHBET hay quảng cáo kèm ảnh đội bóng)
    "atletico", "barcelona", "madrid", "chelsea", "arsenal",
    "liverpool", "juventus", "dortmund",
}

# Sorted by length descending for greedy prefix matching (longest match first)
_VIET_NOISE_WORDS_SORTED: list = sorted(_VIET_NOISE_WORDS, key=len, reverse=True)

# Pre-compiled regex cho _is_viet_noise_with_digits — O(1) C-NFA thay vì O(n_words) Python loop
# Bắt: word + tối đa 4 chữ số ở cuối, HOẶC tối đa 4 chữ số ở đầu + word
# Bổ sung: word + suffix phổ biến (online, vip, pro, live, game, ...) — vd "danhbaionline", "nhacaivip"
_NW_JOINED = '|'.join(map(re.escape, _VIET_NOISE_WORDS_SORTED))
_NOISE_ALPHA_SUFFIX = r'(?:online|vip|pro|live|game|bet|play|win|app|web|link|club|top|hot|new|vn|com)?\d{0,4}'
# OPTIMIZATION: merge 2 regex thành 1 để tránh 2 lần gọi match
_NOISE_COMBINED_RE = re.compile(
    r'^(?:\d{0,4}(?:' + _NW_JOINED + r')|(?:' + _NW_JOINED + r')' + _NOISE_ALPHA_SUFFIX + r')$',
    re.IGNORECASE
)
del _NW_JOINED  # giải phóng bộ nhớ chuỗi trung gian

def _is_viet_noise_with_digits(tok_lower: str) -> bool:
    """True nếu token là từ Tiếng Việt không dấu + tối đa 4 chữ số ở đầu/cuối."""
    return bool(_NOISE_COMBINED_RE.match(tok_lower))

_SOCIAL_NOISE_WORDS: Set[str] = {
    "facebook", "fb", "youtube", "tiktok", "instagram", "zalo",
    "telegram", "twitter", "discord",
    # Tech/brand words that appear in livestream UI (device info, notifications...)
    "bluetooth", "internet", "airplane", "connected", "settings", "android",
    "iphone", "samsung", "xiaomi", "bandwith", "download", "uploaded",
    "password", "username", "nickname", "register", "overview",
    "homepage", "exchange", "transfer", "withdraw", "withdrew",
    "facetime", "calendar", "whatsapp", "messages", "facebook",
    "appstore", "snapchat", "linkedin", "bookmark", "backpack",
    "keyboard", "touchpad", "speakers", "monitor",
    # Vietnamese words (ASCII transliterated) that slip through trap-char cleaner
    "mobile", "khich", "thuong", "khuyen", "nhan", "code", "bonus",}

_NO_DIGIT_SUBSTRING_BLACKLIST: Set[str] = {
    "facebook", "youtube", "tiktok", "instagram", "telegram",
    "twitter", "discord", "zalo", "minigame", "casino",
    "game", "slot", "online", "nohu", "banca", "baccarat",
    "poker", "xito", "maubinh", "tienlen", "thethao", "cacuoc",
    "luck", "mayman", "cskh", "hotro", "lienhe", "matkhau",
    "thongtin", "xacminh", "tinnhan", "cuocgoi", "goitinnhan", "otp",
    # Thêm: prefix thường gặp trong link/marketing, không bao giờ là code
    "link", "www", "http", "club", "web", "app", "site",
    # Tech words from device UI / notification bar
    "bluetooth", "internet", "airplane", "android", "iphone", "samsung",
    "worldcup",
    # Tên địa danh casino / CLB bóng đá → không bao giờ là code thật
    "cagayan", "freeport", "atletico", "barcelona", "madrid",
    "chelsea", "arsenal", "liverpool", "juventus", "dortmund",
    # Từ ghép tiếng Việt xuất hiện trong ảnh quảng cáo
    "slotgame", "diemdanh", "nhacai", "uytin", "baomat",
}
# Pre-compiled — thay thế any(...) loop trong _hi88_code_validate và extract_codes_4_layers
_NO_DIGIT_SUBS_BL_RE = re.compile(
    '|'.join(map(re.escape, sorted(_NO_DIGIT_SUBSTRING_BLACKLIST, key=len, reverse=True))),
    re.IGNORECASE
)

_ANY_SUBSTRING_BLACKLIST: Set[str] = {
    "scam", "succac", "trom", "cocain", "transaction", "freespin",
    "profit", "withdraw", "deposit", "jackpot", "scatter",
    "wildcard", "payline", "spinwin", "bigwin",
    # Link/URL fragments - "linkF8BET", "linkcm88" type false positives
    "link",
    # Common marketing suffix/infix: "pro88bet", "hotbet", "winbet"
    "bet",
    # Event keywords — never a real code
    "worldcup",
}
# Pre-compiled — thay thế any(...) loop ở 9+ call sites
_ANY_SUBS_BL_RE = re.compile(
    '|'.join(map(re.escape, sorted(_ANY_SUBSTRING_BLACKLIST, key=len, reverse=True))),
    re.IGNORECASE
)
# Pattern: 4+ ký tự giống nhau liên tiếp — code thật không có (AAAA, 1111, aaaa...)
_REPEATED_CHAR_RE = re.compile(r'(.)\1{3,}')

KNOWN_SITE_TOKENS: Set[str] = {
    "sc88", "c168", "f168", "fly88", "qq88", "open88", "cm88",
    "jun88", "j88", "ok8386", "f8bet", "hi88", "8kbet", "79king",
    "okking", "new88", "ok9", "mb66", "rr88", "xx88", "gg88", "mm88",
    "shbet", "go88", "789bet", "33win",}
KNOWN_SITE_TOKENS_U = tuple(sorted({s.upper() for s in KNOWN_SITE_TOKENS if s}, key=len, reverse=True))
# Pre-compiled regex cho substring check site tokens (thay thế for-loop O(n))
_KNOWN_SITE_TOKENS_RE = re.compile(
    "|".join(re.escape(t) for t in KNOWN_SITE_TOKENS_U if t),
    re.IGNORECASE,
)

# Regex cho các mẫu marketing
_MARKETING_WITH_YEAR_RE = re.compile(
    r"^(?:NGAY|THANG|NAM|TET|HAPPY|NEWYEAR|YEAR|NOHU|BANCA|NOIHOI|VIP)\d{2,6}$",
    re.IGNORECASE,)
_MARKETING_UPPER_WITH_DIGITS_RE = re.compile(
    r"^(?:DUDOAN|BONUS|THUONG|THUONGVIP|VIP|KM|NOHU|BANCA|SLOT|CASINO|NAP|RUT|THEthao|GAME|FREECODE)[A-Z]*\d{1,6}$",
    re.IGNORECASE,)
_GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE = re.compile(
    r"^(?:NOHU|BANCA|CASINO|SLOT|GAME|ONLINE|LUCK|LUCKY|MAYMAN|THANG|THUA|CUOC|CACUOC|CHOI|KHUYENMAI|THUONG|BONUS|VIP|HAPPY|HOT|WIN|TOP|PRO)"
    r"[A-Z0-9]*\d[A-Z0-9]*$",
    re.IGNORECASE,)
_PLACEHOLDER_PROMO_LABEL_RE = re.compile(
    r"^(?:MATG|MATANG|MAGIFT|VOUCHER|KHUYENMAI|FREECODE|CODE|KMNT|KM|VIP|MASC)\d{1,4}$",
    re.IGNORECASE,)
_YEAR_SUFFIX_RE = re.compile(r"(?:19|20)\d{2}$")
# All-uppercase + site name combo: VIP88BET, TOPBET88, WINF8BET → marketing
_ALL_UPPER_SITE_COMBO_RE = re.compile(
    r"^(?:VIP|TOP|WIN|HOT|NEW|PRO|BET|LINK|BEST|SUPER|MEGA|KING|LUCKY|FREE)"
    r"(?:[A-Z0-9]{2,12})$"
)


# === LAYER 0: CÁC HÀM XỬ LÝ TOKEN CƠ BẢN (Được giữ nguyên) ===
def extract_codes_layer0(text: str) -> list[str]:
    """Tách các token chỉ gồm chữ cái và số, loại bỏ ký tự đặc biệt và dấu cách."""
    tokens = re.split(r"\s+", text)
    return [token for token in tokens if token.isalnum() and len(token) > 0]

def clean_code_for_site(code: str, site_id: str = "") -> str:
    """Làm sạch + validate theo site để giảm false-positive trước khi submit."""
    raw = (code or "").strip()
    if not raw:
        return ""
    site_key = (site_id or "").strip().lower()
    if site_key:
        if site_key in {"c168", "oklive"}:
            stripped = ''.join(c for c in raw if c.isalnum())
            return _c168_clean_validate(stripped) if stripped else ""
        if site_key == "sc88":
            stripped = ''.join(c for c in raw if c.isalnum())
            if len(stripped) == 10:
                validated = _c168_clean_validate(stripped)
                if validated:
                    return validated
            return ""
        if site_key == "f168":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _f168_validate(stripped) if stripped else ""
        if site_key == "fly88":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _fly88_validate(stripped) if stripped else ""
        if site_key == "ok8386":
            stripped = ''.join(c for c in raw if c.isalnum())
            if not stripped:
                return ""
            return _alnum8_validate(stripped) or _ok8386_trap_validate(stripped)
        if site_key in _ALNUM8_SITES:
            stripped = ''.join(c for c in raw if c.isalnum())
            return _alnum8_validate(stripped) if stripped else ""
        if site_key in {"j88"}:
            stripped = ''.join(c for c in raw if c.isalnum())
            return _j88_validate(stripped) if stripped else ""
        if site_key == "79king":
            stripped = ''.join(c for c in raw if c.isalnum())
            if not stripped:
                return ""
            # 79king: chấp nhận short 4-char codes (từ ảnh) + long 6-12 (text)
            return _79king_validate(stripped)
        if site_key in {"okking", "new88", "ok9", "go88", "789bet"}:
            stripped = ''.join(c for c in raw if c.isalnum())
            return _adavawef_validate(stripped) if stripped else ""
        if site_key == "8kbet":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _8kbet_validate(stripped) if stripped else ""
        if site_key == "33win":
            stripped = ''.join(c for c in raw if c.isalnum()).upper()
            return _33win_validate(stripped) if stripped else ""
        if site_key == "open88":
            stripped = ''.join(c for c in raw if c.isalnum())
            if not stripped:
                return ""
            return _open88_special_validate(stripped) or _open88_short_validate(stripped) or _alnum8_validate(stripped)
        if site_key == "cm88":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _cm88_validate(stripped) if stripped else ""
        if site_key == "f8bet":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _f8bet_video_validate(stripped) if stripped else ""
        if site_key == "hi88":
            stripped = ''.join(c for c in raw if c.isalnum())
            return _hi88_code_validate(stripped) if stripped else ""
        try:
            matched = _extract_codes_for_site_raw(raw, site_key)
            if matched:
                return matched[0]
        except Exception:
            pass
        stripped = ''.join(c for c in raw if c.isalnum())
        return stripped if stripped else ""
    return ''.join(c for c in raw if c.isalnum())

# === PIPELINE CŨ (Được giữ nguyên) ===

def layer1_strip_hidden(text: str) -> str:
    """Xóa link, hashtag, zero-width và metadata HTML."""
    clean = LINK_RE.sub(" ", text)
    clean = SPACED_SCHEME_RE.sub(" ", clean)
    clean = SPACED_DOMAIN_RE.sub(" ", clean)
    clean = HASHTAG_RE.sub(" ", clean)
    clean = ZERO_WIDTH_RE.sub("", clean)
    clean = METADATA_RE.sub(" ", clean)
    return clean

def _layer1_no_hashtag(text: str) -> str:
    """Như layer1_strip_hidden nhưng KHÔNG xóa hashtag.
    Dùng cho C168/SC88 vì codes có thể bắt đầu bằng '#' (bị nhầm là hashtag)."""
    clean = LINK_RE.sub(" ", text)
    clean = SPACED_SCHEME_RE.sub(" ", clean)
    clean = SPACED_DOMAIN_RE.sub(" ", clean)
    # KHÔNG strip HASHTAG_RE — '#eHiewhjJEX' là code, không phải hashtag thật
    clean = ZERO_WIDTH_RE.sub("", clean)
    clean = METADATA_RE.sub(" ", clean)
    return clean

def layer2_split_chunks(text: str) -> List[str]:
    """Tách chuỗi thành các token dựa trên khoảng trắng/xuống dòng."""
    parts = re.split(r"[\s\n\r\t]+", text)
    return [item.strip() for item in parts if item.strip()]

def layer3_strip_armor(token: str) -> str:
    return SYMBOL_ARMOR_RE.sub("", token)

def layer4_shape_guard(token: str) -> bool:
    """Logic kiểm tra hình dạng code cũ (chuẩn cho 4-layer fallback)."""
    if not ALNUM_RE.fullmatch(token): return False
    n = len(token)
    if n < 6: return False
    if n > 16: return False  # không site nào có code dài hơn 16 ký tự clean

    # Loại chuỗi lặp: AAAA, 1111, aaBBaaBB... (4+ ký tự giống nhau liên tiếp)
    if _REPEATED_CHAR_RE.search(token): return False

    # Loại noise words bất kể case (Comment, Codefree, Minigamesancode, etc.)
    token_lower = token.lower()
    if token_lower in _VIET_NOISE_WORDS or token_lower in _SOCIAL_NOISE_WORDS:
        return False
    if _is_viet_noise_with_digits(token_lower):
        return False

    has_alpha = any(ch.isalpha() for ch in token)
    has_digit = any(ch.isdigit() for ch in token)

    if has_alpha and not has_digit:
        is_all_lower = token.lower() == token
        # Cho phép all-upper alpha (GG88/MM88: TYEJBM) — chỉ block all-lower
        if is_all_lower: return False
            
    if token.isdigit():
        if len(set(token)) == 1: return False
        digits = [int(c) for c in token]
        diffs = [digits[i+1] - digits[i] for i in range(len(digits)-1)]
        if len(set(diffs)) == 1 and abs(diffs[0]) == 1: return False
        normalized = token.lstrip("0") or "0"
        if len(normalized) >= 5 and normalized.endswith("000"): return False
            
    return True

# === EXTRACTOR 4-LAYER CHÍNH (Fallback - Được giữ nguyên) ===
def extract_codes_4_layers(text: str) -> List[str]:
    if not text or len(text) < 5:  # code tối thiểu 6 chars
        return []
    stage1 = layer1_strip_hidden(text)
    stage2 = layer2_split_chunks(stage1)
    seen: set = set()
    codes: List[str] = []

    for raw_token in stage2:
        # Fast-path C168 (13-char hoặc 11-char raw với special char C168)
        if len(raw_token) in {13, 11} and any(c in _C168_SPECIAL_CHARS_SET for c in raw_token):
            fast = _c168_raw_to_clean(raw_token)
            if fast and fast not in seen:
                seen.add(fast)
                codes.append(fast)
            continue

        # Fast-path SC88 (raw 10-11 char với special char SC88 mở rộng, chưa match C168)
        if len(raw_token) in {10, 11} and any(c in _SC88_SPECIAL_CHARS_SET for c in raw_token):
            fast = _sc88_raw_to_clean(raw_token)
            if fast and fast not in seen:
                seen.add(fast)
                codes.append(fast)
            continue
        
        if raw_token.startswith("\#"): continue
        if VIET_DIACRITICS_RE.search(raw_token): continue
        if DOMAIN_EXT_RE.search(raw_token): continue
        if raw_token.strip().lower() in _SOCIAL_NOISE_WORDS: continue
            
        token = layer3_strip_armor(raw_token)
        if not token: continue

        token_lower = token.lower()
        token_has_digit = any(ch.isdigit() for ch in token)

        if _ANY_SUBS_BL_RE.search(token_lower): continue
        if len(token) >= 10 and "nohu" in token_lower: continue
        if len(token) >= 14 and _NO_DIGIT_SUBS_BL_RE.search(token_lower): continue
        if (not token_has_digit) and len(token) >= 18: continue
        if (not token_has_digit) and _NO_DIGIT_SUBS_BL_RE.search(token_lower): continue

        upper_token = token.upper()
        # Dùng pre-compiled regex (O(1)) thay for-loop O(n_sites) để kiểm tra site token
        if _KNOWN_SITE_TOKENS_RE.search(token):
            continue

        if _MARKETING_WITH_YEAR_RE.fullmatch(upper_token): continue
        if token == upper_token and _MARKETING_UPPER_WITH_DIGITS_RE.fullmatch(upper_token): continue
        if token == upper_token and _ALL_UPPER_SITE_COMBO_RE.fullmatch(upper_token): continue
        if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(token): continue
        if token.lower() in _VIET_NOISE_WORDS: continue
        if _is_viet_noise_with_digits(token.lower()): continue
            
        if not token: continue
        if not layer4_shape_guard(token): continue
            
        if token in seen: continue
        seen.add(token)
        codes.append(token)

    return codes

# === RAW-TOKEN VALIDATORS (Site-specific - Được giữ nguyên) ===
def _raw_to_clean_generic(raw_tok: str, expected_stripped_len: int,
                          special_set: frozenset,
                          require_mixed_case: bool = True,
                          require_has_digit: bool = False) -> str:
    """Validator chung cho raw token có nhúng đúng 1 special char."""
    special_count = sum(1 for c in raw_tok if c in special_set)
    if special_count != 1: return ""
    cleaned = "".join(c for c in raw_tok if c.isalnum())
    if expected_stripped_len > 0 and len(cleaned) != expected_stripped_len: return ""
    if len(cleaned) < 6: return ""
    if cleaned.isdigit(): return ""
    if require_mixed_case:
        if cleaned.upper() == cleaned or cleaned.lower() == cleaned: return ""
    if require_has_digit and not any(c.isdigit() for c in cleaned): return ""
    return cleaned

_C168_SPECIAL_CHARS_EXTENDED = frozenset("%^&@#*$~-")

def _c168_raw_to_clean(raw_tok: str) -> str:
    """C168: raw 11-18 chars có 1-4 special → strip → clean 10 hoặc 12 chars, mixed-case."""
    cleaned = "".join(c for c in raw_tok if c.isalnum())
    if len(cleaned) not in (10, 12): return ""
    if len(cleaned) < 6: return ""
    if cleaned.isdigit(): return ""
    if not any(c.isdigit() for c in cleaned): return ""
    if cleaned.upper() == cleaned or cleaned.lower() == cleaned: return ""
    return cleaned

def _c168_clean_validate(tok: str) -> str:
    """C168 OCR fallback: clean 10/12-char alnum khi OCR đã strip special chars."""
    if len(tok) not in (10, 12): return ""
    if not tok.isalnum(): return ""
    if tok.isdigit(): return ""
    if not any(c.isdigit() for c in tok): return ""
    if tok.upper() == tok or tok.lower() == tok: return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    return tok

def _sc88_raw_to_clean(raw_tok: str) -> str:
    """SC88: 11 raw → 10 stripped, mixed-case."""
    return _raw_to_clean_generic(raw_tok, 10, _SC88_SPECIAL_CHARS_SET, require_mixed_case=True, require_has_digit=True)

def _f8bet_raw_to_clean(raw_tok: str) -> str:
    """F8BET: 9 raw → 8 stripped, mixed-case + có ít nhất 1 chữ số."""
    return _raw_to_clean_generic(raw_tok, 8, _C168_SPECIAL_CHARS_SET, require_mixed_case=True, require_has_digit=True)

def _f8bet_video_validate(tok: str) -> str:
    """F8BET clean token: 8-10-char pure alnum, mixed-case."""
    if not (8 <= len(tok) <= 10): return ""
    if not tok.isalnum(): return ""
    if not (any(c.isupper() for c in tok) and any(c.islower() for c in tok)): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    # Dùng substring search thay == để bắt "linkF8BET", "linkcm88" etc.
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.match(tok): return ""
    if _MARKETING_UPPER_WITH_DIGITS_RE.match(tok): return ""
    return tok

def _hi88_code_validate(tok: str) -> str:
    """HI88: validate token thuần alnum 8–13 chars, mixed-case.
    Codes thực tế: FHRmRELl, kiEu9EeE, kXOU6HUk, wBDZEC4M (8 chars mixed).
    Codes dài (≥10 chars) phải có digit — pure-alpha dài = English words / OCR noise.
    """
    if not (8 <= len(tok) <= 13): return ""
    if tok.isdigit(): return ""
    if not (any(c.isupper() for c in tok) and any(c.islower() for c in tok)): return ""
    # Codes ≥ 10 chars pure-alpha = rất cao xác suất là noise (Gainejhiroig, DHi8stangoua...)
    if len(tok) >= 10 and not any(c.isdigit() for c in tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if tok_lower in _VIET_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    # Dùng substring search
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if not any(c.isdigit() for c in tok):
        if _NO_DIGIT_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.match(tok): return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.match(tok): return ""
    if _MARKETING_UPPER_WITH_DIGITS_RE.match(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    return tok

def _jun88_raw_to_clean(raw_tok: str) -> str:
    """JUN88: 7 raw → 6 stripped, mixed-case (pure-alpha OK)."""
    return _raw_to_clean_generic(raw_tok, 6, _C168_SPECIAL_CHARS_SET, require_mixed_case=True, require_has_digit=False)

def _generic_raw_to_clean(raw_tok: str, min_stripped: int = 8) -> str:
    """Generic: bất kỳ raw với 1 special, sau strip >= min_stripped chars."""
    _ALL_SPECIAL = frozenset("%^&@#*$><+=!_")
    return _raw_to_clean_generic(raw_tok, 0, _ALL_SPECIAL, require_mixed_case=True)

def _f168_validate(tok: str) -> str:
    """F168: 8-9 alnum, không pure-digit, không year suffix, phải mixed-case."""
    if not (8 <= len(tok) <= 9): return ""
    if tok.isdigit(): return ""
    if _YEAR_SUFFIX_RE.search(tok): return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    # Yêu cầu mixed-case (không all-upper, không all-lower) cho tất cả
    if tok.upper() == tok or tok.lower() == tok: return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    # Thêm: nếu không có chữ số → kiểm tra substring blacklist (bắt "Baccarat", "Freeport", ...)
    if not any(c.isdigit() for c in tok) and _NO_DIGIT_SUBS_BL_RE.search(tok_lower): return ""
    return tok

def _fly88_validate(tok: str) -> str:
    """FLY88: 8-10 alnum, cần cả chữ+số, không all-upper, không year suffix."""
    if not (8 <= len(tok) <= 10): return ""
    if tok.isdigit(): return ""
    has_alpha = any(c.isalpha() for c in tok)
    has_digit = any(c.isdigit() for c in tok)
    if not (has_alpha and has_digit): return ""
    if _YEAR_SUFFIX_RE.search(tok): return ""
    if tok.upper() == tok: return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""  # thay thế for-loop O(n_sites)
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    return tok

def _alnum8_validate(tok: str) -> str:
    """Generic 8-char alnum cho qq88, open88, ok8386, 8kbet.
    Loại: pure-digit, pure-alpha all-same-case, placeholder, blacklist.
    """
    if len(tok) != 8: return ""
    if tok.isdigit(): return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    if tok.isalpha() and (tok.upper() == tok or tok.lower() == tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    # Chặn all-upper marketing combos: VIP88BET, TOP88WIN, HOTCODE8 etc.
    if tok == tok.upper() and _ALL_UPPER_SITE_COMBO_RE.fullmatch(tok): return ""
    if tok == tok.upper() and _MARKETING_UPPER_WITH_DIGITS_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    return tok

# Blacklist bổ sung cho adavawef all-uppercase codes
_ADAVAWEF_EXTRA_BLACKLIST: frozenset = frozenset({
    "jackpot", "scatter", "wildcard", "payline", "spinwin",
    "freespin", "deposit", "withdraw", "profit", "minigame",
    "trunglon", "thanglon", "vuiplay", "chuongtri", "sukien",
    "tangcode", "nhapcode", "freecode", "macode", "giaithuong",
    # Xổ số miền / nạp USDT noise — "usdt" substring block USDTL2 (OCR false positive)
    "napdau", "usdt", "xsme", "xsmb", "xsmn", "xstth", "xstt",
    # Link/URL fragments
    "link", "linkvao", "linkdang", "linktele", "linkzalo",
    # Common marketing prefixes/suffixes that are never real codes
    "vip", "pro", "hot", "top", "win", "new", "bet",
})
# Pre-compiled — thay thế any(...) loop trong _adavawef_validate và _j88_validate
_ADAVAWEF_EXTRA_BL_RE = re.compile(
    '|'.join(map(re.escape, sorted(_ADAVAWEF_EXTRA_BLACKLIST, key=len, reverse=True))),
    re.IGNORECASE
)

def _adavawef_validate(tok: str) -> str:
    """Adavawef sites (j88, 79king, okking, new88): plain invite codes 6-12 chars.
    Yêu cầu: có ít nhất 1 chữ số HOẶC mixed-case (có cả hoa lẫn thường).
    Pure-alpha all-uppercase như "ABCVIP", "TOPUP", "PROMO" → noise, loại bỏ.
    Pure-alpha title-case ("Atletico", "Cagayan") hoặc 2-capital compound ("SlotGame") → noise.
    """
    if not (6 <= len(tok) <= 12): return ""
    if tok.isdigit(): return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _ADAVAWEF_EXTRA_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    # Pure-alpha: yêu cầu mixed-case (có cả chữ hoa lẫn chữ thường)
    if tok.isalpha():
        upper_count = sum(1 for c in tok if c.isupper())
        # Reject title-case (1 upper: "Atletico") và 2-capital compound ("SlotGame", "ThuongLon")
        # Code thật dạng pure-alpha thường có >= 3 uppercase rải đều (vd "NqBrMpFz")
        if upper_count < 3:
            return ""
        # Cũng kiểm tra no-digit substring blacklist cho pure-alpha
        if _NO_DIGIT_SUBS_BL_RE.search(tok_lower):
            return ""
    return tok

def _extract_smart(text: str, raw_re: re.Pattern, validator) -> List[str]:
    """Helper: quét raw_re, áp validator, trả list codes đã dedup.
    OPTIMIZATION: fast-path cho Gemini output (single clean token, no spaces/newlines)."""
    if not text or len(text) < 4:
        return []
    # Fast-path: Gemini thường trả về 1 token sạch trên 1 dòng → skip heavy stripping
    stripped = text.strip()
    if stripped and ' ' not in stripped and '\n' not in stripped and len(stripped) <= 20:
        # Single token — chạy regex+validator trực tiếp, bỏ qua ZW/LINK/HASHTAG stripping
        m = raw_re.search(stripped)
        if m:
            c = validator(m.group(1))
            if c:
                return [c]
        # Không match → fall through to full scan (phòng case có noise ẩn)
    clean_text = ZERO_WIDTH_RE.sub("", text)        # ZW chars xen vào token → phá vỡ regex
    clean_text = LINK_RE.sub(" ", clean_text)
    clean_text = HASHTAG_RE.sub(" ", clean_text)
    clean_text = _LUU_Y_RE.sub(" ", clean_text)
    seen: set = set()
    codes: List[str] = []
    for m in raw_re.finditer(clean_text):
        c = validator(m.group(1))
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes

def extract_codes_c168_smart(text: str) -> List[str]:
    """C168: raw 13-char (1 special %^&@\#\*$) → 12-char mixed-case. Hỗ trợ cả 11 -> 10.
    Fallback: OCR có thể strip special chars → thử clean 10/12-char alnum."""
    result = _extract_smart(text, _C168_RAW_RE, _c168_raw_to_clean)
    if result:
        return result
    # OCR fallback: special chars bị strip → clean alnum 10/12 chars
    return _extract_smart(text, _C168_CLEAN_RE, _c168_clean_validate)

def extract_codes_sc88_smart(text: str) -> List[str]:
    """SC88: raw 11-char (1 special từ ><+=!*#@%^&_) → 10-char mixed-case.
    Fallback: text thuần từ Telegram (không qua ảnh) → clean 10-char alnum (giống C168).
    Log thực tế: codes SC88 là pure 10-char alnum (uwwyh4JVxr, Jedv9vDXuf...)."""
    result = _extract_smart(text, _SC88_RAW_RE, _sc88_raw_to_clean)
    if result:
        return result
    # Text fallback: SC88 post thuần text → 10-char alnum (cùng format C168)
    return _extract_smart(text, _C168_CLEAN_RE, _c168_clean_validate)

def extract_codes_f8bet_smart(text: str) -> List[str]:
    """F8BET: raw 9-char (1 special %^&@\\#\\*$) → 8-char mixed-case; fallback 10-char video codes."""
    result = _extract_smart(text, _F8BET_RAW_RE, _f8bet_raw_to_clean)
    if result:
        return result
    # F8BET video/GIF: codes are 10-char pure alnum (không có special char)
    seen: set = set()
    codes: List[str] = []
    for m in _F8BET_VIDEO_RE.finditer(text):
        c = _f8bet_video_validate(m.group(1))
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes

def extract_codes_hi88_smart(text: str) -> List[str]:
    """HI88: codes thuần [A-Za-z0-9]{8,13}, mixed-case."""
    clean = layer1_strip_hidden(text) 
    clean = _VIET_WORD_STRIP_RE.sub(" ", clean)
    clean = _LUU_Y_RE.sub(" ", clean)
    seen: set[str] = set()
    codes: List[str] = []
    for m in _HI88_CLEAN_RE.finditer(clean):
        c = _hi88_code_validate(m.group(1))
        if c and c not in seen:
            seen.add(c)
            codes.append(c)
    return codes

def extract_codes_jun88_smart(text: str) -> List[str]:
    """JUN88: raw 7-char (1 special %^&@\#\*$) → 6-char mixed-case."""
    return _extract_smart(text, _JUN88_RAW_RE, _jun88_raw_to_clean)

def extract_codes_f168_smart(text: str) -> List[str]:
    """F168: thuần alnum 8-9 char, có chữ, không year suffix."""
    return _extract_smart(text, _F168_RE, _f168_validate)

def extract_codes_fly88_smart(text: str) -> List[str]:
    """FLY88: alnum 8-10 char, cần cả chữ+số, không all-upper."""
    return _extract_smart(text, _FLY88_RE, _fly88_validate)

def extract_codes_alnum8_smart(text: str) -> List[str]:
    """Generic 8-char alnum: qq88, open88, ok8386 (không gồm 8kbet nữa)."""
    return _extract_smart(text, _ALNUM8_RE, _alnum8_validate)

def _8kbet_validate(tok: str) -> str:
    """8KBET: code đúng 8 ký tự alnum thuần.
    Hỗ trợ mã từ banner 8KBET thực tế:
    - Mã có chữ + số: PSr53gP2, 4fGGH6vF, zkm2x5sb, kaer4pab.
    - Mã thuần chữ mixed-case: UMvWdknA, shiBPhIr, lTchFdVf, VHSVCsBD, BfFXKtYG, WJTDhUMF, UrrErmla, Hzelcecz.
    """
    if len(tok) != 8: return ""
    if not tok.isalnum(): return ""
    if tok.isdigit(): return ""
    
    # Nếu thuần chữ: bắt buộc phải mixed-case (có chữ HOA + thường)
    if tok.isalpha():
        has_upper = any(c.isupper() for c in tok)
        has_lower = any(c.islower() for c in tok)
        if not (has_upper and has_lower): return ""
        
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    return tok

def extract_codes_8kbet_smart(text: str) -> List[str]:
    """8KBET: code đúng 8 ký tự mixed-case alnum, không check 'bet' substring."""
    return _extract_smart(text, _ALNUM8_RE, _8kbet_validate)

def _j88_validate(tok: str) -> str:
    """J88: code ĐÚNG 8 ký tự, mixed case (có cả HOA lẫn thường), có ít nhất 1 chữ số.
    Pattern từ ảnh mẫu thực tế: 1tp2cVzW, AL5SrHuU, cAFuW5s4, 6DeWwz9m...
    - Loại all-caps+digit (PHATONG88, ABCDE123) → phải có cả upper lẫn lower
    - Loại độ dài ≠ 8 (WELCOME5=7, PHATONG88=9)
    - Loại noise tiếng Việt (DATCUOC01...)
    """
    if len(tok) != 8: return ""
    if tok.isdigit(): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _ADAVAWEF_EXTRA_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    if _GAMBLE_MARKETING_WORDS_WITH_DIGITS_RE.fullmatch(tok): return ""
    # Phải có ít nhất 1 chữ số
    if not any(c.isdigit() for c in tok): return ""
    # Phải mixed case: có cả HOA lẫn thường (random code J88 luôn mixed)
    has_upper = any(c.isupper() for c in tok)
    has_lower = any(c.islower() for c in tok)
    if not (has_upper and has_lower): return ""
    return tok

def extract_codes_j88_smart(text: str) -> List[str]:
    """J88: codes đúng 8 ký tự mixed-case-with-digit (pattern từ ảnh mẫu thực tế)."""
    return _extract_smart(text, _J88_RE, _j88_validate)


# === 33WIN EXTRACTOR ===
# Code format: đúng 6 ký tự UPPERCASE alphanumeric (vd: GKXW74 MH8ND7 NCHA84 MNA8WM)
# Ảnh thường chứa 12 ký tự liền (GKXW74MH8ND7) → tách thành 2×6 codes
# Tất cả uppercase — không có lowercase, không có special chars

# Blacklist prefix/suffix thường gặp trong ảnh marketing 33WIN
_33WIN_BLACKLIST_RE = re.compile(
    r"^(?:WIN|CODE|FREE|VIP|HOT|NEW|TOP|BET|GIFT|BONUS|LUCKY|EVENT|PROMO|33WIN|WIN33)$",
    re.IGNORECASE,
)

def _33win_validate(tok: str) -> str:
    """33WIN: code đúng 6 ký tự, toàn UPPERCASE + digit (không có lowercase).
    Ví dụ thực tế: GKXW74, MH8ND7, NCHA84, MNA8WM.
    Loại: thuần số, thuần chữ all-same (AAAAAA), blacklist marketing.
    """
    if len(tok) != 6:
        return ""
    if not tok.isalnum():
        return ""
    if tok.isdigit():
        return ""
    # Phải toàn uppercase (không có chữ thường)
    if tok != tok.upper():
        return ""
    # Phải có ít nhất 1 chữ cái
    if not any(c.isalpha() for c in tok):
        return ""
    # Loại chuỗi lặp (AAAAAA, 111111)
    if _REPEATED_CHAR_RE.search(tok):
        return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS:
        return ""
    if _33WIN_BLACKLIST_RE.fullmatch(tok):
        return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok):
        return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok):
        return ""
    if _ADAVAWEF_EXTRA_BL_RE.search(tok_lower):
        return ""
    return tok


def _33win_is_code_drop_post(text: str) -> bool:
    """33WIN: text chỉ dùng để nhận diện đây có phải post phát code hay không.

    Không dùng text body/caption để submit vì thường chứa ví dụ kiểu:
    - "VÍ DỤ: 0F0OV9KOR2L2"
    - "✅Nhập: 0F0OV9  ✅Nhập: KOR2L2"
    Code thật cần lấy từ ảnh OCR.
    """
    n = layer1_strip_hidden(text or "").lower()
    if not n:
        return False
    _markers = (
        "33win", "code free", "code vip", "nhap code", "nhập code",
        "inputcode", "qua ve tay", "quà về tay", "nhan lien tay", "nhận liền tay",
    )
    return any(m in n for m in _markers)


def extract_codes_33win_smart(text: str, source: str = "ocr") -> List[str]:
    """33WIN: extract codes 6 ký tự UPPERCASE từ OCR; text chỉ để xác thực post.

    Đặc biệt: ảnh thường chứa 12 ký tự liền (GKXW74MH8ND7) → tách thành 2 codes.
    Logic:
    1. Tìm 12-char UPPERCASE concat → tách thành 2×6 nếu cả 2 part validate.
    2. Tìm 6-char UPPERCASE codes đơn lẻ.
    Không fallback 4-layer vì format rất cụ thể (all-uppercase 6-char).

    source:
    - "ocr": cho phép extract code từ ảnh OCR
    - "text": chỉ xác thực đây là post phát code; KHÔNG lấy code từ text/caption
    """
    if source != "ocr":
        _33win_is_code_drop_post(text)
        return []

    clean_text = layer1_strip_hidden(text)
    seen: set = set()
    results: List[str] = []

    # Bước 1: Quét 12-char concat → tách 2×6
    for m in _33WIN_CONCAT_RE.finditer(clean_text):
        raw = m.group(1)
        p1, p2 = raw[:6], raw[6:]
        v1 = _33win_validate(p1)
        v2 = _33win_validate(p2)
        if v1:
            if v1 not in seen:
                seen.add(v1)
                results.append(v1)
        if v2:
            if v2 not in seen:
                seen.add(v2)
                results.append(v2)

    # Bước 2: Quét 6-char codes đơn lẻ (không phải phần của 12-char đã xử lý)
    for m in _33WIN_RE.finditer(clean_text):
        tok = m.group(1)
        v = _33win_validate(tok)
        if v and v not in seen:
            seen.add(v)
            results.append(v)

    return results

def extract_codes_adavawef_smart(text: str) -> List[str]:
    """Adavawef sites (okking, new88, ok9): plain invite codes 6-12 chars."""
    return _extract_smart(text, _ADAVAWEF_RE, _adavawef_validate)


def _79king_validate(tok: str) -> str:
    """79KING: chấp nhận cả short 4-char UPPERCASE (từ ảnh) và 6-12 char mixed (text).
    Short code: đúng 4 ký tự, toàn uppercase + digit, ít nhất 1 chữ và 1 số.
    Long code: delegate sang _adavawef_validate.
    """
    if len(tok) <= 5:
        # Short code (4-5 chars) — yêu cầu uppercase + digit mix
        if len(tok) < 4:
            return ""
        if not tok.isalnum():
            return ""
        if tok.isdigit():
            return ""
        if tok.isalpha() and not any(c.isdigit() for c in tok):
            # Pure alpha short code (ABCD) — quá nhiều FP, chỉ chấp nhận nếu uppercase
            if tok != tok.upper():
                return ""
            # Kiểm tra _L0_BLACKLIST (CSKH, AUTO, VIP, CODE, ...)
            if tok.upper() in _L0_BLACKLIST:
                return ""
            # 4-char all-upper-alpha: vẫn rủi ro FP — bỏ nếu là noise
            tok_lower = tok.lower()
            if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS:
                return ""
            if _ANY_SUBS_BL_RE.search(tok_lower):
                return ""
        return tok.upper()
    # Long code (6-12) → delegate
    return _adavawef_validate(tok)


def extract_codes_79king_smart(text: str) -> List[str]:
    """79KING: kết hợp short 4-char codes (từ ảnh) và long 6-12 codes (từ text)."""
    seen: set = set()
    results: List[str] = []
    # 1) Quét short codes (4-char uppercase)
    for m in _79KING_SHORT_RE.finditer(text):
        tok = _79king_validate(m.group(1))
        if tok and tok not in seen:
            seen.add(tok)
            results.append(tok)
    # 2) Quét long codes (6-12 char)
    for m in _ADAVAWEF_RE.finditer(text):
        tok = _adavawef_validate(m.group(1))
        if tok and tok not in seen:
            seen.add(tok)
            results.append(tok)
    return results

# NEW88 text-filter deobfuscation: token có thể bị chèn ký tự trong post text
_NEW88_ANCHOR_RE = re.compile(r"(?:CLIP\s*VUI\s*NEW88|NEW88)", re.IGNORECASE)
_NEW88_OBF_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9@#$%^&*+=!_:/\\-]{8,24})(?![A-Za-z0-9])")

def extract_codes_new88_smart(text: str) -> List[str]:
    """NEW88: ưu tiên code plain, fallback deobfuscation cho text phủ filter."""
    seen: set[str] = set()
    results: List[str] = []
    for tok in extract_codes_adavawef_smart(text):
        if tok not in seen:
            seen.add(tok)
            results.append(tok)
    for anchor in _NEW88_ANCHOR_RE.finditer(text):
        window = text[anchor.start():min(len(text), anchor.start() + 800)]
        for m in _NEW88_OBF_TOKEN_RE.finditer(window):
            raw_tok = m.group(1)
            if not any(not c.isalnum() for c in raw_tok):
                continue
            clean = ''.join(c for c in raw_tok if c.isalnum())
            v = _adavawef_validate(clean)
            if v and v not in seen:
                seen.add(v)
                results.append(v)
    return results

# === MB66 EXTRACTOR ===
# MB66 format 1: XX★XXX◆XX✦X (2+3+2+1 = 8 chars alphanumeric với ★◆✦ làm dấu phân cách)
# Ví dụ: xB★aHP◆VH✦3 → clean: xBaHPVH3
_MB66_RAW_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z0-9]{2})\u2605([A-Za-z0-9]{3})\u25C6([A-Za-z0-9]{2})\u2726([A-Za-z0-9])"
    r"(?![A-Za-z0-9])"
)
# MB66 format 2: XXX@XXX$%XX (3+3+2 = 8 chars alphanumeric với @/$% làm trap chars)
# Ví dụ: k75@47T$%SM → clean: k7547TSM
_MB66_RAW_RE2 = re.compile(
    r"(?<![A-Za-z0-9@$%])"
    r"([A-Za-z0-9]{3})@([A-Za-z0-9]{3})\$%([A-Za-z0-9]{2})"
    r"(?![A-Za-z0-9@$%])"
)
# MB66 format 3 (text filter obfuscation): 8-char code bị chèn nhiều ký tự phủ
# Ví dụ: 1✦u✧g★2Xuft -> 1ug2Xuft, c✦E✧A★mpEmJ -> cEAmpEmJ
_MB66_OBF_8CHARS_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"([A-Za-z0-9](?:[^A-Za-z0-9\s][A-Za-z0-9]){7})"
    r"(?![A-Za-z0-9])"
)
_MB66_SPECIAL_SET = {"\u2605", "\u25C6", "\u2726", "@", "$", "%"}  # ★◆✦ + @$%
_MB66_ANCHOR_RE = re.compile(r"(?:CHECK\s*LINK\s*MB66|https?://mb66b\.com/xtlink)", re.IGNORECASE)
_MB66_OBF_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9@#$%^&*+=!_:/\\-]{8,24})(?![A-Za-z0-9])")

def _mb66_raw_to_clean(raw_tok: str) -> str:
    """Strip ★◆✦/@$% từ MB66 raw token → 8-char clean code."""
    return "".join(c for c in raw_tok if c not in _MB66_SPECIAL_SET)

def _mb66_validate(clean: str) -> str:
    """MB66 clean code: đúng 8 ký tự và mixed-case."""
    if len(clean) != 8:
        return ""
    if not clean.isalnum():
        return ""
    if clean.isdigit():
        return ""
    has_upper = any(c.isupper() for c in clean)
    has_lower = any(c.islower() for c in clean)
    if not (has_upper and has_lower):
        return ""
    clean_lower = clean.lower()
    # Kiểm tra noise words (bắt "Chinhxac", "Diemdanh", "Baccarat", ...)
    if clean_lower in _VIET_NOISE_WORDS or clean_lower in _SOCIAL_NOISE_WORDS:
        return ""
    if _ANY_SUBS_BL_RE.search(clean_lower):
        return ""
    if _NO_DIGIT_SUBS_BL_RE.search(clean):
        return ""
    return clean

def _mb66_validate_and_add(clean: str, seen: set, results: list) -> None:
    """Validate 8-char mixed-case code and add to results."""
    v = _mb66_validate(clean)
    if v and v not in seen:
        seen.add(v)
        results.append(v)

def extract_codes_mb66_smart(text: str) -> List[str]:
    """MB66: detect trap-char patterns → strip symbols → 8-char mixed-case code.
    
    Supports multiple trap formats:
    - Format 1: XX★XXX◆XX✦X (★◆✦ separators)
    - Format 2: XXX@XXX$%XX (@ and $% separators)
    """
    results: List[str] = []
    seen: set = set()
    # Format 1: ★◆✦
    for m in _MB66_RAW_RE.finditer(text):
        clean = m.group(1) + m.group(2) + m.group(3) + m.group(4)
        _mb66_validate_and_add(clean, seen, results)
    # Format 2: @$%
    for m in _MB66_RAW_RE2.finditer(text):
        clean = m.group(1) + m.group(2) + m.group(3)
        _mb66_validate_and_add(clean, seen, results)
    # Format 2.5: obfuscation bằng ký tự phủ giữa từng ký tự code
    for m in _MB66_OBF_8CHARS_RE.finditer(text):
        raw_tok = m.group(1)
        clean = ''.join(c for c in raw_tok if c.isalnum())
        _mb66_validate_and_add(clean, seen, results)
    # Format 2.6: token theo khoảng trắng, chứa ký tự phủ (✦✧★...) và clean ra đúng 8 chars
    for raw_tok in re.findall(r"[^\s]{8,24}", text):
        if not any((not c.isalnum()) for c in raw_tok):
            continue
        clean = ''.join(c for c in raw_tok if c.isalnum())
        if len(clean) != 8:
            continue
        _mb66_validate_and_add(clean, seen, results)
    # Format 3 (Telegram filter): code bị chèn ký tự, thường nằm ngay phía trên "CHECK LINK MB66"
    # Ví dụ: T@aEY@ziRR -> TaEYziRR, Et1@TnY$%d6 -> Et1TnYd6
    for anchor in _MB66_ANCHOR_RE.finditer(text):
        window = text[max(0, anchor.start() - 500):anchor.start()]
        for m in _MB66_OBF_TOKEN_RE.finditer(window):
            raw_tok = m.group(1)
            if not any(not c.isalnum() for c in raw_tok):
                continue
            clean = ''.join(c for c in raw_tok if c.isalnum())
            _mb66_validate_and_add(clean, seen, results)
    return results


# Routing đủ 16 site → smart extractor tương ứng, 4-layer chỉ là last-resort
_ALNUM8_SITES = frozenset({"qq88"})  # ok8386 tách riêng để hỗ trợ trap-video format

# OK8386 video: code có ký tự đặc biệt chèn vào (x=o_b1*i7?) → strip về code sạch.
_OK8386_TRAP_CHARS = frozenset(r"!@#$%^&*()-_=+[]{}|:;,.?/\\~`")

def _ok8386_trap_validate(tok: str) -> str:
    """OK8386 trap-video: chấp nhận clean 6-8 ký tự, có chữ+số."""
    if not (6 <= len(tok) <= 8): return ""
    if not tok.isalnum(): return ""
    if tok.isdigit(): return ""
    has_alpha = any(c.isalpha() for c in tok)
    has_digit = any(c.isdigit() for c in tok)
    if not (has_alpha and has_digit): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    return tok

def extract_codes_ok8386_smart(text: str) -> List[str]:
    """OK8386: ưu tiên bắt trap-video codes, fallback alnum8 chuẩn."""
    clean_text = layer1_strip_hidden(text)
    tokens = layer2_split_chunks(clean_text)
    seen: set = set()
    results: List[str] = []
    for raw_tok in tokens:
        if not any(c in _OK8386_TRAP_CHARS for c in raw_tok):
            continue
        cleaned = ''.join(c for c in raw_tok if c.isalnum())
        v = _ok8386_trap_validate(cleaned)
        if v and v not in seen:
            seen.add(v)
            results.append(v)
    for tok in _extract_smart(text, _ALNUM8_RE, _alnum8_validate):
        if tok not in seen:
            seen.add(tok)
            results.append(tok)
    return results

# === OPEN88 / OPEN88LIVE EXTRACTOR ===
# open88live có 3 loại gifcode:
# 1. Standard 8-char mixed alnum (cũ): aBcDeFgH
# 2. Short event codes: 2 HOA + 2 số: TB01, TB02, TB03
# 3. Special-char 8-char (THỰC TẾ XÁC NHẬN từ ảnh channel):
#    xh=yO=UV | 9E+bV+sx | U+PjV$lI | y-MIX&Ay | 5C*OBX%N
#    → 8 ký tự chính xác, mixed case, chứa special chars: + = - * % & $
#    → PHẢI extract loại này — alnum8 thông thường BỎ SÓT hoàn toàn

# Regex open88 special-char: 8 ký tự gồm alnum + special chars (ít nhất 1 special)
# Boundary: không đứng cạnh alnum/special để tránh bắt chuỗi dài hơn
_OPEN88_SPECIAL_CHARS = r"+=\-*%&$"
_OPEN88_SPECIAL_RE = re.compile(
    r"(?<![A-Za-z0-9+\-=*%&$])"
    r"([A-Za-z0-9+\-=*%&$]{8})"
    r"(?![A-Za-z0-9+\-=*%&$])"
)

def _open88_special_validate(tok: str) -> str:
    """Validate open88 special-char gifcode (8 ký tự, có special char, có letter).
    Thực tế: xh=yO=UV, 9E+bV+sx, U+PjV$lI, y-MIX&Ay, 5C*OBX%N
    """
    if len(tok) != 8:
        return ""
    # Phải có ít nhất 1 special char
    specials = set("+=-*%&$")
    if not any(c in specials for c in tok):
        return ""
    # Phải có ít nhất 1 chữ cái (không phải thuần số+special)
    if not any(c.isalpha() for c in tok):
        return ""
    # Không phải URL/path fragment
    if tok.startswith("//") or tok.startswith("http"):
        return ""
    return tok

# Short event codes: TB01, TB02, TB03...
_OPEN88_SHORT_RE = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{2}\d{2,3})(?![A-Za-z0-9])",
)
# Blacklist prefix cho short codes (promo label, không phải gifcode)
_OPEN88_SHORT_BL = frozenset({"KM", "VIP", "NT", "GM", "HL", "VC"})

def _open88_short_validate(tok: str) -> str:
    """Short open88 event code: 4-5 ký tự, 2 HOA + 2-3 số.
    Pattern thực tế: TB01, TB02, TB03
    Loại placeholder promo labels (KM, VIP, NT...) và site tokens.
    """
    if not (4 <= len(tok) <= 5): return ""
    prefix = tok[:2]
    if not (prefix.isupper() and prefix.isalpha()): return ""
    if not tok[2:].isdigit(): return ""
    if prefix in _OPEN88_SHORT_BL: return ""
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(tok): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    return tok

def extract_codes_open88_smart(text: str) -> List[str]:
    """OPEN88LIVE: extract tất cả 3 loại gifcode.

    Loại 1 - Standard 8-char alnum:  aBcDeFgH
    Loại 2 - Short event codes:       TB01 / TB02 / TB03
    Loại 3 - Special-char 8-char:     xh=yO=UV / 9E+bV+sx / U+PjV$lI (THỰC TẾ XÁC NHẬN)
    """
    seen: set = set()
    results: List[str] = []

    # Loại 3 TRƯỚC (special-char) — ưu tiên vì đặc trưng nhất
    for m in _OPEN88_SPECIAL_RE.finditer(text):
        tok = m.group(1)
        v = _open88_special_validate(tok)
        if v and v not in seen:
            seen.add(v)
            results.append(v)

    # Loại 1 - Standard 8-char alnum (backup nếu không có special-char)
    for tok in _extract_smart(text, _ALNUM8_RE, _alnum8_validate):
        if tok not in seen:
            seen.add(tok)
            results.append(tok)

    # Loại 2 - Short event codes
    for m in _OPEN88_SHORT_RE.finditer(text):
        tok = m.group(1).upper()
        v = _open88_short_validate(tok)
        if v and v not in seen:
            seen.add(v)
            results.append(v)

    return results


# CM88: 10-char alnum, mixed-case (upper+lower cả hai) — digit là OPTIONAL
# Fix: ~26% codes thực tế all-alpha (rPmIASnyOw, BSlUNfijDq, ARCzFsgRDS...) bị miss khi enforce digit
_CM88_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z0-9]{10})(?![A-Za-z0-9])")
# OPTIMIZATION: tight regex encodes mixed-case (upper+lower) constraints, digit KHÔNG bắt buộc
_CM88_TIGHT_RE = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(?=[A-Za-z0-9]{0,9}[A-Z])"   # has uppercase
    r"(?=[A-Za-z0-9]{0,9}[a-z])"   # has lowercase
    r"([A-Za-z0-9]{10})"
    r"(?![A-Za-z0-9])"
)

def _cm88_validate(tok: str) -> str:
    """CM88: 10-char alnum, mixed-case (có hoa+thường). Digit là optional."""
    if len(tok) != 10: return ""
    if not tok.isalnum(): return ""
    if _REPEATED_CHAR_RE.search(tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    return tok

def _cm88_validate_from_tight(tok: str) -> str:
    """Validator rút gọn — dùng khi tight regex đã enforce mixed-case."""
    if _REPEATED_CHAR_RE.search(tok): return ""
    tok_lower = tok.lower()
    if tok_lower in _VIET_NOISE_WORDS or tok_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(tok_lower): return ""
    if _KNOWN_SITE_TOKENS_RE.search(tok): return ""
    if _ANY_SUBS_BL_RE.search(tok_lower): return ""
    if _MARKETING_WITH_YEAR_RE.fullmatch(tok): return ""
    return tok

def extract_codes_cm88_smart(text: str) -> List[str]:
    """CM88: 10-char mixed-case alnum (digit optional).
    OPTIMIZATION: dùng _CM88_TIGHT_RE để regex encode mixed-case constraints."""
    return _extract_smart(text, _CM88_TIGHT_RE, _cm88_validate_from_tight)

# === TURNSTILE TRAP-CODE EXTRACTOR (rr88/xx88/gg88/mm88) ===
# Kênh Telegram phát code có trap ký tự: :4U+4*UTJ → 4U4UTJ, 5RX/+4**0Y → 5RX40Y
# Strip : + * / _ — ! @ & » « - ~ ^ $ # | ? để lấy code alphanumeric gốc (5-10 chars).
_TURNSTILE_TRAP_SITES = frozenset({"rr88", "xx88", "gg88", "mm88", "kjc", "8kbet"})
_TRAP_CHARS = frozenset(":+*/-<@#%=_!&»«—~^$#@|?\\")

def extract_codes_turnstile_trap_smart(text: str) -> List[str]:
    """Turnstile sites: extract trap-char codes (:+*/-  mixed with alnum).
    Phase 1: tìm token có trap char → strip → validate.
    Phase 2 fallback: 4-layer cho code sạch (không trap).
    GG88/MM88/XX88/RR88: codes có thể all-alpha, all-uppercase (vd: T*YEJ-BM → TYEJBM).
    """
    clean_text = layer1_strip_hidden(text)
    tokens = layer2_split_chunks(clean_text)
    seen: set = set()
    results: List[str] = []

    for raw_tok in tokens:
        if not any(c in _TRAP_CHARS for c in raw_tok):
            continue
        cleaned = ''.join(c for c in raw_tok if c.isascii() and c.isalnum())
        # KJC Trap codes are strictly 6 characters long (e.g. UL1I57, 87RH8H, VMTVF2, 7VD1G0)
        if not cleaned or len(cleaned) != 6:
            continue
        if cleaned.isdigit():
            continue
        if not any(c.isalpha() for c in cleaned):
            continue
        if VIET_DIACRITICS_RE.search(cleaned):
            continue
        cl = cleaned.lower()
        if cl in _VIET_NOISE_WORDS or cl in _SOCIAL_NOISE_WORDS or "gaixinh" in cl:
            continue
        if re.match(r"^kt\d+[a-z0-9]?$", cl):
            continue
        if _KNOWN_SITE_TOKENS_RE.search(cleaned):
            continue
        if _ANY_SUBS_BL_RE.search(cl):
            continue
        if _REPEATED_CHAR_RE.search(cleaned):
            continue
        if cleaned in seen:
            continue
        seen.add(cleaned)
        results.append(cleaned)

    return results

# Adavawef sites: plain invite code API (j88, 79king, okking, new88)
_ADAVAWEF_SITES = frozenset({"j88", "79king", "okking", "new88", "ok9", "go88", "789bet"})
# Sites với format rõ ràng: KHÔNG fallback về 4-layer (tránh FP)
_NO_4LAYER_FALLBACK = frozenset({"f168", "fly88", "open88"})

def _split_concat_codes(codes: List[str], site_key: str) -> List[str]:
    """Split concatenated codes that exceed site-specific valid lengths.
    E.g. mm88: "8BYP8DPQXF7B" (12 chars) → ["8BYP8D", "PQXF7B"] (2×6).
    Only splits when ALL parts are valid length and the original is NOT valid."""
    valid_lens = _SITE_MIN_CODE_LEN.get(site_key)
    if not valid_lens:
        return codes
    out: List[str] = []
    for c in codes:
        clen = len(c)
        if clen in valid_lens:
            out.append(c)
            continue
        # Try splitting into equal-length parts (most common: 2 codes concatenated)
        split_done = False
        for vl in sorted(valid_lens, reverse=True):
            if clen >= vl * 2 and clen % vl == 0:
                parts = [c[i:i+vl] for i in range(0, clen, vl)]
                # Validate each part has at least 1 alpha
                if all(any(ch.isalpha() for ch in p) for p in parts):
                    out.extend(parts)
                    split_done = True
                    break
        if not split_done:
            # Try 2-way split with different valid lengths
            for vl in sorted(valid_lens, reverse=True):
                remainder = clen - vl
                if remainder in valid_lens and remainder != clen:
                    p1, p2 = c[:vl], c[vl:]
                    if any(ch.isalpha() for ch in p1) and any(ch.isalpha() for ch in p2):
                        out.extend([p1, p2])
                        split_done = True
                        break
            if not split_done:
                out.append(c)  # keep original if can't split
    return out


def extract_codes_for_site(text: str, site_id: str = "", source: str = "text") -> List[str]:
    """Định tuyến thông minh theo site_id — phủ đủ 16 site, 4-layer chỉ là last-resort."""
    site_key = (site_id or "").strip().lower()
    result = _extract_codes_for_site_raw(text, site_key, source=source)
    result = _split_concat_codes(result, site_key)
    return _apply_ai_filter(result, site_key)


def _extract_codes_for_site_raw(text: str, site_key: str, source: str = "text") -> List[str]:
    """Internal: extract codes without AI filter."""

    if site_key == "cm88":
        return extract_codes_cm88_smart(text)
    elif site_key == "mb66":
        # MB66: raw pattern XX★XXX◆XX✦X → 8-char clean — KHÔNG fallback 4-layer tránh noise hashtag
        return extract_codes_mb66_smart(text)
    elif site_key in ("c168", "oklive"):
        # C168 và OKLive: cùng format 10-12 char alphanumeric — KHÔNG fallback 4-layer tránh noise
        # OKLive codes từ video stream: 10-char mixed case (TzCZVkCUA8, eEdvo0s5jw...)
        if site_key == "oklive":
            return extract_codes_cm88_smart(text)  # cm88 extractor: 10-char alnum mixed, no special chars
        return extract_codes_c168_smart(text)
    elif site_key == "sc88":
        # SC88: format đặc trưng (special char nhúng) — KHÔNG fallback 4-layer tránh noise
        return extract_codes_sc88_smart(text)
    elif site_key == "f8bet":
        # F8BET: KHÔNG fallback 4-layer — tránh tiếng Việt OCR noise (CHAODOn, NGAYHOIVIEn...)
        # Codes đã được xử lý đầy đủ bởi f8bet_smart (raw 9-char + video 10-char alnum)
        return extract_codes_f8bet_smart(text)
    elif site_key == "jun88":
        # JUN88: KHÔNG fallback 4-layer — tránh noise từ OCR tiếng Việt
        return extract_codes_jun88_smart(text)
    elif site_key == "hi88":
        # HI88: KHÔNG fallback 4-layer — tránh noise "FaceTime", "Google", timestamps
        return extract_codes_hi88_smart(text)
    elif site_key == "f168":
        # F168: format rõ ràng — KHÔNG fallback 4-layer tránh FP (all-upper alnum)
        return extract_codes_f168_smart(text)
    elif site_key == "fly88":
        # FLY88: format rõ ràng — KHÔNG fallback 4-layer tránh FP (all-upper alnum+digit)
        return extract_codes_fly88_smart(text)
    elif site_key in _ADAVAWEF_SITES:
        if site_key == "j88":
            # J88: all-uppercase alpha codes (ABCVIP, VIP88K, PHATONG...) — validator riêng
            return extract_codes_j88_smart(text)
        if site_key == "new88":
            # NEW88: hỗ trợ text phủ filter với ký tự chèn
            return extract_codes_new88_smart(text)
        if site_key == "79king":
            # 79KING: short 4-char uppercase (ảnh) + long 6-12 (text)
            return extract_codes_79king_smart(text)
        # okking/ok9/go88/789bet: giữ rule mixed-case
        return extract_codes_adavawef_smart(text)
    elif site_key == "8kbet":
        # 8KBET: validator riêng — cho phép "bet" substring trong code (vd: hDSdKbet)
        return extract_codes_8kbet_smart(text)
    elif site_key == "ok8386":
        return extract_codes_ok8386_smart(text)
    elif site_key in _ALNUM8_SITES:
        result = extract_codes_alnum8_smart(text)
        if result: return result
    elif site_key in _TURNSTILE_TRAP_SITES:
        # rr88/xx88/gg88/mm88: trap-char codes (:+*/ mixed with alnum)
        result = extract_codes_turnstile_trap_smart(text)
        if result: return result
        # Fallback to 4-layer for clean codes (no trap chars)
    elif site_key == "open88":
        # OPEN88 / OPEN88LIVE: 8-char + short event codes (TB01/TB02/TB03)
        # KHÔNG fallback 4-layer — tránh FP từ noise marketing alnum8
        return extract_codes_open88_smart(text)
    elif site_key == "33win":
        # 33WIN: 6-char UPPERCASE alnum, auto-split 12→2×6 nếu ảnh dính đôi
        # KHÔNG fallback 4-layer — tránh FP noise uppercase 6-char
        return extract_codes_33win_smart(text, source=source)
    else:
        # Không rõ site: chạy tất cả extractors theo thứ tự ưu tiên độ chính xác cao → thấp
        all_results = []
        for fn in [
            extract_codes_c168_smart, extract_codes_sc88_smart,
            extract_codes_f8bet_smart, extract_codes_jun88_smart,
            extract_codes_hi88_smart, extract_codes_f168_smart,
            extract_codes_adavawef_smart, extract_codes_alnum8_smart,
        ]:
            all_results.extend(fn(text))
        if all_results:
            return list(dict.fromkeys(all_results))

    return extract_codes_4_layers(text)


def _apply_ai_filter(codes: List[str], site_key: str) -> List[str]:
    """Apply learned AI patterns to filter out likely-invalid codes."""
    if not codes:
        return codes
    try:
        from core.ai_filter_learner import get_ai_filter
        ai = get_ai_filter()
        filtered = []
        for c in codes:
            reject, reason = ai.should_reject_code(c, site_key)
            if reject:
                import logging
                logging.getLogger(__name__).debug("[AI-FILTER] Rejected %s for %s: %s", c, site_key, reason)
            else:
                filtered.append(c)
        return filtered if filtered else codes  # fallback: return all if AI rejects everything
    except Exception:
        return codes


# === IS VALID CODE (Post-Extraction Validation - Được giữ nguyên) ===
_SITE_MIN_CODE_LEN: Dict[str, frozenset] = {
    "sc88": frozenset({10}), "c168": frozenset({10, 12}), "f168": frozenset({8}), "fly88": frozenset({8, 9, 10}), "qq88": frozenset({8}),
    "open88": frozenset({4, 5, 8}), "cm88": frozenset({10}), "jun88": frozenset({6}), "j88": frozenset({8}), "ok8386": frozenset({8}),
    "f8bet": frozenset({8, 9, 10}), "hi88": frozenset({8,9,10,11,12,13}), "8kbet": frozenset({8}), "79king": frozenset({4,5,6,7,8,9,10,11,12}),
    "okking": frozenset({6,7,8,9,10,11,12}), "new88": frozenset({6,7,8,9,10,11,12}), "ok9": frozenset({7,8,9,10}), "oklive": frozenset({10}),
    "mb66": frozenset({8}),
    "rr88": frozenset({5,6,7,8,9,10}), "xx88": frozenset({5,6,7,8,9,10}),
    "gg88": frozenset({5,6,7,8,9,10}), "mm88": frozenset({5,6,7,8,9,10}),
    "33win": frozenset({6}),
}

def is_valid_code(token: str, site_id: str) -> bool:
    """Kiểm tra token đã làm sạch có hợp lệ với site_id không (Logic cũ)."""
    if VIET_DIACRITICS_RE.search(token or ""): return False
    cleaned = _STRIP_SPECIAL_RE.sub("", token or "")
    if not cleaned: return False
    site_key = (site_id or "").strip().lower()
    valid_lens = _SITE_MIN_CODE_LEN.get(site_key, [8])
    if len(cleaned) not in valid_lens: return False
    if cleaned.isdigit(): return False
    if _PLACEHOLDER_PROMO_LABEL_RE.fullmatch(cleaned): return False
    if site_key in {"sc88", "c168"}:
        if cleaned.upper() == cleaned or cleaned.lower() == cleaned: return False
    if site_key == "jun88":
        if len(cleaned) != 6: return False
        has_alpha = any(ch.isalpha() for ch in cleaned)
        has_digit = any(ch.isdigit() for ch in cleaned)
        if has_alpha and not has_digit:
            if cleaned.upper() == cleaned or cleaned.lower() == cleaned: return False
    if site_key in {"fly88", "f168"}:
        if _YEAR_SUFFIX_RE.search(cleaned): return False
        has_alpha = any(ch.isalpha() for ch in cleaned)
        has_digit = any(ch.isdigit() for ch in cleaned)
        if site_key == "fly88":
            if not (has_alpha and has_digit): return False
            if not (8 <= len(cleaned) <= 10): return False
            if cleaned.upper() == cleaned: return False
        else:
            if has_alpha and (not has_digit):
                if cleaned.upper() == cleaned or cleaned.lower() == cleaned: return False
    if cleaned.isalpha() and (cleaned.upper() == cleaned or cleaned.lower() == cleaned): return False
    if SYMBOL_ARMOR_RE.search(token): return False
    return True 

# ====================================================================
# === BỔ SUNG LÕI LỌC TELEGRAM MỚI (CHỈ CHO C168/SC88) ================
# ====================================================================

def _is_trigger_activated(text: str) -> bool:
    """Radar Nhận Diện: Kiểm tra cụm từ kích hoạt bắt buộc (case-insensitive)."""
    lower_text = (text or "").lower()
    if not lower_text:
        return False
    _strict_phrases = (
        "lưu ý: mã code hợp lệ",
        "lưu ý: khi nhập code",
        "mã code nhập nhiều lần",
    )
    if any(p in lower_text for p in _strict_phrases):
        return True
    # Mở rộng radar cho C168/SC88 posts thực tế: không bắt buộc đúng 3 câu mẫu.
    # Chỉ cần có dấu hiệu rõ ràng là bài phát code/nhập code.
    _loose_markers = (
        "mã code", "ma code", "nhập code", "nhap code",
        "code hợp lệ", "code hop le", "code hôm nay", "code hom nay",
        "nhận code", "nhan code", "khuyến mãi", "khuyen mai",
        "mã hôm nay", "ma hom nay", "mã hợp lệ", "ma hop le",
    )
    return any(m in lower_text for m in _loose_markers)

def _clean_and_validate_telegram(token: str) -> str:
    """
    Core Blade & Thước đo Hợp lệ cho C168/SC88 Telegram.
    - Gọt rác: Chỉ giữ a-z, A-Z, 0-9 (Case-sensitive)
    - Validation: 8 <= len(code) <= 15. Telegram/monospace có thể bị OCR/entity cắt ngắn hơn
      nhưng vẫn là code hợp lệ sau khi gọt special chars.
    """
    # Core Blade: Gọt mã (re.sub(r'[^a-zA-Z0-9]', '', text))
    cleaned = SYMBOL_ARMOR_RE.sub('', token)
    
    if not cleaned: return ""
        
    # Thước đo Hợp lệ: nới về 8-15 để đỡ bỏ sót monospace/entity tokens.
    if not (8 <= len(cleaned) <= 15): return ""

    # Loại trừ: Mã chỉ toàn số (Vì mã Telegram 10-15 ký tự thường có chữ)
    # LƯU Ý: Nếu mã full chữ được chấp nhận, việc loại trừ full số là một lớp bảo vệ.
    if cleaned.isdigit(): return ""
    # Tăng precision cho c168/sc88 Telegram: code hợp lệ thực tế luôn có ít nhất 1 số.
    if not any(ch.isdigit() for ch in cleaned): return ""
    
    token_lower = cleaned.lower()
    
    # Loại trừ Blacklist (tiếng Việt, MXH, tên site, từ bẩn)
    if token_lower in _VIET_NOISE_WORDS: return ""
    if token_lower in _SOCIAL_NOISE_WORDS: return ""
    if _is_viet_noise_with_digits(token_lower): return ""

    # Kiểm tra substring tên site (regex batch O(1) thay for-loop)
    if _KNOWN_SITE_TOKENS_RE.search(cleaned): return ""

    if _ANY_SUBS_BL_RE.search(token_lower): return ""

    return cleaned

# SC88-exclusive special chars (không có trong C168 set)
# Dùng để phân biệt SC88 raw token vs C168 raw token
_SC88_EXCLUSIVE_SPECIALS = frozenset("-/()<>=+!_")

def _infer_c168_sc88_site(raw_token: str) -> str:
    """Suy luận code thuộc site nào dựa vào special chars trong raw token.
    
    Phân tích format thực tế:
    - C168 image codes: 2-4 specials rải rác, raw length 13-17 → "c168"
    - C168 text/channel: 1 shared special (%^&@#*$), raw length 11 → ambiguous → "both"
    - SC88: 1 SC88-exclusive special (-/()<>=+!_), raw length 10-11 → "sc88"
    - SC88: 1 shared special (%^&@#*$), raw length 11 → ambiguous → "both"
    - Monospace (không special) → "both"
    """
    specials = [c for c in raw_token if not c.isalnum()]
    if not specials:
        return "both"  # one-click monospace → submit cả 2

    # SC88-exclusive specials → chắc chắn SC88
    if any(c in _SC88_EXCLUSIVE_SPECIALS for c in specials):
        # Nếu có 2+ specials VÀ ít nhất 1 non-exclusive → C168 (vd: Scql#jLe3dt~05 có # và ~)
        # ~ không trong SC88-exclusive → nhiều specials → C168
        if len(specials) >= 2:
            return "c168"
        return "sc88"

    # Nhiều specials từ shared set (2+) → C168 image format
    # (vd: dQfV*DS^&C$AEd2k có *, ^, &, $ = 4 specials)
    if len(specials) >= 2:
        return "c168"

    # 1 shared special (%^&@#*$): C168 text và SC88 text đều có format này
    # Không thể phân biệt → submit cả 2 để không bỏ lỡ
    return "both"

def extract_c168_sc88_telegram_codes(raw_text: str, one_click_codes: List[str] | None = None) -> List[Dict[str, str]]:
    """
    Hàm chính xử lý và trích xuất mã code C168/SC88 Telegram.
    CHỈ KÍCH HOẠT khi Radar nhận diện.

    Args:
        raw_text: Văn bản thô của tin nhắn Telegram (dùng cho Radar).
        one_click_codes: List các mã đã được trích xuất từ Monospace.

    Returns:
        List[Dict[str, str]]: Danh sách mã code hợp lệ kèm tag phân loại.
        Mỗi dict: {"code": str, "type": "CODE_RIENG"|"CODE_CHUNG", "site": "c168"|"sc88"|"both"}
    """
    # 1. Radar Nhận Diện (Trigger Bắt Buộc)
    # Fallback an toàn: nếu có one-click/monospace entities thì vẫn nhận diện dù thiếu trigger text.
    _radar_on = _is_trigger_activated(raw_text)
    logger.debug("[C168/SC88-RADAR] radar_on=%s one_click_count=%d text_preview=%r",
                 _radar_on, len(one_click_codes) if one_click_codes else 0,
                 raw_text[:80] if raw_text else "")
    if not _radar_on and not one_click_codes:
        logger.debug("[C168/SC88-RADAR] Skip — no radar trigger AND no monospace entities")
        return []

    # 2. Thu thập Mã Ứng Viên — giữ nguyên raw token để suy luận site
    _ALL_RAW_SPECIALS = _C168_SPECIAL_CHARS_SET | _SC88_SPECIAL_CHARS_SET
    stage1 = _layer1_no_hashtag(raw_text)
    stage2 = layer2_split_chunks(stage1)

    # raw_token_map: cleaned_code → site tag (ưu tiên raw text, fallback monospace = "both")
    raw_token_map: dict[str, str] = {}

    # A) Mã từ raw text — có special char → có thể suy luận site
    for tok in stage2:
        if any(c in _ALL_RAW_SPECIALS for c in tok):
            cleaned = _clean_and_validate_telegram(tok)
            if cleaned:
                site_tag = _infer_c168_sc88_site(tok)
                # Nếu đã có từ raw text với site rõ hơn → giữ nguyên
                if cleaned not in raw_token_map or raw_token_map[cleaned] == "both":
                    raw_token_map[cleaned] = site_tag
            else:
                logger.debug("[C168/SC88-RADAR] Raw token rejected: %r (has special chars)", tok)

    # B) Mã One-Click (từ Monospace) — không có special → "both" (fallback nếu chưa có)
    if one_click_codes:
        for tok in one_click_codes:
            cleaned = _clean_and_validate_telegram(tok)
            if cleaned and cleaned not in raw_token_map:
                raw_token_map[cleaned] = "both"
            elif not cleaned:
                logger.debug("[C168/SC88-RADAR] Monospace token rejected: %r", tok)

    if not raw_token_map:
        logger.info("[C168/SC88-RADAR] radar_on=%s — KHÔNG tìm thấy code hợp lệ nào. "
                    "tokens_with_special=%d one_click=%d",
                    _radar_on, len([t for t in stage2 if any(c in _ALL_RAW_SPECIALS for c in t)]),
                    len(one_click_codes) if one_click_codes else 0)
        return []

    # 3. Phân loại
    # CODE_RIENG : code cá nhân/giới hạn → chỉ 1 tài khoản ăn được → bắn tỉa tuần tự
    #              • ≥2 codes: mỗi code dành riêng cho 1 tài khoản khác nhau
    #              • 1 code + KHÔNG có "nhiều lần": code đơn, giới hạn 1 người dùng
    # CODE_CHUNG : 1 code + có "nhiều lần" → dùng được cho tất cả → broadcast song song
    codes_list = list(raw_token_map.keys())
    count = len(codes_list)
    if not _radar_on and one_click_codes:
        # Monospace-only fallback: ưu tiên không bỏ sót, mặc định CODE_RIENG để bắn chắc.
        code_type = "CODE_RIENG"
    else:
        _raw_lower = raw_text.lower()
        _multi_use_signal = "nhi\u1ec1u l\u1ea7n" in _raw_lower or "nhieu lan" in _raw_lower
        if count >= 2 or not _multi_use_signal:
            code_type = "CODE_RIENG"
        else:
            code_type = "CODE_CHUNG"

    # 4. Output
    result = []
    for code in codes_list:
        result.append({"code": code, "type": code_type, "site": raw_token_map[code]})

    logger.info("[C168/SC88-RADAR] Found %d code(s) — type=%s codes=%s",
                len(result), code_type, [r["code"] for r in result])
    return result


# ====================================================================
# === ENTROPY-BASED GIFTCODE VALIDATOR (Export for telegram_harvester) =
# ====================================================================
import math as _math

def is_high_entropy_giftcode(code: str, site_id: str = "") -> bool:
    """
    Shannon entropy check: real giftcodes have higher character diversity than
    dictionary words or simple promo strings.
    
    Returns True if code passes entropy check (may be a real giftcode).
    Returns False if low-entropy → likely a dictionary word or generic promo.
    
    Threshold: Shannon entropy >= 2.0 bits/char for codes >= 6 chars.
    Common noise words: 'comment'=2.52, 'codefree'=2.75, 'minigame'=2.56
    Real codes: 'HEQUOM'=2.52, 'uRCzuDA8Z7'=3.32, 'WY4rTtx4Ka'=3.22
    
    NOTE: This function does NOT block low-entropy codes in direct submit mode.
    It is only applied during RAW broadcast text extraction (harvest mode).
    """
    if not code or len(code) < 4:
        return False
    
    code_lower = code.lower()
    
    # Quick blacklist check first (O(1) set lookup)
    if code_lower in _VIET_NOISE_WORDS or code_lower in _SOCIAL_NOISE_WORDS:
        return False
    if _is_viet_noise_with_digits(code_lower):
        return False
    
    # Shannon entropy calculation
    n = len(code)
    if n < 6:
        # Short codes skip entropy check
        return True
    
    freq = {}
    for c in code:
        freq[c] = freq.get(c, 0) + 1
    
    entropy = -sum((count / n) * _math.log2(count / n) for count in freq.values())
    
    # Real giftcodes typically have entropy >= 2.0 bits/char
    # Very simple words like "comment" (7 unique / 7 chars = 2.8) pass this but are in noise lists
    # Pure repeated-char like "aaaaaa" = 0.0 bits
    if n <= 8:
        threshold = 1.8  # More lenient for short codes
    else:
        threshold = 2.0
    
    return entropy >= threshold


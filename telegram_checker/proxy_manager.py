"""
proxy_manager.py
================
نظام إدارة البروكسيات التلقائي:
- كشف دولة الرقم من prefix الدولي
- جلب البروكسي المناسب لكل دولة
- توليد إعدادات الاتصال لـ Telethon
"""

import logging
import socks
import asyncio
import database as db

logger = logging.getLogger(__name__)

# =====================================================================
# خريطة prefix الأرقام الدولية → كود الدولة (ISO 3166-1 alpha-2)
# مرتبة من الأطول للأقصر لضمان أدق تطابق
# =====================================================================
PHONE_PREFIX_TO_COUNTRY = {
    # 4 أرقام
    "1242": "BS", "1246": "BB", "1264": "AI", "1268": "AG",
    "1284": "VG", "1340": "VI", "1345": "KY", "1441": "BM",
    "1473": "GD", "1649": "TC", "1664": "MS", "1670": "MP",
    "1671": "GU", "1684": "AS", "1721": "SX", "1758": "LC",
    "1767": "DM", "1784": "VC", "1787": "PR", "1809": "DO",
    "1829": "DO", "1849": "DO", "1868": "TT", "1869": "KN",
    "1876": "JM", "1939": "PR",

    # 3 أرقام
    "212": "MA", "213": "DZ", "216": "TN", "218": "LY",
    "220": "GM", "221": "SN", "222": "MR", "223": "ML",
    "224": "GN", "225": "CI", "226": "BF", "227": "NE",
    "228": "TG", "229": "BJ", "230": "MU", "231": "LR",
    "232": "SL", "233": "GH", "234": "NG", "235": "TD",
    "236": "CF", "237": "CM", "238": "CV", "239": "ST",
    "240": "GQ", "241": "GA", "242": "CG", "243": "CD",
    "244": "AO", "245": "GW", "246": "IO", "247": "AC",
    "248": "SC", "249": "SD", "250": "RW", "251": "ET",
    "252": "SO", "253": "DJ", "254": "KE", "255": "TZ",
    "256": "UG", "257": "BI", "258": "MZ", "260": "ZM",
    "261": "MG", "262": "RE", "263": "ZW", "264": "NA",
    "265": "MW", "266": "LS", "267": "BW", "268": "SZ",
    "269": "KM", "27": "ZA",  "290": "SH", "291": "ER",
    "297": "AW", "298": "FO", "299": "GL",
    "350": "GI", "351": "PT", "352": "LU", "353": "IE",
    "354": "IS", "355": "AL", "356": "MT", "357": "CY",
    "358": "FI", "359": "BG", "370": "LT", "371": "LV",
    "372": "EE", "373": "MD", "374": "AM", "375": "BY",
    "376": "AD", "377": "MC", "378": "SM", "380": "UA",
    "381": "RS", "382": "ME", "383": "XK", "385": "HR",
    "386": "SI", "387": "BA", "389": "MK",
    "420": "CZ", "421": "SK", "423": "LI",
    "500": "FK", "501": "BZ", "502": "GT", "503": "SV",
    "504": "HN", "505": "NI", "506": "CR", "507": "PA",
    "508": "PM", "509": "HT",
    "590": "GP", "591": "BO", "592": "GY", "593": "EC",
    "594": "GF", "595": "PY", "596": "MQ", "597": "SR",
    "598": "UY", "599": "CW",
    "670": "TL", "672": "NF", "673": "BN", "674": "NR",
    "675": "PG", "676": "TO", "677": "SB", "678": "VU",
    "679": "FJ", "680": "PW", "681": "WF", "682": "CK",
    "683": "NU", "685": "WS", "686": "KI", "687": "NC",
    "688": "TV", "689": "PF", "690": "TK", "691": "FM",
    "692": "MH",
    "850": "KP", "852": "HK", "853": "MO", "855": "KH",
    "856": "LA",
    "880": "BD", "886": "TW",
    "960": "MV", "961": "LB", "962": "JO", "963": "SY",
    "964": "IQ", "965": "KW", "966": "SA", "967": "YE",
    "968": "OM", "970": "PS", "971": "AE", "972": "IL",
    "973": "BH", "974": "QA", "975": "BT", "976": "MN",
    "977": "NP", "992": "TJ", "993": "TM", "994": "AZ",
    "995": "GE", "996": "KG", "998": "UZ",

    # 2 رقم
    "20": "EG",  "30": "GR",  "31": "NL",  "32": "BE",
    "33": "FR",  "34": "ES",  "36": "HU",  "39": "IT",
    "40": "RO",  "41": "CH",  "43": "AT",  "44": "GB",
    "45": "DK",  "46": "SE",  "47": "NO",  "48": "PL",
    "49": "DE",  "51": "PE",  "52": "MX",  "53": "CU",
    "54": "AR",  "55": "BR",  "56": "CL",  "57": "CO",
    "58": "VE",  "60": "MY",  "61": "AU",  "62": "ID",
    "63": "PH",  "64": "NZ",  "65": "SG",  "66": "TH",
    "7":  "RU",  "81": "JP",  "82": "KR",  "84": "VN",
    "86": "CN",  "90": "TR",  "91": "IN",  "92": "PK",
    "93": "AF",  "94": "LK",  "95": "MM",  "98": "IR",
    "1":  "US",
}

def get_country_code_from_phone(phone: str) -> str | None:
    """
    استخراج كود الدولة (ISO) من رقم الهاتف الدولي.
    مثال: +4915202573099 → DE
    """
    # تنظيف الرقم
    phone = phone.strip().lstrip('+')

    # البحث من الأطول للأقصر (4 أرقام → 3 → 2 → 1)
    for length in (4, 3, 2, 1):
        prefix = phone[:length]
        if prefix in PHONE_PREFIX_TO_COUNTRY:
            return PHONE_PREFIX_TO_COUNTRY[prefix]

    return None


# =====================================================================
# ProxyManager: جلب البروكسي المناسب وتوليد إعدادات Telethon
# =====================================================================

class ProxyManager:
    def __init__(self):
        self._cache = {}           # country_code → proxy dict
        self._cache_ts = {}        # country_code → timestamp
        self._CACHE_TTL = 120      # ثانيتان (تحديث البروكسي كل 2 دقيقة)

    async def get_proxy_for_phone(self, phone: str):
        """
        يُرجع إعدادات البروكسي لـ Telethon بناءً على دولة الرقم.
        يُرجع None إذا لا يوجد بروكسي للدولة.
        """
        import time
        country_code = get_country_code_from_phone(phone)
        if not country_code:
            logger.debug(f"[ProxyManager] Could not detect country for {phone}")
            return None, None

        # فحص الكاش
        now = time.monotonic()
        if (country_code in self._cache and
                now - self._cache_ts.get(country_code, 0) < self._CACHE_TTL):
            proxy_data = self._cache[country_code]
            if proxy_data is None:
                return country_code, None
            return country_code, self._build_telethon_proxy(proxy_data)

        # جلب من قاعدة البيانات
        proxy_data = await asyncio.to_thread(db.get_proxy_for_country, country_code)
        self._cache[country_code] = proxy_data
        self._cache_ts[country_code] = now

        if not proxy_data:
            logger.debug(f"[ProxyManager] No proxy found for country {country_code} (phone: {phone})")
            return country_code, None

        logger.info(f"[ProxyManager] Using proxy {proxy_data['host']}:{proxy_data['port']} ({country_code}) for {phone}")
        return country_code, self._build_telethon_proxy(proxy_data)

    def _build_telethon_proxy(self, proxy_data: dict):
        """
        تحويل بيانات البروكسي إلى tuple تقبلها Telethon:
        (socks.SOCKS5, host, port, True, username, password)
        """
        proxy_type_map = {
            "SOCKS5": socks.SOCKS5,
            "SOCKS4": socks.SOCKS4,
            "HTTP":   socks.HTTP,
        }
        ptype = proxy_type_map.get(proxy_data.get("proxy_type", "SOCKS5").upper(), socks.SOCKS5)
        username = proxy_data.get("username")
        password = proxy_data.get("password")

        if username and password:
            return (ptype, proxy_data["host"], proxy_data["port"], True, username, password)
        else:
            return (ptype, proxy_data["host"], proxy_data["port"])

    def invalidate_cache(self, country_code=None):
        """إبطال الكاش لدولة معينة أو كله."""
        if country_code:
            self._cache.pop(country_code.upper(), None)
            self._cache_ts.pop(country_code.upper(), None)
        else:
            self._cache.clear()
            self._cache_ts.clear()


proxy_manager = ProxyManager()

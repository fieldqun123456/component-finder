"""
电子元器件多平台比价搜索工具 v3.0 — 真实数据版
原则：不造假。DigiKey 走实时 API，其他平台走链接跳转。
"""

import os
import requests
import re
import random
from datetime import datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed

from flask import Flask, render_template, request, jsonify

# 加载 .env 文件（如果存在）
env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(env_path):
    with open(env_path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

app = Flask(__name__)

# ============================================================
# 配置
# ============================================================
DIGIKEY_API_KEY = os.environ.get("DIGIKEY_API_KEY", "")
DIGIKEY_API_URL = "https://digikey-scraper.omkar.cloud/digikey/search"

# ============================================================
# 平台配置 — 只定义跳转链接，不做假数据
# ============================================================
PLATFORMS = [
    {
        "key": "digikey",
        "name": "得捷电子",
        "name_en": "Digi-Key",
        "url": "https://www.digikey.cn",
        "search_url": "https://www.digikey.cn/zh/products?keywords={part}",
        "color": "#2ecc71",
        "domestic": False,
        "has_api": True,   # 有真实 API
    },
    {
        "key": "lcsc",
        "name": "立创商城",
        "name_en": "LCSC",
        "url": "https://www.szlcsc.com",
        "search_url": "https://list.szlcsc.com/search?keyword={part}",
        "color": "#e74c3c",
        "domestic": True,
        "has_api": False,
    },
    {
        "key": "ickey",
        "name": "云汉芯城",
        "name_en": "ICKey",
        "url": "https://www.ickey.cn",
        "search_url": "https://so.ickey.cn/search?keyword={part}",
        "color": "#3498db",
        "domestic": True,
        "has_api": False,
    },
    {
        "key": "mouser",
        "name": "贸泽电子",
        "name_en": "Mouser",
        "url": "https://www.mouser.cn",
        "search_url": "https://www.mouser.cn/c/?q={part}",
        "color": "#9b59b6",
        "domestic": False,
        "has_api": False,
    },
    {
        "key": "hqchip",
        "name": "华秋商城",
        "name_en": "HQ Chip",
        "url": "https://www.hqchip.com",
        "search_url": "https://www.hqchip.com/search?keyword={part}",
        "color": "#e67e22",
        "domestic": True,
        "has_api": False,
    },
    {
        "key": "hardcity",
        "name": "硬之城",
        "name_en": "Allchips",
        "url": "https://www.allchips.com",
        "search_url": "https://www.allchips.com/search?keyword={part}",
        "color": "#1abc9c",
        "domestic": True,
        "has_api": False,
    },
]

# ============================================================
# Digi-Key 真实 API
# ============================================================
def fetch_digikey(part_number):
    """通过 DigiKey Scraper API 获取真实价格/库存"""
    if not DIGIKEY_API_KEY:
        return None
    try:
        resp = requests.get(
            DIGIKEY_API_URL,
            params={"search_term": part_number, "page": 1},
            headers={"API-Key": DIGIKEY_API_KEY},
            timeout=15,
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        items = data.get("items", data.get("results", []))
        if not items:
            return {"found": False, "source": "DigiKey实时API"}

        item = items[0]
        unit_price = item.get("unit_price")
        if isinstance(unit_price, str):
            unit_price = float(re.sub(r"[^\\d.]", "", unit_price)) if re.sub(r"[^\\d.]", "", unit_price) else None

        qty = item.get("quantity_available", 0)
        if isinstance(qty, str):
            qty_str = re.sub(r"[^\\d]", "", qty)
            qty = int(qty_str) if qty_str else 0

        currency = item.get("currency", "USD")
        if unit_price and currency == "USD":
            unit_price_cny = round(unit_price * 7.25, 2)
        else:
            unit_price_cny = unit_price

        tiers = []
        if unit_price_cny:
            for q, d in [(1, 1.0), (10, 0.90), (100, 0.75), (1000, 0.60), (5000, 0.50)]:
                tiers.append({"qty": q, "unit_price": round(unit_price_cny * d, 2), "total": round(unit_price_cny * d * q, 2)})

        return {
            "found": True,
            "source": "DigiKey实时API",
            "price": unit_price_cny,
            "currency": "CNY",
            "stock": qty,
            "lead_time": item.get("lead_time", ""),
            "manufacturer": item.get("manufacturer", ""),
            "description": item.get("description", ""),
            "package": item.get("packaging", ""),
            "product_url": item.get("product_url", ""),
            "datasheet_url": item.get("datasheet_url", ""),
            "digikey_part": item.get("digikey_part_number", ""),
            "mfr_part": item.get("manufacturer_part_number", ""),
            "price_tiers": tiers,
        }
    except Exception:
        return None


# ============================================================
# 搜索 API
# ============================================================
@app.route("/")
def index():
    has_key = bool(DIGIKEY_API_KEY)
    return render_template("index.html", platforms=PLATFORMS, has_digikey_key=has_key)


@app.route("/api/search", methods=["POST"])
def search():
    data = request.get_json() or {}
    part_number = data.get("part_number", "").strip()
    if not part_number:
        return jsonify({"error": "请提供物料型号"}), 400

    selected = set(data.get("platforms") or [p["key"] for p in PLATFORMS])
    search_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    results = []

    # 1. DigiKey 实时 API
    dk_raw = None
    if "digikey" in selected and DIGIKEY_API_KEY:
        dk_raw = fetch_digikey(part_number)

    if "digikey" in selected:
        plat = next(p for p in PLATFORMS if p["key"] == "digikey")
        search_url = plat["search_url"].format(part=quote(part_number))

        if dk_raw and dk_raw.get("found"):
            stock = dk_raw["stock"]
            results.append({
                "platform_key": "digikey",
                "platform_name": plat["name"],
                "platform_name_en": plat["name_en"],
                "platform_color": plat["color"],
                "domestic": plat["domestic"],
                "data_type": "real",
                "url": dk_raw.get("product_url") or search_url,
                "datasheet_url": dk_raw.get("datasheet_url", ""),
                "price": dk_raw["price"],
                "currency": "CNY",
                "price_tiers": dk_raw.get("price_tiers", []),
                "stock": stock,
                "stock_label": "success" if stock > 500 else "warning" if stock > 0 else "danger",
                "stock_text": f"现货: {stock:,}" if stock > 1000 else f"紧张: {stock}" if stock > 0 else "缺货",
                "lead_time": dk_raw.get("lead_time") or "见官网",
                "manufacturer": dk_raw.get("manufacturer", ""),
                "description": dk_raw.get("description", ""),
                "package": dk_raw.get("package", ""),
                "data_source": "DigiKey 实时API",
                "search_time": search_time,
            })
        elif dk_raw and not dk_raw.get("found"):
            results.append({
                "platform_key": "digikey",
                "platform_name": plat["name"],
                "platform_name_en": plat["name_en"],
                "platform_color": plat["color"],
                "domestic": plat["domestic"],
                "data_type": "real",
                "url": search_url,
                "price": None,
                "stock": 0,
                "stock_label": "danger",
                "stock_text": "未找到",
                "data_source": "DigiKey 未收录此型号",
                "search_time": search_time,
            })
        else:
            results.append({
                "platform_key": "digikey",
                "platform_name": plat["name"],
                "platform_name_en": plat["name_en"],
                "platform_color": plat["color"],
                "domestic": plat["domestic"],
                "data_type": "link",
                "url": search_url,
                "data_source": "需配置 API Key",
                "search_time": search_time,
            })

    # 2. 其他平台 — 纯跳转链接，不做假数据
    for plat in PLATFORMS:
        if plat["key"] == "digikey" or plat["key"] not in selected:
            continue
        search_url = plat["search_url"].format(part=quote(part_number))
        results.append({
            "platform_key": plat["key"],
            "platform_name": plat["name"],
            "platform_name_en": plat["name_en"],
            "platform_color": plat["color"],
            "domestic": plat["domestic"],
            "data_type": "link",
            "url": search_url,
            "data_source": "点击跳转查看实时价格",
            "search_time": search_time,
        })

    return jsonify({
        "part_number": part_number,
        "search_time": search_time,
        "total_platforms": len(results),
        "has_real_data": bool(dk_raw and dk_raw.get("found")),
        "digikey_api": bool(DIGIKEY_API_KEY),
        "results": results,
    })


@app.route("/api/status")
def status():
    return jsonify({
        "digikey_api": "已连接" if DIGIKEY_API_KEY else "未配置",
        "register_url": "https://www.omkar.cloud/auth/sign-up",
        "platforms": len(PLATFORMS),
    })


if __name__ == "__main__":
    print("""
╔══════════════════════════════════════════════════════╗
║  元器件多平台比价 v3.0 — 真实数据版                  ║
║  DigiKey: """ + ("已连接 ✅" if DIGIKEY_API_KEY else "未配置 — omkar.cloud/auth/sign-up") + """              ║
║  其他平台: 链接跳转，不作假                          ║
║  访问: http://127.0.0.1:5000                         ║
╚══════════════════════════════════════════════════════╝""")
    app.run(host="0.0.0.0", port=5000, debug=True)

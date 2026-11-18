"""
Обновляет stats.json настоящими цифрами из Shopify.
Запускается автоматически раз в час через GitHub Actions (.github/workflows/stats.yml).

Секреты берутся из настроек репозитория (Settings → Secrets and variables → Actions):
  SHOPIFY_STORE          адрес магазина, например motionpulse.myshopify.com
  SHOPIFY_TOKEN          готовый Admin API токен (shpat_...)            — или —
  SHOPIFY_CLIENT_ID      Client ID приложения из Dev Dashboard
  SHOPIFY_CLIENT_SECRET  Client Secret приложения
Ключи на сайт не попадают: этот файл их только читает из окружения.
"""
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_VERSION = "2025-10"
STORE = os.environ.get("SHOPIFY_STORE", "").strip().replace("https://", "").replace("http://", "").split("/")[0]


def http_json(url, payload, headers, form=False):
    if form:
        body, ctype = urllib.parse.urlencode(payload).encode(), "application/x-www-form-urlencoded"
    else:
        body, ctype = json.dumps(payload).encode(), "application/json"
    req = urllib.request.Request(url, data=body, headers={"Content-Type": ctype, "Accept": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        # текст ошибки от Shopify (ключей в нём нет) — чтобы в журнале было видно причину
        sys.exit(f"Shopify ответил {e.code} на {url.split('?')[0]}: {e.read().decode(errors='replace')[:500]}")


def get_token():
    token = os.environ.get("SHOPIFY_TOKEN", "").strip()
    secret = os.environ.get("SHOPIFY_CLIENT_SECRET", "").strip()
    if token.startswith("shpss_"):
        # в SHOPIFY_TOKEN положили API secret key, а не токен доступа — используем его как секрет
        print("SHOPIFY_TOKEN похож на API secret key (shpss_) — получаю токен через Client ID")
        secret, token = token, ""
    if token:
        return token
    cid = os.environ.get("SHOPIFY_CLIENT_ID", "").strip()
    if not (cid and secret):
        sys.exit("Нет ключей: добавьте SHOPIFY_TOKEN или SHOPIFY_CLIENT_ID + SHOPIFY_CLIENT_SECRET")
    data = http_json(f"https://{STORE}/admin/oauth/access_token",
                     {"grant_type": "client_credentials", "client_id": cid, "client_secret": secret}, {}, form=True)
    return data["access_token"]


def gql(token, query, variables=None):
    data = http_json(f"https://{STORE}/admin/api/{API_VERSION}/graphql.json",
                     {"query": query, "variables": variables or {}},
                     {"X-Shopify-Access-Token": token})
    if data.get("errors"):
        sys.exit("Ошибка Shopify: " + json.dumps(data["errors"], ensure_ascii=False))
    return data["data"]


def main():
    if not STORE:
        sys.exit("Нет SHOPIFY_STORE")
    token = get_token()
    # Безопасная подсказка для журнала: только тип токена и формат адреса, без самих ключей
    kind = token[:6] if token[:6] in ("shpat_", "shpca_", "shppa_", "shpss_") else "не shpat_ (похоже, это не Admin API access token)"
    print("Тип токена:", kind, "| адрес магазина вида *.myshopify.com:", STORE.endswith(".myshopify.com"))

    orders = gql(token, "{ ordersCount(limit: null) { count } }")["ordersCount"]["count"]
    print("Заказов в Shopify:", orders)

    countries, cursor = set(), None
    q = """query($after: String) {
      orders(first: 250, after: $after) {
        nodes {
          billingAddress { countryCodeV2 }
          shippingAddress { countryCodeV2 }
        }
        pageInfo { hasNextPage endCursor }
      }
    }"""
    while True:
        page = gql(token, q, {"after": cursor})["orders"]
        for o in page["nodes"]:
            for addr in (o.get("billingAddress"), o.get("shippingAddress")):
                if addr and addr.get("countryCodeV2"):
                    countries.add(addr["countryCodeV2"])
                    break
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    stats = {"orders": orders, "countries": len(countries), "updated": datetime.now(timezone.utc).strftime("%Y-%m-%d")}
    if stats["countries"] == 0:
        sys.exit("Страны не определились (нет доступа к адресам заказов) — stats.json не меняю")
    with open("stats.json", "w") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("stats.json:", stats)


if __name__ == "__main__":
    main()

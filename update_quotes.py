#!/usr/bin/env python3
"""
Atualiza a base local de cotações (SEED_QUOTES) dentro de index.html.

Fontes:
- Ações da B3            -> brapi.dev  (BRAPI_TOKEN)
- Ações dos EUA           -> Finnhub    (FINNHUB_TOKEN), convertidas pra BRL
- Criptomoedas            -> brapi.dev  (BRAPI_TOKEN), já em BRL

Roda dentro do GitHub Actions (veja .github/workflows/update-quotes.yml),
uma vez por dia, depois do fechamento da B3.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

HTML_PATH = "index.html"
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "").strip()
FINNHUB_TOKEN = os.environ.get("FINNHUB_TOKEN", "").strip()

# Tickers tratados como ações americanas (via Finnhub). Qualquer ticker do
# SEED_QUOTES que NÃO esteja aqui nem em CRYPTO_COINS é tratado como ação da B3.
US_TICKERS = {
    "AAPL", "MSFT", "GOOGL", "AMZN", "NVDA", "META", "TSLA", "JPM", "V", "MA",
    "WMT", "JNJ", "PG", "HD", "DIS", "NFLX", "KO", "PEP", "ADBE", "CRM",
    "INTC", "AMD", "CSCO", "ORCL", "IBM", "PYPL", "NKE", "MCD", "SBUX", "BA",
    "XOM", "CVX", "PFE", "ABT", "COST", "TGT", "UBER", "ABNB", "COIN", "PLTR",
    "SPOT", "SQ", "SHOP", "QCOM", "TXN", "AVGO", "BRKB",
}

# Tickers tratados como criptomoedas (via brapi.dev /v2/crypto)
CRYPTO_COINS = {
    "BTC", "ETH", "BNB", "XRP", "SOL", "DOGE", "ADA", "TRX", "LINK", "AVAX",
    "DOT", "MATIC", "LTC", "SHIB", "BCH", "UNI", "ATOM", "XLM", "ETC", "FIL",
    "APT", "ARB", "OP", "NEAR", "ICP",
}


def load_html():
    with open(HTML_PATH, encoding="utf-8") as f:
        return f.read()


def extract_current_tickers(html):
    m = re.search(r"const SEED_QUOTES=\{(.*?)\};", html, re.DOTALL)
    if not m:
        print("Não encontrei o bloco SEED_QUOTES em index.html — abortando.")
        sys.exit(1)
    body = m.group(1)
    tickers = re.findall(r"([A-Z0-9\^]+):\[([\d.]+),'((?:[^'\\]|\\.)*)'\]", body)
    current = {}
    for ticker, price, name in tickers:
        current[ticker] = (float(price), name.replace("\\'", "'"))
    return current


def fetch_usd_brl_rate():
    """Busca a cotação USD->BRL na brapi.dev. Se falhar, devolve None (chamador decide o fallback)."""
    headers = {"Authorization": f"Bearer {BRAPI_TOKEN}"} if BRAPI_TOKEN else {}
    try:
        resp = requests.get("https://brapi.dev/api/v2/currency?currency=USD-BRL", headers=headers, timeout=30)
        if resp.status_code == 200:
            data = resp.json()
            rate = data.get("currency", [{}])[0].get("bidPrice")
            if rate:
                return float(rate)
    except Exception as e:
        print(f"  aviso: falha ao buscar câmbio USD/BRL: {e}")
    return None


def fetch_b3_quotes(tickers):
    """1 ativo por requisição (limite do plano gratuito do brapi.dev)."""
    results = {}
    headers = {"Authorization": f"Bearer {BRAPI_TOKEN}"} if BRAPI_TOKEN else {}
    for i, ticker in enumerate(tickers):
        try:
            resp = requests.get(f"https://brapi.dev/api/quote/{ticker}", headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  aviso: {ticker} (B3) falhou com status {resp.status_code}")
                continue
            data = resp.json()
            for item in data.get("results", []):
                symbol = item.get("symbol") or item.get("stock")
                price = item.get("regularMarketPrice")
                name = item.get("shortName") or item.get("longName") or symbol
                if symbol and isinstance(price, (int, float)):
                    results[symbol] = (round(float(price), 2), name)
        except Exception as e:
            print(f"  aviso: erro em {ticker} (B3): {e}")
        time.sleep(0.25)
        if (i + 1) % 40 == 0:
            print(f"  progresso B3: {i + 1}/{len(tickers)}...")
    return results


def fetch_us_quotes(tickers, usd_brl_rate):
    """Finnhub: 1 requisição por ticker, preço vem em USD -> convertido pra BRL."""
    results = {}
    if not FINNHUB_TOKEN:
        print("  aviso: FINNHUB_TOKEN não configurado — ações dos EUA não serão atualizadas hoje.")
        return results
    for i, ticker in enumerate(sorted(tickers)):
        try:
            resp = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": ticker, "token": FINNHUB_TOKEN},
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"  aviso: {ticker} (EUA) falhou com status {resp.status_code}")
                continue
            data = resp.json()
            price_usd = data.get("c")
            if price_usd:
                results[ticker] = (round(float(price_usd) * usd_brl_rate, 2), ticker)
        except Exception as e:
            print(f"  aviso: erro em {ticker} (EUA): {e}")
        time.sleep(0.2)
        if (i + 1) % 20 == 0:
            print(f"  progresso EUA: {i + 1}/{len(tickers)}...")
    return results


def fetch_crypto_quotes(coins):
    """brapi.dev crypto: 1 moeda por requisição, já retorna em BRL."""
    results = {}
    headers = {"Authorization": f"Bearer {BRAPI_TOKEN}"} if BRAPI_TOKEN else {}
    for i, coin in enumerate(sorted(coins)):
        try:
            resp = requests.get(
                "https://brapi.dev/api/v2/crypto",
                params={"coin": coin, "currency": "BRL"},
                headers=headers,
                timeout=30,
            )
            if resp.status_code != 200:
                print(f"  aviso: {coin} (cripto) falhou com status {resp.status_code}")
                continue
            data = resp.json()
            for item in data.get("coins", []):
                price = item.get("regularMarketPrice")
                name = item.get("coinName") or coin
                if isinstance(price, (int, float)):
                    results[coin] = (round(float(price), 6 if price < 0.01 else 2), name)
        except Exception as e:
            print(f"  aviso: erro em {coin} (cripto): {e}")
        time.sleep(0.25)
    return results


def format_price(p):
    """Formata preço sem notação científica (crítico para moedas como SHIB, com preço < 0.01)."""
    s = f"{p:.10f}".rstrip("0").rstrip(".")
    return s if s else "0"


def build_seed_quotes_js(merged):
    items = []
    for ticker in sorted(merged.keys()):
        price, name = merged[ticker]
        name_escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        items.append(f"{ticker}:[{format_price(price)},'{name_escaped}']")
    return "const SEED_QUOTES={" + ",".join(items) + "};"


def main():
    if not BRAPI_TOKEN:
        print("BRAPI_TOKEN não configurado (repository secret ausente) — abortando sem alterar nada.")
        sys.exit(1)

    html = load_html()
    current = extract_current_tickers(html)
    all_tickers = set(current.keys())
    print(f"Tickers na base local: {len(all_tickers)}")

    us_tickers = all_tickers & US_TICKERS
    crypto_tickers = all_tickers & CRYPTO_COINS
    b3_tickers = all_tickers - US_TICKERS - CRYPTO_COINS

    print(f"  B3: {len(b3_tickers)} | EUA: {len(us_tickers)} | Cripto: {len(crypto_tickers)}")

    usd_brl_rate = fetch_usd_brl_rate() or 5.17  # fallback aproximado se a busca de câmbio falhar
    print(f"Câmbio USD/BRL usado: {usd_brl_rate}")

    fetched = {}
    fetched.update(fetch_b3_quotes(sorted(b3_tickers)))
    fetched.update(fetch_us_quotes(us_tickers, usd_brl_rate))
    fetched.update(fetch_crypto_quotes(crypto_tickers))

    print(f"Cotações obtidas com sucesso: {len(fetched)} de {len(all_tickers)}")

    # Mescla: usa o valor novo quando disponível, mantém o antigo como fallback
    merged = dict(current)
    merged.update(fetched)

    new_seed_js = build_seed_quotes_js(merged)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")

    html = re.sub(r"const SEED_QUOTES=\{.*?\};", new_seed_js, html, flags=re.DOTALL)
    html = re.sub(r"const SEED_DATE = '.*?';", f"const SEED_DATE = '{today}';", html)

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    if not fetched:
        print("Nenhuma cotação nova foi obtida — verifique os tokens ou as APIs. index.html não foi alterado de forma útil.")
        sys.exit(1)

    print(f"index.html atualizado com sucesso. Data de referência: {today}")


if __name__ == "__main__":
    main()

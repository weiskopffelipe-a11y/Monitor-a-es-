#!/usr/bin/env python3
"""
Atualiza a base local de cotações (SEED_QUOTES) dentro de index.html,
buscando os preços atuais na API brapi.dev.

Roda dentro do GitHub Actions (veja .github/workflows/update-quotes.yml),
uma vez por dia, depois do fechamento da B3.

Requer a variável de ambiente BRAPI_TOKEN (token gratuito de brapi.dev/dashboard,
guardado como "repository secret" no GitHub — nunca fica exposto no site).
"""
import json
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

HTML_PATH = "index.html"
CHUNK_SIZE = 40
BRAPI_TOKEN = os.environ.get("BRAPI_TOKEN", "").strip()


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
    # devolve dict ticker -> (preco_antigo, nome_antigo), pra usar de fallback se a API falhar
    current = {}
    for ticker, price, name in tickers:
        current[ticker] = (float(price), name.replace("\\'", "'"))
    return current


def chunk(lst, size):
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def fetch_quotes(tickers):
    """Busca cotações em lotes. Retorna dict ticker -> (preco, nome)."""
    results = {}
    headers = {"Authorization": f"Bearer {BRAPI_TOKEN}"} if BRAPI_TOKEN else {}
    for group in chunk(tickers, CHUNK_SIZE):
        url = f"https://brapi.dev/api/quote/{','.join(group)}"
        try:
            resp = requests.get(url, headers=headers, timeout=30)
            if resp.status_code != 200:
                print(f"  aviso: lote falhou com status {resp.status_code}: {group[:3]}...")
                continue
            data = resp.json()
            for item in data.get("results", []):
                symbol = item.get("symbol") or item.get("stock")
                price = item.get("regularMarketPrice")
                name = item.get("shortName") or item.get("longName") or symbol
                if symbol and isinstance(price, (int, float)):
                    results[symbol] = (round(float(price), 2), name)
        except Exception as e:
            print(f"  aviso: erro no lote {group[:3]}...: {e}")
    return results


def build_seed_quotes_js(merged):
    items = []
    for ticker in sorted(merged.keys()):
        price, name = merged[ticker]
        name_escaped = name.replace("\\", "\\\\").replace("'", "\\'")
        items.append(f"{ticker}:[{price},'{name_escaped}']")
    return "const SEED_QUOTES={" + ",".join(items) + "};"


def main():
    if not BRAPI_TOKEN:
        print("BRAPI_TOKEN não configurado (repository secret ausente) — abortando sem alterar nada.")
        sys.exit(1)

    html = load_html()
    current = extract_current_tickers(html)
    tickers = list(current.keys())
    print(f"Tickers na base local: {len(tickers)}")

    fetched = fetch_quotes(tickers)
    print(f"Cotações obtidas com sucesso: {len(fetched)} de {len(tickers)}")

    # Mescla: usa o valor novo quando disponível, mantém o antigo como fallback
    merged = dict(current)
    merged.update(fetched)

    new_seed_js = build_seed_quotes_js(merged)
    today = datetime.now(ZoneInfo("America/Sao_Paulo")).strftime("%d/%m/%Y")

    html = re.sub(r"const SEED_QUOTES=\{.*?\};", new_seed_js, html, flags=re.DOTALL)
    html = re.sub(r"const SEED_DATE = '.*?';", f"const SEED_DATE = '{today}';", html)
    # também atualiza o texto do badge visível na tela, se a data antiga aparecer lá
    html = re.sub(
        r"fechamento de \d{2}/\d{2}/\d{4}",
        f"fechamento de {today}",
        html,
    )

    with open(HTML_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    if not fetched:
        print("Nenhuma cotação nova foi obtida — verifique o token ou a API. index.html não foi alterado de forma útil.")
        sys.exit(1)

    print(f"index.html atualizado com sucesso. Data de referência: {today}")


if __name__ == "__main__":
    main()

"""
data_contract.py — Paso 1 del roadmap: construcción del payload
Intelligence Layer / Arkad Tools

build_payload(asset_key) es el único punto de entrada. Devuelve un dict
JSON-serializable con todo lo que el motor de caracterización de Estado
(doc fuente, Sección 1) necesita: 3 snapshots (current/w4/w12) por fuente,
calendario reciente + upcoming, correlaciones con zscore/percentil
calculado on-the-fly (no persistido — ver decisión de arquitectura sobre
pricing_correlations), ETF Flows (3 snapshots por ticker, solo para
activos con 'etf_tickers' en su config — hoy únicamente BTC; la clave
'etf_flows' directamente NO aparece en el payload del resto), y
geopolitics (lista de eventos de geopolitical_events —
GDELT/Federal Register/MOFCOM — solo para activos con
'geopolitics_lookback_days' en su config; [] para el resto o si no hay
eventos en la ventana).

No hace ningún juicio de fase/modificador/convicción — eso es trabajo
del motor LLM (Paso 2). Este módulo solo junta datos crudos de métricas.
"""

import statistics
from datetime import date, timedelta, datetime, timezone

from config import get_asset_config, SNAPSHOT_OFFSETS_DAYS
from supabase_clients import get_data_layer_client


# ---------------------------------------------------------------------------
# Helpers genéricos
# ---------------------------------------------------------------------------

def _latest_row_on_or_before(client, table: str, date_col: str, filters: dict, on_or_before: date):
    query = client.table(table).select("*")
    for col, val in filters.items():
        query = query.eq(col, val)
    query = query.lte(date_col, on_or_before.isoformat())
    query = query.order(date_col, desc=True).limit(1)
    resp = query.execute()
    return resp.data[0] if resp.data else None


def _snapshots(client, table: str, date_col: str, filters: dict, today: date) -> dict:
    """Devuelve {'current': row|None, 'w4': row|None, 'w12': row|None}."""
    out = {}
    for label, offset_days in SNAPSHOT_OFFSETS_DAYS.items():
        target_date = today - timedelta(days=offset_days)
        out[label] = _latest_row_on_or_before(client, table, date_col, filters, target_date)
    return out


# ---------------------------------------------------------------------------
# Fuentes individuales
# ---------------------------------------------------------------------------

def _get_pricing(client, cfg: dict, today: date) -> dict:
    filters = {"symbol": cfg["pricing_symbol"]}
    return _snapshots(client, "pricing_metrics", "date", filters, today)


def _get_cot(client, cfg: dict, today: date) -> dict:
    filters = {"symbol": cfg["cot_symbol"], "dataset_type": cfg["cot_dataset_type"]}
    return _snapshots(client, "cot_metrics", "report_date", filters, today)


def _get_fred(client, cfg: dict, today: date) -> dict:
    out = {}
    for series_id in cfg["fred_series"]:
        filters = {"series_id": series_id}
        out[series_id] = _snapshots(client, "fred_metrics", "observation_date", filters, today)
    return out


def _get_etf_flows(client, cfg: dict, asset_key: str, today: date) -> dict | None:
    """
    Snapshots current/w4/w12 de etf_flows_metrics, por ticker, solo para
    activos que declaran 'etf_tickers' en su ASSET_CONFIGS (hoy: solo BTC,
    ticker agregado 'TOTAL' — ver ETF_Flows/etf_flows_metrics_calculator.py,
    que en V1 solo calcula (asset, ticker) = (BTC, TOTAL)).

    Devuelve None para el resto de los 14 activos (no tienen 'etf_tickers'
    en su config) — build_payload() usa ese None para NO agregar la clave
    'etf_flows' al payload de esos activos (ver más abajo), en vez de
    agregarla con valor null.

    El filtro 'asset' usa asset_key directamente (coincide 1:1 con la
    columna 'asset' de etf_flows_metrics, ej. "BTC" == "BTC") — no hace
    falta un identificador aparte en cfg como pricing_symbol/cot_symbol,
    porque a diferencia de esas tablas acá no hay divergencia de nombre.
    """
    tickers = cfg.get("etf_tickers")
    if not tickers:
        return None

    out = {}
    for ticker in tickers:
        filters = {"asset": asset_key, "ticker": ticker}
        out[ticker] = _snapshots(client, "etf_flows_metrics", "date", filters, today)
    return out


def _get_geopolitics(client, cfg: dict, asset_key: str, today: date) -> list:
    """
    Eventos de geopolitical_events (GDELT + Federal Register + MOFCOM,
    Data Layer) relevantes para este activo, en la ventana de lookback.

    El filtro 'asset' ya viene pre-curado por el Data Layer (ver
    CATEGORY_ASSET_MAP en GDELT/config.py y Policy_watch/config.py) — acá
    solo se lee, no se decide relevancia ni se duplica ese mapeo.

    Solo se llama para activos con 'geopolitics_lookback_days' en su
    config (opt-in explícito, mismo patrón que 'etf_tickers' para ETF
    Flows). Si no está seteado, devuelve [] sin consultar Supabase.

    Devuelve [] tanto si el activo no está habilitado como si está
    habilitado pero no hay eventos en la ventana — ambos son resultados
    válidos y esperados, no huecos de datos (la mayoría de los activos,
    la mayoría de los días, no tienen eventos geopolíticos relevantes).
    """
    lookback = cfg.get("geopolitics_lookback_days")
    if lookback is None:
        return []

    since = today - timedelta(days=lookback)
    fields = (
        "source, category, role, event_date, event_count, "
        "avg_goldstein, max_abs_goldstein, total_mentions, avg_tone, "
        "narrative, actor1, actor2, source_urls"
    )
    resp = (
        client.table("geopolitical_events")
        .select(fields)
        .eq("asset", asset_key)
        .gte("event_date", since.isoformat())
        .lte("event_date", today.isoformat())
        .order("event_date", desc=True)
        .execute()
    )
    return resp.data or []


def _get_sentiment(client, cfg: dict, today: date) -> dict:
    """
    sentiment_metrics no tiene columna symbol — es un indicador (general o
    específico de cripto), filtrado por 'indicator'. Default 'fear_and_greed'
    (CNN, mercado general) para no romper assets existentes; BTC pasa
    'crypto_fear_and_greed' vía cfg.
    """
    filters = {"indicator": cfg.get("sentiment_indicator", "fear_and_greed")}
    return _snapshots(client, "sentiment_metrics", "date", filters, today)


def _get_calendar_recent(client, cfg: dict, today: date) -> list:
    since = today - timedelta(days=cfg["calendar_lookback_days"])
    resp = (
        client.table("calendar_metrics")
        .select("*")
        .in_("currency", cfg["calendar_currencies"])
        .eq("impact", cfg["calendar_impact_filter"])
        .gte("event_date", since.isoformat())
        .lte("event_date", today.isoformat())
        .order("event_date", desc=True)
        .execute()
    )
    return resp.data or []


def _get_calendar_upcoming(client, cfg: dict, today: date) -> list:
    until = today + timedelta(days=cfg["calendar_forward_days"])
    # select explícito: excluye raw_payload (HTML/tags de la fuente, ruido
    # puro para el motor) y otros campos operativos que no aportan señal.
    fields = (
        "event_name, currency, country_name, event_group, impact, "
        "release_datetime, event_date, event_status, "
        "actual, forecast, previous, surprise"
    )
    resp = (
        client.table("economic_calendar_events")
        .select(fields)
        .in_("currency", cfg["calendar_currencies"])
        .eq("impact", cfg["calendar_impact_filter"])
        .eq("event_status", "upcoming")
        .gte("event_date", today.isoformat())
        .lte("event_date", until.isoformat())
        .order("event_date", desc=False)
        .execute()
    )
    return resp.data or []


def _fetch_correlation_pair_row(client, symbol_a: str, symbol_b: str, on_or_before: date):
    resp = (
        client.table("pricing_correlations")
        .select("*")
        .or_(
            f"and(symbol_a.eq.{symbol_a},symbol_b.eq.{symbol_b}),"
            f"and(symbol_a.eq.{symbol_b},symbol_b.eq.{symbol_a})"
        )
        .lte("date", on_or_before.isoformat())
        .order("date", desc=True)
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def _fetch_correlation_history(client, symbol_a: str, symbol_b: str, since: date, until: date) -> list:
    resp = (
        client.table("pricing_correlations")
        .select("date, corr_252d")
        .or_(
            f"and(symbol_a.eq.{symbol_a},symbol_b.eq.{symbol_b}),"
            f"and(symbol_a.eq.{symbol_b},symbol_b.eq.{symbol_a})"
        )
        .gte("date", since.isoformat())
        .lte("date", until.isoformat())
        .order("date", desc=False)
        .execute()
    )
    return resp.data or []


def _zscore_and_percentile(current_value: float, history_values: list) -> dict:
    """Calcula zscore/percentil del valor actual contra su propia historia.
    On-the-fly, sin persistir (decisión de arquitectura — ver conversación).
    """
    clean = [v for v in history_values if v is not None]
    if len(clean) < 30 or current_value is None:
        return {"zscore": None, "percentile": None, "sample_size": len(clean)}

    mean = statistics.mean(clean)
    stdev = statistics.stdev(clean) if len(clean) > 1 else 0.0
    zscore = (current_value - mean) / stdev if stdev > 0 else 0.0

    rank = sum(1 for v in clean if v <= current_value)
    percentile = round(100 * rank / len(clean), 1)

    return {"zscore": round(zscore, 2), "percentile": percentile, "sample_size": len(clean)}


def _get_correlations(client, cfg: dict, today: date) -> dict:
    corr_symbol = cfg["correlation_symbol"]
    history_since = today - timedelta(days=730)  # ~2 años para zscore on-the-fly

    out = {}
    for other_symbol in cfg["correlation_pairs"]:
        current_row = _fetch_correlation_pair_row(client, corr_symbol, other_symbol, today)
        current_corr = float(current_row["corr_252d"]) if current_row and current_row.get("corr_252d") is not None else None

        history_rows = _fetch_correlation_history(client, corr_symbol, other_symbol, history_since, today)
        history_values = [float(r["corr_252d"]) for r in history_rows if r.get("corr_252d") is not None]

        stats = _zscore_and_percentile(current_corr, history_values)

        out[other_symbol] = {
            "corr_20d": float(current_row["corr_20d"]) if current_row and current_row.get("corr_20d") is not None else None,
            "corr_60d": float(current_row["corr_60d"]) if current_row and current_row.get("corr_60d") is not None else None,
            "corr_252d": current_corr,
            "corr_252d_zscore_2y": stats["zscore"],
            "corr_252d_percentile_2y": stats["percentile"],
            "as_of": current_row["date"] if current_row else None,
        }
    return out


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_payload(asset_key: str, as_of: date | None = None) -> dict:
    """
    Construye el payload JSON completo para el Asset Translator.

    asset_key: clave en ASSET_CONFIGS (ej. "GOLD")
    as_of:     fecha de referencia (default: hoy). Útil para backtesting.
    """
    cfg = get_asset_config(asset_key)
    today = as_of or date.today()
    client = get_data_layer_client()

    payload = {
        "asset_key": asset_key,
        "display_name": cfg["display_name"],
        # Corrección Aug 2026: pasa las limitaciones conocidas del Data
        # Layer (antes solo comentarios en config.py, invisibles para el
        # modelo) como dato real del payload — la Llamada 2 lo lee en su
        # prompt para no presentar un proxy como si fuera el dato que
        # aproxima (ver engine._SYSTEM_PROMPT_LLAMADA_2). Ausente/lista
        # vacía si el activo no tiene ninguna declarada.
        "known_limitations": cfg.get("known_limitations", []),
        "as_of": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pricing": _get_pricing(client, cfg, today),
        "cot": {
            "primary_field_prefix": cfg["cot_primary_field_prefix"],
            "secondary_field_prefix": cfg["cot_secondary_field_prefix"],
            "snapshots": _get_cot(client, cfg, today),
        },
        "fred": _get_fred(client, cfg, today),
        "sentiment": _get_sentiment(client, cfg, today),
        "geopolitics": _get_geopolitics(client, cfg, asset_key, today),
        "correlations": _get_correlations(client, cfg, today),
        "calendar_recent": _get_calendar_recent(client, cfg, today),
        "calendar_upcoming": _get_calendar_upcoming(client, cfg, today),
    }

    # CORRECCIÓN (encontrada en smoke test real, GOLD): antes esta clave
    # se agregaba siempre, con valor None para los 14 activos sin
    # 'etf_tickers'. Eso hacía que la Llamada 2 viera "etf_flows": null
    # en el payload y lo interpretara como un hueco de evidencia real
    # ("dato faltante"), cuando en realidad es estructuralmente no
    # aplicable para ese activo — no hay nada faltando, nunca debió
    # existir. Se omite la clave por completo cuando no aplica, mismo
    # criterio que ya usa 'geopolitics' (opt-in vía config, ausente si
    # no corresponde) en vez de mandar un null ambiguo.
    etf_flows = _get_etf_flows(client, cfg, asset_key, today)
    if etf_flows is not None:
        payload["etf_flows"] = etf_flows

    return payload


if __name__ == "__main__":
    import json
    import sys
    asset_key = sys.argv[1] if len(sys.argv) > 1 else "GOLD"
    result = build_payload(asset_key)
    print(json.dumps(result, indent=2, default=str))
"""
data_contract.py — Paso 1 del roadmap: construcción del payload
Intelligence Layer / Arkad Tools

build_payload(asset_key) es el único punto de entrada. Devuelve un dict
JSON-serializable con todo lo que el motor de caracterización de Estado
(doc fuente, Sección 1) necesita: 3 snapshots (current/w4/w12) por fuente,
calendario reciente + upcoming, y correlaciones con zscore/percentil
calculado on-the-fly (no persistido — ver decisión de arquitectura sobre
pricing_correlations).

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


def _get_sentiment(client, today: date) -> dict:
    """
    sentiment_metrics no tiene columna symbol — es un indicador de mercado
    general (fear_and_greed), no específico de Oro. Se trae igual como
    contexto macro, filtrando por indicator en vez de symbol.
    """
    filters = {"indicator": "fear_and_greed"}
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
        "as_of": today.isoformat(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "pricing": _get_pricing(client, cfg, today),
        "cot": {
            "primary_field_prefix": cfg["cot_primary_field_prefix"],
            "secondary_field_prefix": cfg["cot_secondary_field_prefix"],
            "snapshots": _get_cot(client, cfg, today),
        },
        "fred": _get_fred(client, cfg, today),
        "sentiment": _get_sentiment(client, today),
        "correlations": _get_correlations(client, cfg, today),
        "calendar_recent": _get_calendar_recent(client, cfg, today),
        "calendar_upcoming": _get_calendar_upcoming(client, cfg, today),
    }
    return payload


if __name__ == "__main__":
    import json
    result = build_payload("GOLD")
    print(json.dumps(result, indent=2, default=str))
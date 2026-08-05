"""
persistence.py — Paso 4: escritura/lectura en Intelligence Layer
Intelligence Layer / Arkad Tools

Adaptado a schema_version=2 (Sentinel Market State Model, Documento 3).

Regla de bootstrap (Documento 3 §7.1): si la fila guardada en
asset_outputs para un activo tiene schema_version=1 (o no existe
ninguna fila), get_previous_state()/get_previous_hypothesis() devuelven
None — el motor lo trata como primera corrida, nunca intenta interpretar
un 'fase' V1 como si fuera un 'estado' V2.

Funciones públicas:
  get_previous_hypothesis(asset_key) -> dict | None
  get_previous_state(asset_key)      -> dict | None
  save_run(asset_key, payload, output, elasticity_flags=None) -> dict
"""

from datetime import date, datetime, timezone

from output_schema import AssetTranslatorOutput
from evidence_map import EVIDENCE_TIER_VERSION
from supabase_clients import get_intel_layer_client
from data_contract import build_payload
from engine import run_engine
from elasticity import compute_elasticity_flags

CURRENT_SCHEMA_VERSION = 2


def get_previous_hypothesis(asset_key: str) -> dict | None:
    """
    Lee la última corrida guardada (asset_outputs). Devuelve
    {"as_of": str, "hipotesis": dict, "paranoia": dict,
    "invalidaria_check": dict|None} listo para pasarle a
    precheck.check_invalidacion_confirmada(), o None si es la primera
    corrida V2 para este activo (no hay fila, o la fila es
    schema_version=1 — Documento 3 §7.1).
    """
    client = get_intel_layer_client()
    resp = (
        client.table("asset_outputs")
        .select("hipotesis, paranoia, schema_version, as_of")
        .eq("asset_key", asset_key)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None

    row = resp.data[0]
    if row["schema_version"] != CURRENT_SCHEMA_VERSION:
        return None

    hipotesis = row["hipotesis"] or {}
    return {
        "as_of": row["as_of"],
        "hipotesis": hipotesis,
        "paranoia": row["paranoia"],
        "invalidaria_check": hipotesis.get("invalidaria_check"),
    }


def get_previous_state(asset_key: str) -> dict | None:
    """
    Lee estado/estado_provisional_hacia/conviccion de la última corrida
    (asset_outputs). Insumo de engine.run_engine() (estado_previo) y de
    elasticity.compute_elasticity_flags(). None si es la primera corrida
    V2 para este activo — mismo criterio de bootstrap que
    get_previous_hypothesis().
    """
    client = get_intel_layer_client()
    resp = (
        client.table("asset_outputs")
        .select("estado, estado_provisional_hacia, modificadores, conviccion, schema_version, as_of")
        .eq("asset_key", asset_key)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None

    row = resp.data[0]
    if row["schema_version"] != CURRENT_SCHEMA_VERSION:
        return None

    return {
        "estado": row["estado"],
        "estado_provisional_hacia": row["estado_provisional_hacia"],
        "modificadores": row["modificadores"],
        "conviccion": row["conviccion"],
        "as_of": row["as_of"],
    }


def save_run(
    asset_key: str,
    payload: dict,
    output: AssetTranslatorOutput,
    elasticity_flags: dict | None = None,
) -> dict:
    """
    Persiste una corrida V2 completa en las 3 tablas. Nunca escribe en
    la columna vieja 'fase' — queda NULL para filas schema_version=2,
    tal como define la migración (migration_v2_schema.sql). El
    ensamblado estado+contexto de AssetTranslatorOutput se aplana acá
    para las columnas de consulta rápida, y se guarda completo (nested)
    en asset_history.full_output para auditoría.
    """
    client = get_intel_layer_client()
    today = date.today()
    now = datetime.now(timezone.utc).isoformat()

    output_dict = output.model_dump(mode="json")
    estado, contexto = output.estado, output.contexto

    # --- asset_outputs (upsert, 1 fila por activo) ---
    outputs_row = {
        "asset_key": asset_key,
        "display_name": payload.get("display_name", asset_key),
        "as_of": today.isoformat(),
        "generated_at": now,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "estado": estado.estado,
        "estado_provisional_hacia": estado.estado_provisional_hacia,
        "evidence_map_version": EVIDENCE_TIER_VERSION,
        "modificadores": contexto.modificadores,
        "conviccion": estado.conviccion,
        "evidence_keys": estado.evidence_keys,
        "frase_puente": contexto.frase_puente,
        "traduccion_macro": contexto.traduccion_macro,
        "en_criollo": contexto.en_criollo,
        "hipotesis": output_dict["contexto"]["hipotesis"],
        "paranoia": output_dict["contexto"]["paranoia"],
        "memoria": output_dict["contexto"]["memoria"],
        "updated_at": now,
        # 'fase' deliberadamente omitido: queda NULL, Documento 3 §7.
    }
    client.table("asset_outputs").upsert(outputs_row, on_conflict="asset_key").execute()

    # --- asset_history (upsert por asset_key+as_of) ---
    history_full_output = output_dict
    if elasticity_flags is not None:
        history_full_output = {**output_dict, "elasticity_flags": elasticity_flags}

    history_row = {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "generated_at": now,
        "schema_version": CURRENT_SCHEMA_VERSION,
        "estado": estado.estado,
        "estado_provisional_hacia": estado.estado_provisional_hacia,
        "evidence_map_version": EVIDENCE_TIER_VERSION,
        "modificadores": contexto.modificadores,
        "conviccion": estado.conviccion,
        "full_output": history_full_output,
    }
    client.table("asset_history").upsert(history_row, on_conflict="asset_key,as_of").execute()

    # --- hypotheses_history (upsert por asset_key+as_of) ---
    hipotesis_dict = output_dict["contexto"]["hipotesis"]
    hyp_row = {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "sostiene": contexto.hipotesis.sostiene,
        "debilita": contexto.hipotesis.debilita,
        "invalidaria": contexto.hipotesis.invalidaria,
        "invalidaria_check": hipotesis_dict.get("invalidaria_check"),
        "podria_acelerarla": contexto.hipotesis.podria_acelerarla,
        "variable_a_vigilar": contexto.paranoia.variable_a_vigilar,
        "contexto_limpio": contexto.paranoia.contexto_limpio,
        "estado_hipotesis_previa": contexto.memoria.estado_hipotesis_previa,
    }
    client.table("hypotheses_history").upsert(hyp_row, on_conflict="asset_key,as_of").execute()

    return {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "schema_version": CURRENT_SCHEMA_VERSION,
        "tables_written": ["asset_outputs", "asset_history", "hypotheses_history"],
    }


def run_daily_for_asset(asset_key: str, dry_run: bool = False) -> dict:
    """
    Corrida completa V2 para un activo: Etapa 0 + Llamada 1 + Llamada 2
    (vía engine.run_engine) + elasticity flags. Si dry_run=True, no llama
    a save_run() — imprime el resultado para revisión manual, mismo
    espíritu que smoke_test_gold.py pero para los 15 activos en un loop.
    """
    payload = build_payload(asset_key)
    previous_state = get_previous_state(asset_key)
    previous_hypothesis = get_previous_hypothesis(asset_key)

    output = run_engine(asset_key, payload, previous_state, previous_hypothesis)

    today_estado = {
        "estado": output.estado.estado,
        "modificadores": output.contexto.modificadores,
        "conviccion": output.estado.conviccion,
    }
    elasticity_flags = compute_elasticity_flags(today_estado, previous_state, payload)

    if dry_run:
        print(
            f"[persistence:{asset_key}] DRY-RUN — no se persiste. "
            f"estado={output.estado.estado} conviccion={output.estado.conviccion} "
            f"modificadores={output.contexto.modificadores}"
        )
        return {
            "status": "dry_run_ok",
            "estado": output.estado.estado,
            "conviccion": output.estado.conviccion,
            "modificadores": output.contexto.modificadores,
            "elasticity_flags": elasticity_flags,
        }

    result = save_run(asset_key, payload, output, elasticity_flags=elasticity_flags)
    print(f"[persistence:{asset_key}] Guardado OK: {result}")
    return {"status": "saved", **result}


if __name__ == "__main__":
    import os
    import sys
    import time
    import traceback

    from config import ASSET_CONFIGS

    # Mismo criterio que weekly_persistence.py: pausa entre activos para
    # no ráfagear el free tier de Gemini.
    DELAY_BETWEEN_ASSETS_SECONDS = 15

    dry_run = "--dry-run" in sys.argv or os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true")

    asset_keys = list(ASSET_CONFIGS.keys())

    # ASSET_FILTER (incluir) / ASSET_EXCLUDE (excluir): listas separadas por
    # comas de asset_keys. Mismo patrón que weekly_persistence.py — permite
    # correr un subconjunto (ej. el job daily-engine-btc-weekend con
    # ASSET_FILTER=BTC) sin duplicar este archivo.
    _asset_filter = os.environ.get("ASSET_FILTER", "").strip()
    if _asset_filter:
        _requested = {a.strip().upper() for a in _asset_filter.split(",") if a.strip()}
        asset_keys = [a for a in asset_keys if a in _requested]

    _asset_exclude = os.environ.get("ASSET_EXCLUDE", "").strip()
    if _asset_exclude:
        _excluded = {a.strip().upper() for a in _asset_exclude.split(",") if a.strip()}
        asset_keys = [a for a in asset_keys if a not in _excluded]

    if not asset_keys:
        raise SystemExit(
            "ASSET_FILTER/ASSET_EXCLUDE no dejaron ningún asset_key para correr."
        )

    if dry_run:
        print("[persistence] DRY-RUN activado — no se llama a save_run() para ningún activo.")

    results: dict[str, dict] = {}
    for i, asset_key in enumerate(asset_keys):
        try:
            results[asset_key] = run_daily_for_asset(asset_key, dry_run=dry_run)
        except Exception as exc:
            print(f"[persistence:{asset_key}] FALLÓ: {exc}")
            traceback.print_exc()
            results[asset_key] = {"status": "error", "error": str(exc)}

        is_last = i == len(asset_keys) - 1
        if not is_last:
            time.sleep(DELAY_BETWEEN_ASSETS_SECONDS)

    print("\n=== Resumen Daily Engine (V2) ===")
    for asset_key, result in results.items():
        status = result.get("status", "?")
        if status in ("saved", "dry_run_ok"):
            print(f"  {asset_key}: {status} — estado={result.get('estado')} conviccion={result.get('conviccion')}")
        else:
            print(f"  {asset_key}: ERROR — {result.get('error')}")
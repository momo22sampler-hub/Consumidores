"""
weekly_persistence.py — Orquestación oportunista + persistencia del Weekly Recap
Intelligence Layer / Arkad Tools

Funciones públicas:
  get_week_range(asset_key, today)                    -> (week_start, week_end)
  get_daily_runs(asset_key, week_start, week_end)      -> list[dict] (asset_history)
  get_hypotheses_week(asset_key, week_start, week_end) -> list[dict] (hypotheses_history)
  get_previous_weekly_recap(asset_key, week_start)     -> dict | None
  save_weekly_recap(...)                               -> dict
  generate_and_save_weekly(asset_key)                  -> dict (esto llama el cron)

Principio oportunista: si asset_history no tiene NINGÚN registro para
este activo en la ventana de la semana — ya sea porque el motor diario
todavía no existe, o porque no corrió esta semana puntual —
generate_and_save_weekly() NO genera un recap y devuelve
status="skipped_no_data", sin lanzar excepción. Esto permite tener los
22 asset_keys de config.py preparados desde ahora sin acoplar el
Weekly Recap al orden en que se vayan implementando los motores diarios.

No hay calendario de feriados ni de medios días (decisión de diseño):
runs_used es simplemente COUNT(*) de lo que exista en la ventana. La
diferencia entre una semana con feriado y una semana con un motor
caído es indistinguible para esta capa — para eso está weekly_confidence
+ nota_completitud, no un calendario aparte.

Estado actual: el __main__ corre generate_and_save_weekly() para TODOS
los asset_keys en config.ASSET_CONFIGS, un activo a la vez, con pausa
entre llamadas — mismo criterio que persistence.py del motor diario.
Los activos sin datos esta semana devuelven skipped_no_data al toque,
sin gastar cuota de Gemini.
"""

from datetime import date, datetime, timedelta, timezone

from config import get_asset_config
from supabase_clients import get_intel_layer_client
from weekly_engine import run_weekly_engine


def get_week_range(asset_key: str, today: date | None = None) -> tuple[date, date]:
    """
    Semana ISO (lunes a domingo) que contiene `today`. Misma ventana de
    fechas para todos los activos — weekly_expected_runs (5 o 7) no
    cambia la ventana, solo cuántas corridas se esperan encontrar adentro.
    """
    today = today or date.today()
    week_start = today - timedelta(days=today.weekday())  # lunes
    week_end = week_start + timedelta(days=6)              # domingo
    return week_start, week_end


def get_daily_runs(asset_key: str, week_start: date, week_end: date) -> list[dict]:
    client = get_intel_layer_client()
    resp = (
        client.table("asset_history")
        .select("as_of, generated_at, fase, modificadores, conviccion, full_output")
        .eq("asset_key", asset_key)
        .gte("as_of", week_start.isoformat())
        .lte("as_of", week_end.isoformat())
        .order("as_of", desc=False)
        .execute()
    )
    return resp.data or []


def get_hypotheses_week(asset_key: str, week_start: date, week_end: date) -> list[dict]:
    client = get_intel_layer_client()
    resp = (
        client.table("hypotheses_history")
        .select(
            "as_of, sostiene, debilita, invalidaria, podria_acelerarla, "
            "variable_a_vigilar, contexto_limpio, estado_hipotesis_previa"
        )
        .eq("asset_key", asset_key)
        .gte("as_of", week_start.isoformat())
        .lte("as_of", week_end.isoformat())
        .order("as_of", desc=False)
        .execute()
    )
    return resp.data or []


def get_previous_weekly_recap(asset_key: str, week_start: date) -> dict | None:
    """Trae el weekly_recap de la semana inmediatamente anterior, si existe."""
    client = get_intel_layer_client()
    previous_week_start = week_start - timedelta(days=7)
    resp = (
        client.table("weekly_recap")
        .select("*")
        .eq("asset_key", asset_key)
        .eq("week_start", previous_week_start.isoformat())
        .limit(1)
        .execute()
    )
    return resp.data[0] if resp.data else None


def save_weekly_recap(
    asset_key: str,
    display_name: str,
    week_start: date,
    week_end: date,
    runs_expected: int,
    runs_used: int,
    output,
) -> dict:
    """
    generated_at se setea explícito acá (no se deja al DEFAULT now() de la
    columna): como esto es un UPSERT, el DEFAULT solo aplica en el INSERT
    inicial de la semana — si el recap se regenera a mitad de semana
    (patrón oportunista normal: 2 corridas hoy, 5 el viernes), el timestamp
    quedaría congelado en la primera corrida sin este seteo explícito.
    """
    client = get_intel_layer_client()
    output_dict = output.model_dump(mode="json")

    row = {
        "asset_key": asset_key,
        "display_name": display_name,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "runs_expected": runs_expected,
        "runs_used": runs_used,
        "weekly_confidence": output.weekly_confidence,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lo_mas_importante": output_dict["lo_mas_importante"],
        "que_aprendio_sentinel": output.que_aprendio_sentinel,
        "narrativas_cambiaron": output.narrativas_cambiaron,
        "evolucion_hipotesis": output_dict["evolucion_hipotesis"],
        "sorpresas_semana": output.sorpresas_semana,
        "comparacion_semana_anterior": output.comparacion_semana_anterior,
        "variables_a_vigilar": output_dict["variables_a_vigilar"],
        "full_output": output_dict,
    }
    # upsert por (asset_key, week_start): si el cron corre 2 veces la
    # misma semana, pisa en vez de duplicar.
    client.table("weekly_recap").upsert(row, on_conflict="asset_key,week_start").execute()

    return {
        "asset_key": asset_key,
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "status": "generated",
        "runs_used": runs_used,
        "runs_expected": runs_expected,
        "weekly_confidence": output.weekly_confidence,
    }


def generate_and_save_weekly(asset_key: str, today: date | None = None, verbose: bool = True) -> dict:
    """
    Orquestador oportunista — esto es lo que llama el cron el día que
    exista, una vez por activo (22 veces, mismo patrón que
    generate_and_save() del motor diario). Por ahora, corrida manual.
    """
    def log(msg: str) -> None:
        if verbose:
            print(f"[weekly:{asset_key}] {msg}")

    cfg = get_asset_config(asset_key)
    week_start, week_end = get_week_range(asset_key, today)

    log(f"Buscando corridas diarias entre {week_start} y {week_end}...")
    daily_runs = get_daily_runs(asset_key, week_start, week_end)

    if not daily_runs:
        log("Sin registros en asset_history para esta ventana — skip oportunista.")
        return {
            "asset_key": asset_key,
            "week_start": week_start.isoformat(),
            "week_end": week_end.isoformat(),
            "status": "skipped_no_data",
        }

    runs_expected = cfg["weekly_expected_runs"]
    runs_used = len(daily_runs)

    hypotheses_week = get_hypotheses_week(asset_key, week_start, week_end)
    previous_weekly_recap = get_previous_weekly_recap(asset_key, week_start)

    log(f"{runs_used}/{runs_expected} corridas encontradas. Generando recap...")
    output = run_weekly_engine(
        asset_display_name=cfg["display_name"],
        week_start=week_start,
        week_end=week_end,
        daily_runs=daily_runs,
        hypotheses_week=hypotheses_week,
        previous_weekly_recap=previous_weekly_recap,
        runs_expected=runs_expected,
        runs_used=runs_used,
    )

    result = save_weekly_recap(
        asset_key, cfg["display_name"], week_start, week_end,
        runs_expected, runs_used, output,
    )
    log(f"Guardado OK: {result}")
    return result


if __name__ == "__main__":
    import time
    import traceback

    from config import ASSET_CONFIGS

    # Mismo criterio que persistence.py: pausa entre activos para no
    # ráfagear el free tier. El patrón oportunista hace que muchos
    # activos terminen en skip casi instantáneo (sin llamar a Gemini),
    # así que la corrida real tarda bastante menos que N x este delay.
    DELAY_BETWEEN_ASSETS_SECONDS = 15

    results: dict[str, dict] = {}
    asset_keys = list(ASSET_CONFIGS.keys())

    for i, asset_key in enumerate(asset_keys):
        try:
            results[asset_key] = generate_and_save_weekly(asset_key)
        except Exception as exc:
            print(f"[weekly:{asset_key}] FALLÓ: {exc}")
            traceback.print_exc()
            results[asset_key] = {"status": "error", "error": str(exc)}

        is_last = i == len(asset_keys) - 1
        if not is_last:
            time.sleep(DELAY_BETWEEN_ASSETS_SECONDS)

    print("\n=== Resumen Weekly Recap ===")
    for asset_key, result in results.items():
        status = result.get("status", "?")
        if status == "generated":
            print(f"  {asset_key}: OK — {result['runs_used']}/{result['runs_expected']} — confidence={result['weekly_confidence']}")
        elif status == "skipped_no_data":
            print(f"  {asset_key}: sin datos esta semana (skip)")
        else:
            print(f"  {asset_key}: ERROR — {result.get('error')}")
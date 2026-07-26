"""
persistence.py — Paso 4 del roadmap: escritura/lectura en Intelligence Layer
Intelligence Layer / Arkad Tools

Funciones públicas:
  get_previous_hypothesis(asset_key)   -> hipotesis+paranoia de la última
                                           corrida, para pasarle a
                                           engine.run_engine() (Sección 6)
  get_previous_state(asset_key)        -> fase+modificadores+conviccion de
                                           la última corrida, para comparar
                                           contra la corrida de hoy
  generate_and_save(asset_key)         -> orquesta el patrón de 2 llamadas
                                           y persiste el resultado (esto es
                                           lo que llama el cron, un asset_key
                                           a la vez)
  save_run(asset_key, payload, output, elasticity_flags=None)
                                        -> escribe en las 3 tablas

Patrón de 2 llamadas (decisión: costo despreciable a 25 activos/día — ver
conversación): la corrida preliminar (sin elasticity_flags) le da al motor
libertad para decidir fase/modificador/conviccion de hoy sin ningún sesgo
externo. Con ESE resultado ya podemos calcular los 5 flags reales
comparando contra la última corrida guardada (elasticity.py, Paso 3) — y
recién ahí hacer la corrida final, la que de verdad se persiste, con los
flags ya resueltos guiando cuánto expandirse (Sección 7 del doc). La
corrida preliminar se descarta, no se guarda en ninguna tabla.

No mezcla memoria con datos — todo esto vive en el Supabase de
Intelligence Layer, nunca en el de Data Layer.

Nota (upsert por asset_key+as_of en asset_history y hypotheses_history):
si el motor corre 2 veces el mismo día para el mismo activo (reintento
manual, corrida disparada a mano después de una noticia fuerte, etc.),
la segunda corrida PISA la fila de ese día en vez de insertar una
variante extra — un solo registro por (asset_key, as_of) en las 3
tablas. Esto requiere que asset_history y hypotheses_history tengan una
constraint UNIQUE (asset_key, as_of) en Supabase — ver
dedup_and_add_constraint.sql.
"""

from datetime import date, datetime, timezone

from output_schema import AssetTranslatorOutput
from supabase_clients import get_intel_layer_client


def get_previous_hypothesis(asset_key: str) -> dict | None:
    """
    Lee la última corrida guardada para este activo desde asset_outputs
    (upsert de "último análisis"). Devuelve el dict que engine.run_engine()
    espera como `previous_hypothesis`, o None si es la primera corrida.
    """
    client = get_intel_layer_client()
    resp = (
        client.table("asset_outputs")
        .select("hipotesis, paranoia, as_of")
        .eq("asset_key", asset_key)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None

    row = resp.data[0]
    return {
        "as_of": row["as_of"],
        "hipotesis": row["hipotesis"],
        "paranoia": row["paranoia"],
    }


def get_previous_state(asset_key: str) -> dict | None:
    """
    Lee fase/modificadores/conviccion de la última corrida guardada
    (asset_outputs). Es el insumo de elasticity.compute_elasticity_flags()
    para comparar la corrida de hoy contra la última — separado de
    get_previous_hypothesis() porque esa otra función solo trae
    hipotesis/paranoia (lo que necesita engine.py), no el estado
    compuesto (lo que necesita elasticity.py).

    Devuelve {"fase": str, "modificadores": list[str], "conviccion": str,
    "as_of": str} o None si es la primera corrida para este activo.
    """
    client = get_intel_layer_client()
    resp = (
        client.table("asset_outputs")
        .select("fase, modificadores, conviccion, as_of")
        .eq("asset_key", asset_key)
        .limit(1)
        .execute()
    )
    if not resp.data:
        return None

    row = resp.data[0]
    return {
        "fase": row["fase"],
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
    Persiste una corrida completa en las 3 tablas de Intelligence Layer:
      - asset_outputs:      UPSERT (pisa el último análisis)
      - asset_history:      UPSERT por (asset_key, as_of) — un registro
                             por día, se pisa si el motor corre 2 veces
      - hypotheses_history: UPSERT por (asset_key, as_of) — ídem

    payload: el dict devuelto por data_contract.build_payload() — se usa
             solo para sacar display_name, no se persiste completo acá
             (el payload crudo no es responsabilidad de Intelligence Layer).

    elasticity_flags: dict de elasticity.compute_elasticity_flags() (Paso 3),
             calculado DESPUÉS de tener `output` (ver nota de arquitectura
             en elasticity.py — no son pre-calculables para esta misma
             corrida). Si se pasa, se guarda dentro de full_output en
             asset_history bajo la clave "elasticity_flags" — no hace
             falta migración de schema para esto. Si más adelante querés
             una columna dedicada (para queries más simples desde el
             cron), avisame y armo la migración.

    Devuelve un resumen de qué se escribió, para logging del caller.
    """
    client = get_intel_layer_client()
    today = date.today()
    now = datetime.now(timezone.utc).isoformat()

    output_dict = output.model_dump(mode="json")

    # --- asset_outputs (upsert) ---
    outputs_row = {
        "asset_key": asset_key,
        "display_name": payload.get("display_name", asset_key),
        "as_of": today.isoformat(),
        "generated_at": now,
        "fase": output.estado.fase,
        "modificadores": output.estado.modificadores,
        "conviccion": output.estado.conviccion,
        "evidence_keys": output.estado.evidence_keys,
        "frase_puente": output.frase_puente,
        "traduccion_macro": output.traduccion_macro,
        "en_criollo": output.en_criollo,
        "hipotesis": output_dict["hipotesis"],
        "paranoia": output_dict["paranoia"],
        "memoria": output_dict["memoria"],
        "updated_at": now,
    }
    client.table("asset_outputs").upsert(outputs_row, on_conflict="asset_key").execute()

    # --- asset_history (upsert por asset_key+as_of — evita duplicados si
    #     el motor corre 2 veces el mismo día) ---
    history_full_output = output_dict
    if elasticity_flags is not None:
        history_full_output = {**output_dict, "elasticity_flags": elasticity_flags}

    history_row = {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "generated_at": now,
        "fase": output.estado.fase,
        "modificadores": output.estado.modificadores,
        "conviccion": output.estado.conviccion,
        "full_output": history_full_output,
    }
    client.table("asset_history").upsert(history_row, on_conflict="asset_key,as_of").execute()

    # --- hypotheses_history (upsert por asset_key+as_of — evita duplicados
    #     si el motor corre 2 veces el mismo día) ---
    hyp_row = {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "sostiene": output.hipotesis.sostiene,
        "debilita": output.hipotesis.debilita,
        "invalidaria": output.hipotesis.invalidaria,
        "podria_acelerarla": output.hipotesis.podria_acelerarla,
        "variable_a_vigilar": output.paranoia.variable_a_vigilar,
        "contexto_limpio": output.paranoia.contexto_limpio,
        "estado_hipotesis_previa": output.memoria.estado_hipotesis_previa,
    }
    client.table("hypotheses_history").upsert(hyp_row, on_conflict="asset_key,as_of").execute()

    return {
        "asset_key": asset_key,
        "as_of": today.isoformat(),
        "tables_written": ["asset_outputs", "asset_history", "hypotheses_history"],
        "elasticity_flags": elasticity_flags,
    }


def generate_and_save(asset_key: str, verbose: bool = True) -> dict:
    """
    Orquesta una corrida completa para un activo: patrón de 2 llamadas
    al motor + cálculo de flags + persistencia. Esto es lo que el cron
    llama una vez por activo (25 veces, una por asset_key).

    1. Arma el payload y busca estado/hipótesis previos.
    2. Corrida PRELIMINAR (sin elasticity_flags) — el motor decide fase/
       modificador/conviccion de hoy con libertad total.
    3. Calcula los 5 flags reales (elasticity.py) comparando esa
       preliminar contra la última guardada en asset_outputs.
    4. Corrida FINAL (con elasticity_flags ya resueltos) — esta es la
       que se persiste. La preliminar se descarta.
    5. save_run() en las 3 tablas.

    Devuelve el resumen de save_run().
    """
    # Imports acá adentro (no a nivel de módulo) para evitar import
    # circular: engine.py y data_contract.py ya importan cosas de este
    # mismo paquete en sus propios __main__.
    from data_contract import build_payload
    from engine import run_engine
    from elasticity import compute_elasticity_flags

    def log(msg: str) -> None:
        if verbose:
            print(f"[{asset_key}] {msg}")

    log("Buscando estado e hipótesis previos...")
    previous_state = get_previous_state(asset_key)
    previous_hypothesis = get_previous_hypothesis(asset_key)

    log("Armando payload desde Data Layer...")
    payload = build_payload(asset_key)

    log("Corrida preliminar (sin flags)...")
    preliminary_output = run_engine(payload, previous_hypothesis=previous_hypothesis)

    log("Calculando flags de elasticidad (Paso 3, sin LLM)...")
    preliminary_estado = preliminary_output.estado.model_dump(mode="json")
    elasticity_flags = compute_elasticity_flags(preliminary_estado, previous_state, payload)
    log(f"Flags: {elasticity_flags}")

    log("Corrida final (con flags)...")
    # Fix: en la primera corrida (previous_state is None) NO se pasan los
    # flags calculados como si fueran una lectura real de "nada cambió" —
    # son 5 False por definición (no hay nada contra qué comparar), y
    # pasarlos como dict (truthy) le indicaría al motor que sea breve
    # justo en el análisis que debería ser el más completo. Se siguen
    # persistiendo en asset_history vía save_run() más abajo, solo se
    # excluyen de la instrucción al modelo.
    final_output = run_engine(
        payload,
        previous_hypothesis=previous_hypothesis,
        elasticity_flags=elasticity_flags if previous_state is not None else None,
    )

    log("Guardando en Intelligence Layer...")
    result = save_run(asset_key, payload, final_output, elasticity_flags=elasticity_flags)
    log(f"Guardado OK: {result}")
    return result


if __name__ == "__main__":
    import time
    import traceback

    ASSETS = [
        "GOLD",
        "SILVER",
        "CRUDE_OIL",
        "NASDAQ",
        "SP500",
        "DXY",
        "COPPER",
        "RUSSELL2000",
        "EURUSD",
        "GBPUSD",
        "USDJPY",
        "USDCAD",
        "USDCHF",
        "AUDUSD",
        "BTC",
    ]

    # Pausa entre activos (no entre reintentos dentro de una misma llamada
    # — eso ya lo maneja engine.py). Con 15 activos, sin esto se manda una
    # ráfaga de 2 llamadas x N activos casi en simultáneo, lo que aumenta
    # la chance de pisar el límite de RPM del free tier y generar más 503
    # de los que el retry por-llamada puede compensar. 15s es conservador
    # para no alargar demasiado la corrida (+3.5min extra en total);
    # ajustar si se sigue viendo 503 frecuente.
    DELAY_BETWEEN_ASSETS_SECONDS = 15

    # --- Segunda capa de reintento: a nivel de ACTIVO, no de llamada ---
    # engine.py ya reintenta cada llamada individual hasta 5 veces con
    # backoff exponencial (8/16/32/64s ≈ 2min). Eso cubre baches cortos
    # de Gemini, pero un evento de "high demand" prolongado (Google mismo
    # reporta que pueden durar de minutos a varias horas) agota esos 2min
    # sin resolverse. En vez de resignarse a "hoy este activo se queda sin
    # hipótesis", se guarda qué activos fallaron y se los reintenta en
    # pasadas separadas, con una espera más larga entre pasadas — le da al
    # problema de Gemini más tiempo real para disolverse solo, sin
    # bloquear la corrida entera esperando horas.
    RETRY_ROUND_DELAY_SECONDS = 300  # 5 min entre pasadas de reintento
    MAX_RETRY_ROUNDS = 2             # + la pasada inicial = hasta 3 intentos por activo

    def _run_round(assets_to_run: list[str]) -> dict:
        """Corre generate_and_save() para una lista de activos, con su
        propio try/except por activo (una falla no frena a los demás)."""
        round_results: dict[str, dict] = {}
        for i, asset in enumerate(assets_to_run):
            try:
                round_results[asset] = generate_and_save(asset)
            except Exception as exc:
                print(f"[{asset}] FALLÓ: {exc}")
                traceback.print_exc()
                round_results[asset] = {"error": str(exc)}

            is_last_asset = i == len(assets_to_run) - 1
            if not is_last_asset:
                time.sleep(DELAY_BETWEEN_ASSETS_SECONDS)
        return round_results

    # Pasada inicial: los 15 activos.
    results: dict[str, dict] = _run_round(ASSETS)

    # Pasadas de reintento: solo los que quedaron con error, hasta agotar
    # MAX_RETRY_ROUNDS. Si ya no queda ninguno con error, no se espera de más.
    for retry_round in range(1, MAX_RETRY_ROUNDS + 1):
        failed_assets = [asset for asset, result in results.items() if "error" in result]
        if not failed_assets:
            break

        print(
            f"\n=== Pasada de reintento {retry_round}/{MAX_RETRY_ROUNDS} — "
            f"{len(failed_assets)} activo(s) pendientes: {failed_assets} ==="
        )
        print(f"Esperando {RETRY_ROUND_DELAY_SECONDS}s antes de reintentar...")
        time.sleep(RETRY_ROUND_DELAY_SECONDS)

        retry_results = _run_round(failed_assets)
        results.update(retry_results)

    print("\n=== Resumen de la corrida ===")
    for asset, result in results.items():
        if "error" in result:
            print(f"  {asset}: ERROR (persistió tras {MAX_RETRY_ROUNDS + 1} pasadas) — {result['error']}")
        else:
            print(f"  {asset}: OK — flags={result.get('elasticity_flags')}")
"""
elasticity.py — Paso 3 del roadmap: flags determinísticos de elasticidad
Intelligence Layer / Arkad Tools

Sin LLM. Una función pública: compute_elasticity_flags().

ADAPTADO a schema_version=2 (Sentinel Market State Model, Documento 3):
antes comparaba 'fase' (V1), ahora compara 'estado' (V2) — el resto de
la lógica de esta sección no cambia. Los 5 nombres de flag se mantienen
tal cual (cambio_fase, cambio_modificador, conflicto_detectado,
conviccion_bajo, sorpresa_calendario) para no romper ningún consumidor
downstream (Dashboard, etc.) que ya lea estas claves — 'cambio_fase'
ahora significa "cambió el estado", no literalmente "cambió la fase".
Si en algún momento se quiere renombrar la clave en sí, es una decisión
aparte que toca el Dashboard también, no algo para colar acá.

NOTA DE ARQUITECTURA (leer antes de tocar el enganche en persistence.py)
-------------------------------------------------------------------------
De los 5 flags de la Sección 7 del doc fuente, 4 (cambio_fase,
cambio_modificador, conflicto_detectado, conviccion_bajo) dependen del
estado que el motor DECIDE en la corrida de hoy — no existen todavía en
el momento en que armaríamos el prompt para esa misma corrida. No son
pre-calculables sin haber ejecutado ya engine.run_engine().

El único flag genuinamente pre-calculable ANTES de llamar a Gemini es
sorpresa_calendario, porque sale de un dato duro ya calculado en
Data Layer (calendar_metrics.zscore_6), sin pasar por juicio del LLM.

Por eso esta función se usa DESPUÉS de run_engine(), no antes:
comparás el output ya generado (today_estado) contra lo último guardado
en asset_outputs (previous_state, vía persistence.get_previous_state()).
Sirve para:
  - persistir el historial de "qué tan movido viene el activo"
    (asset_history.full_output.elasticity_flags)
  - la decisión de cron/alertas (Paso 2 de tu plan)
  - previous_state de la corrida de MAÑANA sigue siendo el asset_outputs
    de hoy, así que el ciclo se cierra solo día a día.
"""

CONVICCION_ORDER = {"BAJA": 0, "MEDIA": 1, "ALTA": 2}
CALENDAR_SURPRISE_ZSCORE_THRESHOLD = 2.0

EMPTY_FLAGS = {
    "cambio_fase": False,
    "cambio_modificador": False,
    "conflicto_detectado": False,
    "conviccion_bajo": False,
    "sorpresa_calendario": False,
}


def _sorpresa_calendario(payload: dict, threshold: float = CALENDAR_SURPRISE_ZSCORE_THRESHOLD) -> bool:
    """
    True si algún evento reciente de calendario (payload['calendar_recent'])
    tuvo una sorpresa estadísticamente extrema: |zscore_6| >= threshold.

    zscore_6 viene de calendar_metrics (Data Layer) — dato duro ya
    calculado, no depende de la corrida del motor. Casteo explícito a
    float() porque Supabase puede devolver el numeric como str según el
    cliente/driver.
    """
    for event in payload.get("calendar_recent", []) or []:
        z = event.get("zscore_6")
        if z is None:
            continue
        try:
            if abs(float(z)) >= threshold:
                return True
        except (TypeError, ValueError):
            continue
    return False


def compute_elasticity_flags(
    today_estado: dict,
    previous_state: dict | None,
    payload: dict,
) -> dict:
    """
    Compara la corrida de hoy contra la última guardada y devuelve los
    5 flags determinísticos. Se llama DESPUÉS de run_engine() — ver nota
    de arquitectura arriba.

    today_estado:    dict con shape {"estado": str, "modificadores": list[str],
                      "conviccion": str} — se arma en persistence.py
                      combinando output.estado.estado / output.estado.conviccion
                      (Llamada 1) con output.contexto.modificadores (Llamada 2),
                      ya que en V2 los modificadores viven en ContextoOutput,
                      no en EstadoActivo.
    previous_state:  mismo shape + "as_of"/"estado_provisional_hacia", tal
                      cual lo devuelve persistence.get_previous_state(), o
                      None si es la primera corrida schema_version=2 para
                      este activo (bootstrap, Documento 3 §7.1).
    payload:         dict de data_contract.build_payload() — se usa solo
                      para sorpresa_calendario (independiente de la memoria).

    Devuelve el dict de 5 flags, listo para persistir.
    """
    flags = dict(EMPTY_FLAGS)
    flags["sorpresa_calendario"] = _sorpresa_calendario(payload)

    if previous_state is None:
        # Primera corrida: nada contra qué comparar. sorpresa_calendario
        # es la única excepción porque no depende de la memoria.
        return flags

    prev_estado = previous_state.get("estado")
    prev_modificadores = set(previous_state.get("modificadores") or [])
    prev_conviccion = previous_state.get("conviccion")

    hoy_estado = today_estado.get("estado")
    hoy_modificadores = set(today_estado.get("modificadores") or [])
    hoy_conviccion = today_estado.get("conviccion")

    flags["cambio_fase"] = bool(prev_estado) and hoy_estado != prev_estado
    flags["cambio_modificador"] = hoy_modificadores != prev_modificadores

    # conflicto_detectado: no es un cálculo aparte — es leer si la
    # Llamada 2 marcó el modificador contexto_conflictivo en la corrida
    # de hoy (Documento 2 §12: ese modificador ES la definición de
    # conflicto_detectado, no hay una regla numérica paralela).
    flags["conflicto_detectado"] = "contexto_conflictivo" in hoy_modificadores

    if prev_conviccion in CONVICCION_ORDER and hoy_conviccion in CONVICCION_ORDER:
        flags["conviccion_bajo"] = (
            CONVICCION_ORDER[hoy_conviccion] < CONVICCION_ORDER[prev_conviccion]
        )

    return flags


if __name__ == "__main__":
    # Smoke test rápido sin tocar Supabase ni Gemini.
    payload_stub = {
        "calendar_recent": [
            {"event_name": "CPI", "zscore_6": 2.4},
            {"event_name": "NFP", "zscore_6": 0.3},
        ]
    }
    previous = {
        "estado": "acumulacion",
        "estado_provisional_hacia": None,
        "modificadores": ["esperando_catalizadores"],
        "conviccion": "ALTA",
        "as_of": "2026-07-30",
    }
    today = {
        "estado": "equilibrio",
        "modificadores": ["contexto_conflictivo"],
        "conviccion": "MEDIA",
    }

    flags = compute_elasticity_flags(today, previous, payload_stub)
    print(flags)
    assert flags == {
        "cambio_fase": True,
        "cambio_modificador": True,
        "conflicto_detectado": True,
        "conviccion_bajo": True,
        "sorpresa_calendario": True,
    }
    print("OK — elasticity.py adaptado a schema_version=2 (compara 'estado', no 'fase')")
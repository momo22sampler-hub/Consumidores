"""
precheck.py — Chequeos determinísticos previos a la Etapa 0
Intelligence Layer / Arkad Tools

Sin LLM. Extiende el patrón ya usado en elasticity._sorpresa_calendario():
lectura directa de un dato duro del payload, sin juicio del modelo.
Documento 3 §6.1.

Dos funciones públicas:
  check_invalidacion_confirmada() -> bool | None
  check_ruptura_precio()          -> bool

CORRECCIÓN Aug 2026: la navegación de dotted_key sobre el payload (antes
_get_by_dotted_key(), local a este archivo) se unificó en
evidence_map.resolve_payload_value() — era la misma lógica reimplementada
en dos lugares, y evidence_map.py es el único módulo donde el Documento 3
§3 dice que debe vivir la correspondencia payload <-> evidencia.
"""

import operator as op

from evidence_map import resolve_payload_value

_OPERATORS = {
    "<": op.lt,
    "<=": op.le,
    ">": op.gt,
    ">=": op.ge,
    "==": op.eq,
}


def check_invalidacion_confirmada(
    payload_hoy: dict,
    hipotesis_previa: dict | None,
) -> bool | None:
    """
    Documento 3 §6.1. Evalúa si la condición de invalidación de la
    hipótesis guardada ayer se cumplió, contra el payload de hoy.

    Devuelve:
      True  -> la condición estructurada se cumplió, hay invalidación
               confirmada de forma determinística.
      False -> la condición estructurada existe y NO se cumplió.
      None  -> no hay hipótesis previa (primera corrida), o la condición
               de 'invalidaria' no tiene 'invalidaria_check' estructurado
               (es cualitativa) — compute_candidate_states() trata None
               igual que False, pero el caller debe loguearlo distinto
               para saber que acá hace falta revisión humana eventual
               (Documento 3 §6.3 sigue siendo la red de seguridad).
    """
    if hipotesis_previa is None:
        return None

    check = hipotesis_previa.get("invalidaria_check")
    if not check:
        return None

    valor_actual = resolve_payload_value(payload_hoy, check["evidence_key"])
    if valor_actual is None:
        # La clave que la hipótesis de ayer necesitaba ya no está en el
        # payload de hoy (activo cambió de config, dato faltante, etc.)
        # — no se puede confirmar determinísticamente. No se asume ni
        # True ni False.
        return None

    comparador = _OPERATORS[check["operador"]]
    try:
        return bool(comparador(float(valor_actual), float(check["umbral"])))
    except (TypeError, ValueError):
        return None


# Umbral de ruptura: cierre fuera de los límites del rango vigente,
# expresado como z-score de 1 día por simplicidad inicial (Documento 3
# no fija el número exacto — queda como parámetro calibrable por activo,
# ver Documento 3 §5.1, "el criterio operativo... queda a criterio de
# calibración por activo").
RUPTURA_ZSCORE_1D_THRESHOLD = 2.0


def check_ruptura_precio(
    payload_hoy: dict,
    threshold: float = RUPTURA_ZSCORE_1D_THRESHOLD,
) -> bool:
    """
    Documento 3 §5 (Etapa 0) / Documento 2 §5.1. Chequeo puramente
    numérico sobre 9.1 — no requiere LLM. Señal de que el precio de hoy
    salió del comportamiento reciente lo suficiente como para abrir la
    pregunta de un Estado Provisional; NO decide por sí solo el destino
    ni confirma nada — solo habilita que la Llamada 1 tenga más de un
    candidato disponible.
    """
    zscore_1d = resolve_payload_value(payload_hoy, "pricing.current.zscore_1d")
    if zscore_1d is None:
        return False
    try:
        return abs(float(zscore_1d)) >= threshold
    except (TypeError, ValueError):
        return False
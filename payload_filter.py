"""
payload_filter.py — Filtrado de payload por tier de evidencia
Intelligence Layer / Arkad Tools

Documento 3 §5: la Llamada 1 solo puede VER 9.1+9.2+9.3 — no alcanza
con instruirle que "no use" el resto, tiene que no estar en su payload.
Este módulo hace ese recorte de forma determinística, usando
evidence_map.py como única fuente de verdad sobre qué prefijo es qué
tier.
"""

from evidence_map import EVIDENCE_TIER_BY_PREFIX

# Prefijos de tope de primer nivel del payload armado por
# data_contract.build_payload() (ver ese módulo). "pricing" cubre 9.1 y
# 9.2 (Sección 3 del Documento 3 — se resuelven ambos al mismo prefijo
# de payload, la distinción es a nivel de evidence_key individual, no
# de rama del payload).
_STRUCTURAL_TOP_LEVEL_KEYS = {"pricing", "cot", "etf_flows"}

_CONTEXTUAL_TOP_LEVEL_KEYS = {
    "fred",
    "correlations",
    "calendar_recent",
    "calendar_upcoming",
    "geopolitics",
    "sentiment",
}

# Claves de metadata que no son evidencia (display_name, etc.) y viajan
# en ambos payloads sin filtrar.
_PASSTHROUGH_KEYS = {"display_name", "asset_key"}


def build_structural_payload(full_payload: dict) -> dict:
    """
    Documento 3 §5, Llamada 1. Devuelve un dict que contiene SOLO las
    ramas 9.1/9.2/9.3 del payload completo, más passthrough de
    metadata. Si data_contract.py agrega una fuente nueva y nadie
    actualiza _STRUCTURAL_TOP_LEVEL_KEYS/_CONTEXTUAL_TOP_LEVEL_KEYS,
    esa clave queda afuera de ambos payloads por diseño (fail-closed:
    mejor una fuente nueva ausente que una contextual colándose en la
    Llamada 1 sin que nadie la haya clasificado).
    """
    out = {k: v for k, v in full_payload.items() if k in _PASSTHROUGH_KEYS}
    for key in _STRUCTURAL_TOP_LEVEL_KEYS:
        if key in full_payload:
            out[key] = full_payload[key]
    return out


def build_contextual_call_payload(
    full_payload: dict,
    estado_fijado: dict,
    previous_hypothesis: dict | None = None,
) -> dict:
    """
    Documento 3 §5, Llamada 2. Payload completo (9.1-9.4) más el estado
    ya decidido por la Llamada 1, como dato de solo lectura explícito
    — nunca como algo que este payload sugiera que puede cambiarse.

    previous_hypothesis: dict devuelto por
    persistence.get_previous_hypothesis() — {"as_of", "hipotesis",
    "paranoia", "invalidaria_check"} — o None si es la primera corrida
    schema_version=2 para este activo (bootstrap, Documento 3 §7.1).

    CORRECCIÓN (encontrada en smoke test real contra Gemini, sin mock):
    antes este payload no incluía la hipótesis previa en absoluto, así
    que la Llamada 2 no tenía con qué comparar para llenar 'Memoria' y
    terminaba alucinando una respuesta (ej. 'vigente_sin_cambios' en una
    corrida bootstrap sin ninguna hipótesis previa real). Se agrega acá
    'memoria_previa' explícito — None en bootstrap, dict con la
    hipótesis/paranoia de ayer en corridas normales — para que la
    comparación de Memoria (Documento 2 §14) sea real, no inferida.
    """
    return {
        **full_payload,
        "estado_fijado_por_llamada_1": estado_fijado,
        "memoria_previa": previous_hypothesis,
    }


def assert_no_contextual_leak(structural_payload: dict) -> None:
    """
    Guardia de desarrollo: falla fuerte si por error una clave
    contextual quedó adentro del payload estructural. Se llama en tests
    y, opcionalmente, antes de cada corrida real en modo debug.
    """
    leaked = _CONTEXTUAL_TOP_LEVEL_KEYS & structural_payload.keys()
    if leaked:
        raise ValueError(
            f"Fuga de evidencia contextual (9.4) hacia la Llamada 1: {leaked}. "
            "Documento 3 §5 prohíbe que la Llamada 1 vea 9.4."
        )
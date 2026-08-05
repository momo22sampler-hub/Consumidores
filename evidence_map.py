"""
evidence_map.py — Mapa de evidencia: Data Layer -> Jerarquía del Documento 2
Intelligence Layer / Arkad Tools

Documento 3, Sección 3. Este módulo es el único lugar del sistema donde
vive la correspondencia entre los prefijos de payload que arma
data_contract.py y los tiers de evidencia definidos en el Documento 2
(§9-10). Nada más en el código debería tener esta tabla hardcodeada en
otro lado — cualquier validación determinística (evidence_keys de la
Llamada 1, el pre-check de invalidación, etc.) importa de acá.

Regla de uso: EVIDENCE_TIER_VERSION se incrementa cada vez que este mapa
cambia. Se persiste junto con cada corrida (asset_history.evidence_map_version)
para poder auditar, meses después, bajo qué regla de jerarquía se tomó
una decisión — Documento 2 §17, "toda transición debe ser explicable y
auditable" aplica también a la jerarquía de evidencia usada, no solo a
la evidencia en sí.
"""

from typing import Any, Literal

EVIDENCE_TIER_VERSION = 2  # bump: assert_evidence_keys_exist ahora rechaza claves con valor None

EvidenceTier = Literal["9.1", "9.2", "9.3", "9.4"]

# Prefijos de payload -> tier. Se matchea con str.startswith() sobre la
# clave completa del payload (ej. "pricing.current.zscore_1d" matchea
# el prefijo "pricing.").
EVIDENCE_TIER_BY_PREFIX: dict[str, EvidenceTier] = {
    # 9.1 — Precio y Tiempo (obligatoria, Documento 2 §9.1)
    "pricing.": "9.1",

    # 9.2 — Derivada de precio (estructural, apoyo, Documento 2 §9.2)
    # Nota: hoy vive en el mismo namespace "pricing.*" que 9.1 (medias,
    # zscore, etc. salen de pricing_metrics). Se resuelve por sufijo de
    # campo, no por prefijo — ver resolve_tier().
    # "pricing.*.sma_*", "pricing.*.zscore_*"  -> resuelto en resolve_tier()

    # 9.3 — Posicionamiento institucional (estructural, apoyo elevado,
    # Documento 2 §9.3). Un activo usa cot.* O etf_flows.*, nunca ambos
    # — ver get_positioning_source().
    "cot.": "9.3",
    "etf_flows.": "9.3",

    # 9.4 — Contextual (Documento 2 §9.4)
    "fred.": "9.4",
    "correlations.": "9.4",
    "calendar_recent.": "9.4",
    "calendar_upcoming.": "9.4",
    "geopolitics.": "9.4",
    "sentiment.": "9.4",
}

# Sufijos de campo dentro de "pricing.*" que corresponden a 9.2 en vez
# de 9.1 — todo lo demás bajo "pricing." es 9.1.
_PRICING_9_2_FIELD_SUFFIXES = ("sma_", "zscore_", "dist_sma_")


def resolve_tier(evidence_key: str) -> EvidenceTier | None:
    """
    Devuelve el tier (Documento 2 §9) de una evidence_key del payload,
    o None si la clave no matchea ningún prefijo conocido (debería
    tratarse como error de datos, no como evidencia sin clasificar).
    """
    if evidence_key.startswith("pricing."):
        field = evidence_key.rsplit(".", 1)[-1]
        if any(field.startswith(suf) for suf in _PRICING_9_2_FIELD_SUFFIXES):
            return "9.2"
        return "9.1"

    for prefix, tier in EVIDENCE_TIER_BY_PREFIX.items():
        if prefix == "pricing.":
            continue  # ya resuelto arriba
        if evidence_key.startswith(prefix):
            return tier

    return None


def get_positioning_source(asset_config: dict) -> Literal["cot", "etf_flows"]:
    """
    Documento 3 §3: cada activo declara en su config cuál es su fuente
    de 9.3 — nunca ambas, nunca ambigüedad. Sigue la misma regla que ya
    usa data_contract._get_etf_flows(): solo los activos con
    'etf_tickers' en su ASSET_CONFIG usan etf_flows; el resto usa cot.
    """
    return "etf_flows" if asset_config.get("etf_tickers") else "cot"


def assert_only_structural(evidence_keys: list[str]) -> None:
    """
    Guardia para la Llamada 1 (Documento 3 §5): evidence_keys de
    EstadoActivo solo puede citar tiers 9.1/9.2/9.3. Si aparece una
    clave 9.4, es un error de disciplina de citado — la Llamada 1 no
    debería tener evidencia contextual en su payload filtrado, así que
    esto no debería poder pasar salvo bug en el filtrado del payload.
    """
    for key in evidence_keys:
        tier = resolve_tier(key)
        if tier == "9.4":
            raise ValueError(
                f"evidence_key '{key}' es evidencia contextual (9.4) citada "
                "por la Llamada 1 (Estado y Convicción) — Documento 3 §5 "
                "prohíbe que la Llamada 1 vea o cite evidencia contextual."
            )
        if tier is None:
            raise ValueError(
                f"evidence_key '{key}' no matchea ningún prefijo conocido en "
                "evidence_map.EVIDENCE_TIER_BY_PREFIX — revisar mapa o payload."
            )


# ---------------------------------------------------------------------------
# Corrección Aug 2026 (smoke test GOLD, primera corrida real): lo de arriba
# valida el TIER de una evidence_key, nunca si esa clave existe de verdad en
# el payload de esta corrida. Eso dejó pasar sin bloqueo un caso real: la
# Llamada 2 citó "encuestas de sentimiento del consumidor" en
# que_metrica_no_termina_de_cerrar — un tier plausible (9.4/sentiment) pero
# una fuente que no existe en absoluto en el Data Layer. Documento 2 §9 ya
# dice "Sentinel no asume, no completa con conocimiento externo y no infiere
# datos que no existen" — esto es aplicar esa regla con rigor de
# implementación real, no una regla nueva.
#
# Estas funciones son también el único lugar del sistema donde se navega un
# dotted_key sobre el payload — precheck.py importa de acá en vez de
# reimplementar la misma lógica por su cuenta.
# ---------------------------------------------------------------------------

_MISSING = object()


def _navigate(payload: dict, dotted_key: str) -> Any:
    """Navega dotted_key sobre payload. Soporta tanto claves de dict como
    índices de lista (ej. 'geopolitics.0.event_count') — corrección Aug
    2026 (casos AUDUSD/CRUDE_OIL): antes solo soportaba dict, así que
    cualquier evidence_key hacia una lista (calendar_recent,
    calendar_upcoming, geopolitics) fallaba siempre, sin importar si el
    campo citado era real o no. Devuelve el sentinel _MISSING si algún
    tramo no existe — nunca None, para no confundir 'la clave no existe'
    con 'la clave existe y vale None'."""
    node: Any = payload
    for part in dotted_key.split("."):
        if isinstance(node, dict):
            if part not in node:
                return _MISSING
            node = node[part]
        elif isinstance(node, list):
            if not part.isdigit():
                return _MISSING
            idx = int(part)
            if idx >= len(node):
                return _MISSING
            node = node[idx]
        else:
            return _MISSING
    return node


def resolve_payload_value(payload: dict, dotted_key: str) -> Any:
    """
    Devuelve el valor real en dotted_key, o None si no existe (dato
    faltante para este activo — Documento 2 §9.5, no es bloqueante por
    sí solo). Reemplaza a precheck._get_by_dotted_key(), que duplicaba
    esta misma navegación en otro archivo.
    """
    value = _navigate(payload, dotted_key)
    return None if value is _MISSING else value


def key_exists_in_payload(payload: dict, dotted_key: str) -> bool:
    """
    True si dotted_key resuelve a un valor REAL (no None) en el payload.
    Un campo que existe pero vale None es estructuralmente vacío para este
    activo (ej. lev_money_percentile_5y para GOLD/disagg_fut donde la
    categoría no aplica) — no es evidencia citeable.

    Si necesitás saber solo si el tramo de la clave existe en la
    estructura (sin importar si el valor es None), usá
    key_path_exists_in_payload().
    """
    value = _navigate(payload, dotted_key)
    return value is not _MISSING and value is not None


def key_path_exists_in_payload(payload: dict, dotted_key: str) -> bool:
    """
    True si dotted_key resuelve a cualquier valor en el payload, incluido
    None. Usar solo cuando se necesita saber si la clave está en la
    estructura, no si tiene dato real.
    """
    return _navigate(payload, dotted_key) is not _MISSING


def assert_evidence_keys_exist(evidence_keys: list[str], payload: dict) -> None:
    """
    Post-check (Documento 3 §6.4, corrección Aug 2026 + corrección v2):
    cada evidence_key que el modelo cita tiene que (a) existir en el
    payload de esta corrida Y (b) tener un valor no-None — un campo None
    es estructuralmente vacío para este activo y no puede sostenerse como
    evidencia (ej. lev_money_percentile_5y para GOLD/disagg_fut).

    Se usa tanto para las evidence_keys de EstadoActivo (Llamada 1) como
    para las de Paranoia e invalidaria_check (Llamada 2).
    """
    faltantes = [key for key in evidence_keys if not key_exists_in_payload(payload, key)]
    if faltantes:
        # Clasificar: ¿falta la clave del todo, o existe pero vale None?
        ausentes   = [k for k in faltantes if _navigate(payload, k) is _MISSING]
        vacios     = [k for k in faltantes if _navigate(payload, k) is None]
        partes = []
        if ausentes:
            partes.append(f"no existen en el payload: {ausentes}")
        if vacios:
            partes.append(
                f"existen pero tienen valor None para este activo "
                f"(campo vacío / no aplica a este dataset): {vacios}"
            )
        raise ValueError(
            f"evidence_key(s) inválidas — "
            + " | ".join(partes)
            + ". Documento 2 §9: Sentinel no cita fuentes sin dato real."
        )
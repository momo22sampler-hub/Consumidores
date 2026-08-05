"""
engine_estado.py — Etapa 0: matriz de transiciones y construcción de
candidatos (Documento 3 §5 y §6.1-6.2)
Intelligence Layer / Arkad Tools

Sin LLM. Dos responsabilidades:
  1. TRANSITION_MATRIX: la matriz de transiciones permitidas del
     Documento 2 §7, como estructura de datos — no como texto en un
     prompt.
  2. compute_candidate_states(): dado el estado previo y el resultado
     de los chequeos determinísticos (invalidación confirmada, ruptura
     de precio detectada), devuelve el conjunto de estados que la
     Llamada 1 puede elegir hoy. La Llamada 1 nunca ve el catálogo
     completo salvo que el propio estado previo ya sea Equilibrio y
     haya ruptura en juego.

Este módulo corre en "modo solo log" durante el rollout (Documento 3
§10, paso 3): se ejecuta contra producción actual y se registra qué
candidatos habría dado, sin todavía restringir la llamada real al LLM.
"""

from dataclasses import dataclass
from typing import Literal

from output_schema import EstadoMercado

# ---------------------------------------------------------------------------
# Documento 2 §7 — Matriz de transiciones permitidas
# ---------------------------------------------------------------------------
# Cada entrada: estado_origen -> lista de estados destino alcanzables,
# todos "vía Estado Provisional" salvo que se indique "directo".
# Ninguna combinación fuera de este mapa es válida.

TRANSITION_MATRIX: dict[EstadoMercado, dict[str, list[EstadoMercado]]] = {
    "equilibrio": {
        "directo": ["acumulacion", "distribucion"],
        "via_ep": ["price_discovery_alcista", "price_discovery_bajista"],
    },
    "acumulacion": {
        "directo": [],
        "via_ep": ["equilibrio", "price_discovery_alcista"],
        # NOTA (Documento 2 §7): acumulacion -> distribucion directa es inválida.
        # Si el COT gira sin resolución direccional previa, el destino es
        # "equilibrio" (evaluación desde cero), nunca "distribucion".
    },
    "distribucion": {
        "directo": [],
        "via_ep": ["equilibrio", "price_discovery_bajista"],
    },
    "price_discovery_alcista": {
        "directo": [],
        "via_ep": ["distribucion", "reacumulacion"],
        # reacumulacion = pausa de continuación dentro de PD Alcista (Doc2 §6.6).
        # redistribucion NO es alcanzable desde PD Alcista — implicaría
        # cambio de dirección estructural sin pasar por Equilibrio primero.
    },
    "price_discovery_bajista": {
        "directo": [],
        "via_ep": ["acumulacion", "redistribucion"],
        # redistribucion = pausa de continuación dentro de PD Bajista (Doc2 §6.7).
        # reacumulacion NO es alcanzable desde PD Bajista — mismo razonamiento.
    },
    "reacumulacion": {
        # Pausa dentro de PD Alcista. Solo dos salidas válidas:
        # - Retoma del PD Alcista (continuación, caso más frecuente)
        # - Falla de la continuación hacia Equilibrio (el PD Alcista no retoma)
        # NO puede ir a PD Bajista ni a Redistribución directamente (Doc2 §7).
        "directo": [],
        "via_ep": ["price_discovery_alcista", "equilibrio"],
    },
    "redistribucion": {
        # Pausa dentro de PD Bajista. Simétrico a reacumulacion.
        "directo": [],
        "via_ep": ["price_discovery_bajista", "equilibrio"],
    },
}


def is_valid_transition(origen: EstadoMercado, destino: EstadoMercado) -> bool:
    """Aserción de integridad (Documento 3 §6.2) — no debería dispararse
    nunca si compute_candidate_states() se usó correctamente."""
    if origen == destino:
        return True  # persistencia, Documento 2 §11
    row = TRANSITION_MATRIX[origen]
    return destino in row["directo"] or destino in row["via_ep"]


@dataclass(frozen=True)
class CandidateSet:
    """Resultado de la Etapa 0: lo que la Llamada 1 puede elegir hoy."""
    candidatos: list[EstadoMercado]
    # Si True, cualquier candidato != estado_previo debe salir marcado
    # como estado_provisional_hacia, NO como estado confirmado directo
    # (Documento 2 §5.1 — toda transición pasa por Estado Provisional,
    # salvo las marcadas "directo" en la matriz, que solo aplican desde
    # Equilibrio).
    requiere_estado_provisional: bool
    motivo: str  # para logging/auditoría — Documento 2 §17


def compute_candidate_states(
    estado_previo: EstadoMercado,
    estado_provisional_previo: EstadoMercado | None,
    invalidacion_confirmada: bool | None,
    ruptura_precio_detectada: bool,
) -> CandidateSet:
    """
    Documento 3 §5, Etapa 0. Sin LLM. Implementa las tres reglas de
    construcción en orden de precedencia.
    """

    # Regla 1: hay un Estado Provisional abierto -> solo dos salidas,
    # confirmar o invalidar. Nunca un tercer estado no contemplado en
    # la apertura original.
    if estado_provisional_previo is not None:
        return CandidateSet(
            candidatos=[estado_provisional_previo, estado_previo],
            requiere_estado_provisional=True,
            motivo=(
                f"Estado Provisional ya abierto hacia '{estado_provisional_previo}' "
                f"— hoy se confirma o se invalida, no se evalúan otras opciones."
            ),
        )

    # Regla 2: sin Estado Provisional abierto, invalidación NO confirmada
    # (False o None) -> el único candidato firme es mantener el estado.
    # Si además hay ruptura de precio, se agrega la posibilidad de abrir
    # un Estado Provisional hacia los destinos de la matriz.
    if not invalidacion_confirmada:
        candidatos = [estado_previo]
        motivo = f"Invalidación no confirmada — se mantiene '{estado_previo}'."
        if ruptura_precio_detectada:
            row = TRANSITION_MATRIX[estado_previo]
            destinos_ep = row["directo"] + row["via_ep"]
            candidatos = [estado_previo] + destinos_ep
            motivo += (
                f" Ruptura de precio detectada: se habilita evaluar Estado "
                f"Provisional hacia {destinos_ep}."
            )
        return CandidateSet(
            candidatos=candidatos,
            requiere_estado_provisional=ruptura_precio_detectada,
            motivo=motivo,
        )

    # Regla 3: invalidación confirmada -> se habilitan los estados
    # alcanzables desde estado_previo según la matriz, siempre vía EP
    # (Documento 2 §5.1 sigue aplicando incluso con invalidación
    # confirmada — la confirmación de invalidación NO es lo mismo que
    # confirmación de la nueva estructura de precio).
    row = TRANSITION_MATRIX[estado_previo]
    destinos = row["directo"] + row["via_ep"]
    return CandidateSet(
        candidatos=[estado_previo] + destinos,
        requiere_estado_provisional=True,
        motivo=(
            f"Invalidación confirmada desde '{estado_previo}' — candidatos "
            f"habilitados por matriz: {destinos}. Toda transición sigue "
            f"requiriendo confirmación vía Estado Provisional."
        ),
    )
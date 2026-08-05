"""
output_schema.py — Schema de salida del Asset Translator
Intelligence Layer / Arkad Tools

Mapea 1:1 la estructura del Sentinel Market State Model (Documento 2) y
su implementación (Documento 3):
  Doc.2 §6, §5.1  -> EstadoActivo (Estado x Estado Provisional, Convicción)
  Doc.2 §12       -> ModificadorContexto
  (sin cambios)   -> AssetTranslatorOutput (frase puente, traducción macro, criolla)
  (sin cambios)   -> Hipotesis (4 componentes)
  Doc.3, corrección Aug 2026 -> Paranoia (3 de 5 preguntas ahora exigen
                     {observacion, evidence_key} verificable, no texto libre)
  (sin cambios)   -> Memoria (comparación contra la hipótesis anterior)

Usado como response_schema en la llamada a Gemini — la API valida que
el modelo devuelva exactamente esta forma, sin parseo de markdown.

CAMBIO DE ARQUITECTURA (Documento 3, Sección 4 y 5):
  - EstadoActivo ya NO es el output completo de una sola llamada libre.
    Es el output de la Llamada 1, restringido en tiempo de ejecución al
    conjunto de candidatos que produce compute_candidate_states()
    (ver engine_estado.py). El campo `estado` sigue siendo un Literal
    con los 6 valores del catálogo completo porque Pydantic no puede
    generar un enum dinámico por corrida — la restricción real ocurre
    en el prompt de la Llamada 1 (que solo describe los candidatos del
    día) y se verifica con la aserción de integridad de la Sección 6.2
    del Documento 3, no confiando en el tipo por sí solo.
  - `exhaustion` DEJA de ser un valor posible de estado. Pasa a
    ModificadorContexto como exhaustion_alcista/exhaustion_bajista
    (Documento 2 §12).
  - `contexto_favorable` y `contexto_turbulento` quedan fuera de esta
    versión: no tienen equivalente en el catálogo cerrado de
    modificadores del Documento 2 §12. Si se necesitan, es una vuelta
    al Documento 2 primero, no un agregado silencioso acá.
  - EstadoActivo separa la salida "estado ya confirmado" de la salida
    "estado provisional en evaluación" en dos campos distintos en vez
    de mezclarlos en un solo Literal, para que nunca sea ambiguo si lo
    que se persiste es un estado firme o una hipótesis de transición
    (Documento 2 §5.1).
"""

from typing import Literal
from pydantic import BaseModel, Field


# --- Documento 2 §6: catálogo cerrado de estados ---
EstadoMercado = Literal[
    "equilibrio",
    "acumulacion",
    "distribucion",
    "price_discovery_alcista",
    "price_discovery_bajista",
    "reacumulacion",   # pausa dentro de PD Alcista — Doc2 §6.6
    "redistribucion",  # pausa dentro de PD Bajista — Doc2 §6.7
]

# --- Documento 2 §5.1: hacia qué estado apunta un Estado Provisional abierto ---
# None = no hay Estado Provisional abierto hoy.
EstadoProvisionalHacia = EstadoMercado | None

# --- Documento 2 §12: catálogo cerrado de modificadores (0, 1, o más) ---
ModificadorContexto = Literal[
    "exhaustion_alcista",
    "exhaustion_bajista",
    "contexto_conflictivo",
    "esperando_catalizadores",
    "desacople_intermarket",
]

# --- Dirección hacia la que apunta un Estado Provisional abierto ---
# None = no hay Estado Provisional abierto hoy.
# Nota: reacumulacion y redistribucion son destinos válidos de EP
# (desde PD Alcista y PD Bajista respectivamente — Doc2 §7).
EstadoProvisionalHacia = EstadoMercado | None

Conviccion = Literal["ALTA", "MEDIA", "BAJA"]

# --- Comparación de la hipótesis anterior contra la actual ---
EstadoHipotesisPrevia = Literal[
    "vigente_sin_cambios",
    "vigente_fortalecida",
    "vigente_debilitada",
    "invalidada",
    "no_aplica_primera_corrida",
]


class EstadoActivo(BaseModel):
    """
    Output de la Llamada 1 (Documento 3 §5). Decidido SOLO con evidencia
    9.1+9.2+9.3 (precio, derivadas de precio, posicionamiento) y
    restringido al conjunto de candidatos de compute_candidate_states().
    No lleva modificadores, hipótesis ni narrativa — esos son output de
    la Llamada 2, que recibe este objeto como dato ya fijado.
    """

    estado: EstadoMercado = Field(
        description=(
            "El estado vigente HOY. Si hay un Estado Provisional abierto "
            "que todavía no se resolvió, este campo mantiene el estado "
            "PREVIO (el provisional no reemplaza al vigente hasta "
            "confirmarse — Documento 2 §5.1)."
        )
    )
    estado_provisional_hacia: EstadoProvisionalHacia = Field(
        default=None,
        description=(
            "Si hoy se abrió o sigue abierta una hipótesis de transición, "
            "el estado destino que se está evaluando. None si no hay "
            "ninguna transición en evaluación."
        ),
    )
    conviccion: Conviccion
    evidence_keys: list[str] = Field(
        description=(
            "Claves concretas del payload (solo 9.1/9.2/9.3) que sostienen "
            "el estado elegido, ej. 'pricing.current.zscore_1d', "
            "'cot.snapshots.current.m_money_percentile_5y'. Ancla "
            "anti-alucinación — Documento 2 §9."
        )
    )


class InvalidacionCheck(BaseModel):
    """
    Versión estructurada y opcional de 'invalidaria', usada por el
    pre-check determinístico de la Sección 6.1 del Documento 3. Convive
    con el campo de texto libre 'invalidaria' (que sigue siendo
    obligatorio y es lo que se muestra en la narrativa) — este campo es
    solo para cuando la condición ES expresable como comparación
    numérica sobre una clave del payload.
    """
    evidence_key: str = Field(
        description="Clave del payload a evaluar, ej. 'cot.snapshots.current.m_money_percentile_5y'."
    )
    operador: Literal["<", "<=", ">", ">=", "=="]
    umbral: float


class Hipotesis(BaseModel):
    """4 componentes — la distinción presente/futuro es la clave."""
    sostiene: str = Field(description="Evidencia actual que apoya el escenario.")
    debilita: str = Field(
        description="Evidencia actual en contra, sin llegar a invalidar. Presente, no futuro."
    )
    invalidaria: str = Field(
        description=(
            "Condición futura falseable que rompería la tesis, en texto "
            "narrativo. Futuro, no presente."
        )
    )
    invalidaria_check: InvalidacionCheck | None = Field(
        default=None,
        description=(
            "Si 'invalidaria' es expresable como comparación numérica "
            "sobre una clave del payload, completar acá — es el insumo "
            "del pre-check determinístico (Documento 3 §6.1). Si la "
            "condición es cualitativa (no expresable así), dejar en None: "
            "el sistema lo trata como invalidación no-determinística y "
            "exige el chequeo manual de la Sección 6.3."
        ),
    )
    podria_acelerarla: str = Field(
        description="Condición futura que confirmaría/reforzaría el escenario más rápido."
    )


class EvidenciaCitada(BaseModel):
    """
    Observación + su ancla de evidencia obligatoria.

    Corrección Aug 2026 (smoke test GOLD, primera corrida real): antes
    3 de las 5 preguntas de Paranoia eran texto libre. El modelo citó
    "encuestas de sentimiento del consumidor" en
    que_metrica_no_termina_de_cerrar — una fuente que no existe en el
    Data Layer, sin que nada en el schema forzara un ancla verificable.
    Con evidence_key obligatorio, el post-check
    (evidence_map.assert_evidence_keys_exist) puede rechazar la corrida
    si la clave no existe de verdad en el payload — no alcanza con que
    el modelo "diga" que hay una fuente.
    """
    observacion: str = Field(description="La observación en sí, en texto narrativo.")
    evidence_key: str = Field(
        description=(
            "Clave del payload que sostiene esta observación, ej. "
            "'fred.NFCI.current.zscore' (fred.* solo tiene métricas "
            "derivadas — nunca '.value'). Tiene que existir de verdad en "
            "el payload de esta corrida — se valida en post-check y la "
            "corrida se rechaza si no existe (Documento 2 §9)."
        )
    )


class Paranoia(BaseModel):
    """5 preguntas. Las 3 que citan datos concretos requieren evidence_key
    verificable (Documento 3, corrección Aug 2026); las otras 2 son
    reformulaciones de campos ya anclados en otro lado (invalidaria) o
    no citan un dato puntual del payload."""
    que_estoy_ignorando: EvidenciaCitada = Field(
        description="Una métrica del payload NO mencionada en la traducción macro, y por qué se consideró secundaria."
    )
    que_me_hace_ruido: EvidenciaCitada = Field(
        description="Una métrica cuyo signo/magnitud es inconsistente con el resto del relato."
    )
    que_metrica_no_termina_de_cerrar: EvidenciaCitada = Field(
        description="Un dato con confianza baja en sí mismo (poca profundidad histórica, ventana corta, etc.), no una duda filosófica."
    )
    que_podria_cambiar_la_opinion: str = Field(
        description="Debe ser la misma condición de 'invalidaria' reformulada de cara a la semana próxima, no una idea nueva."
    )
    variable_a_vigilar: str = Field(
        description="UNA sola variable explícita, no una lista. Fuerza a priorizar."
    )
    contexto_limpio: bool = Field(
        description="True si genuinamente no hay nada real que objetar esta semana."
    )


class Memoria(BaseModel):
    """Comparación contra la corrida anterior (Documento 2 §14)."""
    estado_hipotesis_previa: EstadoHipotesisPrevia
    explicacion: str = Field(
        description=(
            "Si invalidada, debe citar explícitamente qué condición de "
            "'invalidaria' se cruzó. Si 'vigente_sin_cambios' pero el "
            "estado de EstadoActivo cambió respecto de ayer, la corrida es "
            "inconsistente por definición (Documento 2 §14) y se rechaza "
            "en post-check — Documento 3 §6.3."
        )
    )
    variable_vigilada_semana_pasada_aparecio_hoy: bool = Field(
        description="False si es la primera corrida o si no apareció. True si se mencionó en la traducción macro de hoy."
    )


class ContextoOutput(BaseModel):
    """
    Output de la Llamada 2 (Documento 3 §5). Recibe EstadoActivo ya
    fijado como dato de solo lectura — este schema NO incluye ningún
    campo de estado, por lo que estructuralmente no puede reescribirlo.
    Usa evidencia 9.4 (contextual) para modificadores, hipótesis y
    narrativa.
    """

    modificadores: list[ModificadorContexto] = Field(
        default_factory=list,
        description="Uno o más, o ninguno. Ver Documento 2 §12 — pueden coexistir."
    )

    frase_puente: str = Field(
        description="1 frase. Bisagra entre el titular técnico y la traducción macro completa — regla de los 10 segundos."
    )

    traduccion_macro: str = Field(
        description=(
            "Largo variable según triggers de elasticidad. Debe anclarse a "
            "los criterios del estado ya fijado por la Llamada 1, no ser "
            "texto genérico con el estado pegado arriba."
        )
    )

    en_criollo: str = Field(
        description="Espejo de traduccion_macro en lenguaje informal. NUNCA introduce información nueva."
    )

    hipotesis: Hipotesis
    paranoia: Paranoia
    memoria: Memoria


class AssetTranslatorOutput(BaseModel):
    """
    Objeto completo persistido — resultado de ensamblar EstadoActivo
    (Llamada 1) + ContextoOutput (Llamada 2). Este ensamblado ocurre en
    código (engine.py orquestador), nunca dentro de una sola llamada al
    modelo — ver Documento 3 §5.
    """

    estado: EstadoActivo
    contexto: ContextoOutput
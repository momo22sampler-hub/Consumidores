"""
engine.py — Motor de caracterización de Estado (Sentinel Market State Model)
Intelligence Layer / Arkad Tools

run_engine(asset_key, payload, previous_state, previous_hypothesis) es el
único punto de entrada. Reemplaza a la versión V1 de una sola llamada
libre por el pipeline del Documento 3 §5:

  Etapa 0 (sin LLM)  -> precheck.py + engine_estado.compute_candidate_states()
  Llamada 1 (LLM)    -> EstadoActivo, restringida a los candidatos de Etapa 0
  Post-check 6.2     -> aserción de integridad contra TRANSITION_MATRIX
  Llamada 2 (LLM)    -> ContextoOutput, con estado ya fijado como solo-lectura
                        y la hipótesis previa (o None) como dato explícito
  Post-check 6.3     -> coherencia Memoria/Estado (también en bootstrap)

Cada llamada tiene su propio response_schema — dos objetos Pydantic
distintos, no un único AssetTranslatorOutput libre como en V1.

CORRECCIÓN 2026-08 (encontrada en smoke test real contra Gemini, sin
mock, activo GOLD, primera corrida): la Llamada 2 no recibía
'previous_hypothesis' en absoluto, así que no tenía con qué comparar
para llenar 'Memoria' (Documento 2 §14) y alucinó
'estado_hipotesis_previa=vigente_sin_cambios' en una corrida bootstrap
sin ninguna hipótesis previa real. El post-check 6.3 tampoco corría en
bootstrap, así que no lo atrapó. Se corrige pasando 'memoria_previa'
explícito a la Llamada 2 (ver payload_filter.build_contextual_call_payload)
y corriendo el post-check 6.3 siempre, con una rama específica para
bootstrap que exige 'no_aplica_primera_corrida'.

CORRECCIÓN 2026-08 (2) (mismo smoke test): la misma corrida mostró dos
huecos más. (a) que_metrica_no_termina_de_cerrar citó una fuente que no
existe en el Data Layer ("encuestas de sentimiento del consumidor") sin
ancla verificable — Paranoia pasa a exigir {observacion, evidence_key}
en sus 3 preguntas basadas en datos (output_schema.EvidenciaCitada), y
se agrega un post-check (_assert_paranoia_evidence_exists, usando
evidence_map.assert_evidence_keys_exist) que rechaza la corrida si esa
evidence_key no existe de verdad en el payload. (b) la narrativa habló
de "tasas reales" pese a que config.py documenta explícitamente que no
existe DFII10/TIPS en el Data Layer — se agrega regla explícita en el
prompt de la Llamada 2 prohibiendo presentar un proxy (DGS10 + PCEPILFE)
como si fuera el dato que aproxima.

CORRECCIÓN 2026-08 (3) (checklist post-smoke-test, mismo bug de origen
sin cerrar en el resto del sistema): assert_evidence_keys_exist() solo
se aplicaba a Paranoia. EstadoActivo.evidence_keys (Llamada 1) y
Hipotesis.invalidaria_check.evidence_key (Llamada 2) pasaban el chequeo
de tier (assert_only_structural) pero nadie confirmaba que existieran
de verdad en el payload. Se agregan _assert_estado_evidence (dentro de
_call_llamada_1, contra structural_payload) y
_assert_invalidaria_check_evidence_exists (post Llamada 2, contra el
payload completo).

CORRECCIÓN 2026-08 (4) — Bug de Bootstrap (checklist post-smoke-test):
en modo bootstrap (is_first_run=True), el CandidateSet ofrecía los 6
estados del catálogo completo. Esto viola Documento 2 §6.6 y §8:
reacumulacion_redistribucion exige un Price Discovery previo comprobable
("ocurre DENTRO de un Price Discovery ya activo"), y price_discovery
exige una resolución previa de Acumulación/Distribución. Sin historial
real ninguna de estas condiciones puede verificarse — ofrecerlas como
candidatas el día 1 sería una alucinación estructural. El bootstrap
queda restringido a [equilibrio, acumulacion, distribucion].

CORRECCIÓN 2026-08 (5) — esperando_catalizadores 100% frecuencia:
el criterio de verdad del modificador era demasiado laxo ("existen
eventos de calendario relevantes pendientes"). Se reescribió con triple
condición SIMULTÁNEA: (1) evento de alto impacto en calendar_upcoming,
(2) relevancia DEMOSTRABLE para ESTE activo específico, (3) timing
inminente respecto al Estado Provisional activo o próxima corrida. La
mera presencia de publicaciones en el calendario global ya no es
condición suficiente.
"""

import json
import os
import time

from google import genai
from google.genai import types

from output_schema import (
    AssetTranslatorOutput,
    ContextoOutput,
    EstadoActivo,
)
from engine_estado import CandidateSet, compute_candidate_states, is_valid_transition
from evidence_map import assert_only_structural, assert_evidence_keys_exist
from payload_filter import build_contextual_call_payload, build_structural_payload
from precheck import check_invalidacion_confirmada, check_ruptura_precio

MODEL_NAME = "gemini-flash-lite-latest"

MAX_GENERATE_RETRIES = 5
RETRY_BASE_DELAY_SECONDS = 8


def _is_transient_error(exc: Exception) -> bool:
    code = getattr(exc, "code", None)
    if code in (429, 500, 503):
        return True
    text = str(exc)
    return any(marker in text for marker in ("503", "429", "UNAVAILABLE", "RESOURCE_EXHAUSTED"))


def _generate_content_with_retry(client: genai.Client, **kwargs):
    last_exc: Exception | None = None
    for attempt in range(1, MAX_GENERATE_RETRIES + 1):
        try:
            return client.models.generate_content(**kwargs)
        except Exception as exc:
            last_exc = exc
            is_last_attempt = attempt == MAX_GENERATE_RETRIES
            if not _is_transient_error(exc) or is_last_attempt:
                raise
            delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
            print(
                f"[engine] Gemini no disponible (intento {attempt}/{MAX_GENERATE_RETRIES}). "
                f"Reintentando en {delay}s... ({exc})"
            )
            time.sleep(delay)
    raise last_exc


class TransitionIntegrityError(RuntimeError):
    """Post-check 6.2: la Llamada 1 devolvió una transición fuera de la
    matriz pese a estar restringida a los candidatos de la Etapa 0. Esto
    indica un bug en compute_candidate_states() o en el prompt — se
    trata como error de sistema, no se reintenta silenciosamente."""


class MemoriaEstadoInconsistencyError(RuntimeError):
    """Post-check 6.3 (Documento 2 §14): Memoria dice 'vigente_sin_cambios'
    pero el estado de hoy difiere del de ayer — o, en bootstrap, Memoria
    dice cualquier cosa distinta de 'no_aplica_primera_corrida' cuando
    no había ninguna hipótesis previa real contra la cual comparar."""


class ParanoiaEvidenceError(RuntimeError):
    """Post-check (Documento 3, corrección Aug 2026 — hallado en smoke
    test real de GOLD): la Llamada 2 citó una evidence_key en Paranoia
    (que_estoy_ignorando / que_me_hace_ruido / que_metrica_no_termina_de_cerrar)
    que no existe de verdad en el payload de esta corrida. Alucinación de
    fuente — Documento 2 §9 lo prohíbe explícitamente."""


class EstadoEvidenceError(RuntimeError):
    """Post-check (Documento 3, corrección Aug 2026 — 2da mitad del mismo
    bug de origen que ParanoiaEvidenceError, cerrado en el mismo pase por
    el checklist post-smoke-test): la Llamada 1 citó en
    EstadoActivo.evidence_keys una clave que pasa el chequeo de TIER
    (assert_only_structural) pero no existe de verdad en el payload
    estructural. Mismo riesgo que Paranoia, mismo remedio."""


class HipotesisEvidenceError(RuntimeError):
    """Post-check (Documento 3, corrección Aug 2026, misma tanda): si
    Hipotesis.invalidaria_check no es None, su evidence_key tiene que
    existir de verdad en el payload — si no, el pre-check determinístico
    de mañana (precheck.check_invalidacion_confirmada) simplemente
    devolvería None en silencio por una clave inventada hoy, degradando
    la Etapa 0 sin que nadie lo note."""


# ---------------------------------------------------------------------------
# Etapa 0
# ---------------------------------------------------------------------------

def _run_etapa_0(
    estado_previo: str,
    estado_provisional_previo: str | None,
    payload: dict,
    hipotesis_previa: dict | None,
) -> CandidateSet:
    invalidacion_confirmada = check_invalidacion_confirmada(payload, hipotesis_previa)
    ruptura_precio_detectada = check_ruptura_precio(payload)
    candidate_set = compute_candidate_states(
        estado_previo=estado_previo,
        estado_provisional_previo=estado_provisional_previo,
        invalidacion_confirmada=invalidacion_confirmada,
        ruptura_precio_detectada=ruptura_precio_detectada,
    )
    print(f"[engine][etapa_0] {candidate_set.motivo}")
    return candidate_set


# ---------------------------------------------------------------------------
# Llamada 1 — Estado y Convicción (solo 9.1+9.2+9.3)
# ---------------------------------------------------------------------------

# Documento 2 §6 — Catálogo de Estados. Definición formal de cada estado
# (Entrada obligatoria + Apoyo/firma característica + qué NO califica
# todavía), inlineada acá para que la Llamada 1 aplique el criterio real
# del proyecto en vez de asociación libre con Wyckoff general. Se muestra
# solo la definición de los estados que compute_candidate_states() dejó
# como candidatos ese día — no las 6 siempre.
_ESTADO_DEFINICIONES: dict[str, str] = {
    "equilibrio": (
        "Equilibrio/Rango — el precio y el valor coinciden, sin sesgo "
        "direccional identificable. Entrada (obligatoria): compresión de "
        "rango (barras de rango decreciente respecto al tramo direccional "
        "previo), rotación entre extremos sin dirección neta sostenida, "
        "ausencia de una racha de cierres consecutivos hacia un extremo. "
        "Apoyo: COT sin sesgo sostenido (percentil de Managed Money sin "
        "tendencia clara entre snapshot actual, w4 y w12)."
    ),
    "acumulacion": (
        "Acumulación — caso particular de rango donde el precio muestra "
        "agotamiento de una tendencia BAJISTA previa Y el COT muestra manos "
        "fuertes incrementando posición de forma SOSTENIDA pese al precio "
        "deprimido. Entrada (obligatoria): precio en zona baja relativa, "
        "señales de pérdida de momentum bajista (rango de barras "
        "decreciente, mechas inferiores, shortening of the thrust). Apoyo "
        "— firma característica del estado, NO opcional: percentil de "
        "Managed Money en ASCENSO SOSTENIDO en varias semanas (actual vs. "
        "w4 vs. w12), incluso si el precio todavía no acompaña. Sin esa "
        "divergencia precio-deprimido + COT-ascendente, no es Acumulación "
        "— es, como máximo, Equilibrio. La presión de evidencia contextual "
        "(tasas, DXY, calendario) no es, por sí sola, motivo para "
        "abandonar este estado."
    ),
    "distribucion": (
        "Distribución — análogo simétrico de Acumulación. Entrada "
        "(obligatoria): precio en zona ALTA relativa, con señales de "
        "pérdida de momentum ALCISTA (rango decreciente, mechas "
        "superiores) que se vienen viendo ANTES de cualquier caída — no "
        "una caída abrupta de una sola vela sin desgaste previo. Apoyo — "
        "firma característica, NO opcional: percentil de Managed Money en "
        "DESCENSO SOSTENIDO en varias semanas, con el precio todavía firme "
        "o eufórico. Sin ese desgaste previo Y esa divergencia de COT, no "
        "es Distribución."
    ),
    "price_discovery_alcista": (
        "Price Discovery Alcista (Markup) — desequilibrio vertical con "
        "dirección alcista CONFIRMADA, no solo un buen día. Entrada "
        "(obligatoria): barras de rango amplio, cierres CONSISTENTEMENTE "
        "cerca de máximos (sostenido en el tiempo, no una sola sesión), "
        "bajo consumo de tiempo por nivel de precio, ausencia de reingreso "
        "a la zona previa. Un movimiento que todavía está DENTRO de un "
        "rango previamente establecido, sin haberlo superado de forma "
        "sostenida, no califica todavía aunque el impulso reciente sea "
        "fuerte."
    ),
    "price_discovery_bajista": (
        "Price Discovery Bajista (Markdown) — análogo simétrico de Price "
        "Discovery Alcista, dirección bajista: barras de rango amplio, "
        "cierres CONSISTENTEMENTE cerca de mínimos, sostenido en el "
        "tiempo. Una ruptura de un solo período — incluso violenta — no "
        "es, por sí sola, Price Discovery Bajista: es la evidencia que "
        "ABRE un Estado Provisional hacia este destino, no la "
        "confirmación de que ya se llegó."
    ),
    "reacumulacion": (
        "Reacumulación — pausa lateral dentro de un Price Discovery "
        "ALCISTA ya confirmado (Doc2 §6.6). Estructuralmente idéntica a "
        "Acumulación en la evidencia de precio (compresión de rango, "
        "barras decrecientes), pero el estado previo era Price Discovery "
        "Alcista — esa es la única diferencia con Acumulación. Sin ese "
        "antecedente directo de PD Alcista en el historial, el estado no "
        "puede ser Reacumulación. Apoyo: el COT puede mostrar toma de "
        "ganancias parcial (baja del percentil desde máximos) sin que eso "
        "invalide el estado por sí solo. Salida vía EP: retoma del PD "
        "Alcista (más probable) o falla de continuación hacia Equilibrio."
    ),
    "redistribucion": (
        "Redistribución — análogo simétrico de Reacumulación (Doc2 §6.7), "
        "dentro de un Price Discovery BAJISTA ya confirmado. Misma "
        "evidencia de precio que Distribución (compresión de rango), pero "
        "el estado previo era Price Discovery Bajista. Sin ese antecedente "
        "directo, el estado no puede ser Redistribución. Apoyo: el COT "
        "puede mostrar cobertura táctica parcial sin invalidar el estado. "
        "Salida vía EP: retoma del PD Bajista (más probable) o falla de "
        "continuación hacia Equilibrio."
    ),
}


def _system_prompt_llamada_1(
    candidate_set: CandidateSet,
    estado_previo: str,
    cot_primary: str,
    cot_secondary: str,
) -> str:
    candidatos_fmt = ", ".join(candidate_set.candidatos)
    definiciones_fmt = "\n".join(
        f"- {_ESTADO_DEFINICIONES[c]}" for c in candidate_set.candidatos
    )
    return f"""Sos el motor de Estado de un Asset Translator macro, siguiendo el \
Sentinel Market State Model (Documento 2). Tu único trabajo hoy es decidir \
el estado del mercado y la convicción de esa lectura, usando EXCLUSIVAMENTE \
la evidencia de Precio (9.1), evidencia derivada de precio (9.2) y \
posicionamiento institucional — COT o ETF Flows según el activo (9.3), que \
es todo lo que tenés disponible en este payload. No tenés evidencia \
contextual (macro, calendario, geopolítica, sentiment) — no la necesitás \
para esta decisión, y si el payload no la trae es a propósito.

## Estado previo
El estado vigente hasta ayer era: {estado_previo}

## Tus únicas opciones válidas hoy
{candidatos_fmt}

No es una lista de sugerencias — es el catálogo COMPLETO de lo que podés \
elegir. Estas opciones ya fueron calculadas de forma determinística contra \
la matriz de transiciones del Documento 2 §7 y el estado previo. Si te \
parece que la evidencia apunta a un estado que no está en esta lista, es \
señal de que la transición requiere más confirmación de la que hay hoy — \
elegí la opción de esta lista que mejor sostenga la evidencia, no fuerces \
una que no está.

## Definición formal de cada opción (Documento 2 §6)
No elijas por asociación libre con lo que "generalmente" significa cada \
nombre — usá exactamente el criterio de entrada y de apoyo que sigue. Si \
la evidencia de hoy no cumple la entrada obligatoria (y, cuando aplique, \
la firma característica de apoyo) de una opción, esa opción no te \
corresponde aunque el nombre te parezca el más intuitivo:
{definiciones_fmt}

## Convicción
ALTA si 9.1, 9.2 y 9.3 están alineados entre sí sosteniendo el mismo \
estado. MEDIA si hay alguna tensión entre precio y posicionamiento sin \
llegar a contradecirse. BAJA si hay conflicto genuino entre precio y \
posicionamiento (Documento 2 §10 — precio manda para decidir el estado, \
pero el conflicto en sí baja la convicción, no cambia el estado).

## evidence_keys — reglas de citado
Cada clave citada tiene que pertenecer a pricing.*, cot.* o etf_flows.* — \
no tenés otra cosa en el payload para citar.

IMPORTANTE para COT: para este activo, los únicos campos COT con datos \
reales (no None) son los de las categorías '{cot_primary}' (primaria) y \
'{cot_secondary}' (secundaria). Si citás cot.*.OTRA_CATEGORIA_CUALQUIERA.* \
será rechazado porque esos campos son None para este activo. Solo podés \
citar claves bajo 'cot.snapshots.*.{cot_primary}_*' y \
'cot.snapshots.*.{cot_secondary}_*' (más open_interest_* que siempre existe).
"""


def _call_llamada_1(
    client: genai.Client,
    structural_payload: dict,
    candidate_set: CandidateSet,
    estado_previo: str,
    cot_primary: str = "m_money",
    cot_secondary: str = "swap",
) -> EstadoActivo:
    system_prompt = _system_prompt_llamada_1(
        candidate_set, estado_previo, cot_primary, cot_secondary
    )
    response = _generate_content_with_retry(
        client,
        model=MODEL_NAME,
        contents=json.dumps(structural_payload, default=str),
        config=types.GenerateContentConfig(
            system_instruction=system_prompt,
            response_mime_type="application/json",
            response_schema=EstadoActivo,
        ),
    )
    estado_activo: EstadoActivo = response.parsed
    assert_only_structural(estado_activo.evidence_keys)
    try:
        assert_evidence_keys_exist(estado_activo.evidence_keys, structural_payload)
    except ValueError as exc:
        # Se valida contra structural_payload, no contra el payload
        # completo: es lo único que la Llamada 1 tuvo disponible, así
        # que es el único universo posible de claves reales para ella.
        raise EstadoEvidenceError(str(exc)) from exc
    return estado_activo


# ---------------------------------------------------------------------------
# Post-check 6.2 — aserción de integridad
# ---------------------------------------------------------------------------

def _assert_transition_integrity(estado_previo: str, estado_activo: EstadoActivo) -> None:
    destino = estado_activo.estado_provisional_hacia or estado_activo.estado
    if not is_valid_transition(estado_previo, destino):
        raise TransitionIntegrityError(
            f"Llamada 1 devolvió una transición fuera de matriz: "
            f"{estado_previo!r} -> {destino!r}. Esto no debería ser posible "
            f"si Etapa 0 restringió bien los candidatos — revisar "
            f"compute_candidate_states() y el prompt de la Llamada 1."
        )


# ---------------------------------------------------------------------------
# Llamada 2 — Contexto, Modificadores, Hipótesis, Narrativa (payload completo)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_LLAMADA_2 = """Sos la segunda etapa del motor de Asset \
Translator macro (Sentinel Market State Model, Documento 2). El estado y \
la convicción de hoy YA fueron decididos por la Llamada 1 con evidencia \
estructural (precio + posicionamiento) — los recibís en \
'estado_fijado_por_llamada_1' como dato de solo lectura. No podés \
cambiarlos, y tu output no tiene ningún campo para hacerlo.

Tu trabajo es, usando el payload completo (incluida evidencia contextual: \
FRED, correlaciones, calendario, geopolítica, sentiment):

## Modificadores — Documento 2 §12 — catálogo cerrado, criterios de verdad explícitos
Ninguno de estos es un estado. Nunca contradicen ni reemplazan \
'estado_fijado_por_llamada_1'. El catálogo vacío (0 modificadores) es una \
respuesta válida y frecuente — no es una falla, es la respuesta correcta \
cuando ningún criterio se cumple. Elegí ÚNICAMENTE los que cumplen su \
criterio de verdad tal como se define a continuación:

### exhaustion_alcista / exhaustion_bajista
Criterio necesario y suficiente: hay pérdida de momentum demostrable —\
medida en el precio, no inferida de contexto — de uno de los dos lados \
DENTRO del estado vigente actual (Price Discovery, Acumulación o \
Distribución). Señales concretas: shortening of the thrust, mechas de \
rechazo en extremos del rango, rango de barras decreciente en la dirección \
del movimiento. NO es una condición de ánimo o de expectativa — tiene que \
verse en los datos de precio del payload. Es la evidencia que PUEDE abrir \
un Estado Provisional, pero no es en sí misma un destino. Un mercado no \
"está en Exhaustion": un Price Discovery Bajista "muestra Exhaustion \
bajista". Si no está presente en los datos estructurales del payload, no \
lo marques.

### contexto_conflictivo
Criterio: la evidencia contextual (9.4: FRED, correlaciones, calendario, \
geopolítica, sentiment) apunta en sentido CONTRARIO y MATERIAL a la \
evidencia estructural (9.1/9.2/9.3) que sostiene el estado vigente. La \
tensión tiene que ser real, nombrable y no trivial. NO califica: ruido \
geopolítico de fondo, evidencia contextual ambigua, datos que simplemente \
"no confirman" el estado sin contradecirlo. Si lo marcás, la narrativa \
DEBE nombrar explícitamente cuál evidencia estructural específica choca con \
cuál evidencia contextual específica. Una etiqueta sin ese anclaje \
bilateral en el texto es un error.

### esperando_catalizadores
Criterio ESTRICTO — se aplica ÚNICAMENTE si las TRES condiciones siguientes \
se cumplen de forma SIMULTÁNEA:
(1) Existe al menos un evento en 'calendar_upcoming' con impacto ALTO para \
    los mercados (ej. decisión de tasas Fed/BCE/BoJ, CPI, NFP, GDP flash, \
    earnings de primer nivel si el activo es un índice) que todavía no fue \
    publicado en el momento de esta corrida.
(2) Ese evento tiene capacidad DEMOSTRABLE de alterar el estado estructural \
    ESPECÍFICO de ESTE activo — no del mercado en general. Un dato de \
    empleo puede ser irrelevante para un activo de materias primas con \
    drivers propios; una decisión de la Fed es irrelevante para un par de \
    divisas que no involucra al dólar. La relevancia tiene que poder \
    nombrarse en la narrativa con una cadena causal concreta.
(3) El timing es inminente — dentro de la próxima semana, o dentro de la \
    ventana de validación del Estado Provisional activo si lo hay.
Si no se cumplen las TRES condiciones de forma simultánea para ESTE \
activo, NO marques este modificador aunque el calendario económico global \
esté cargado de publicaciones. La existencia de cualquier evento en \
'calendar_upcoming' NO es condición suficiente por sí sola. Si lo marcás, \
la narrativa DEBE nombrar el evento específico, su fecha, y la cadena \
causal que lo conecta con la estructura de ESTE activo.

### desacople_intermarket
Criterio: una correlación históricamente estable entre este activo y otra \
serie ('correlations.*') se rompió de forma medible en el período reciente. \
Tenés que poder nombrar CUÁL par específico se desacopló (ej. "oro vs. \
DXY") y citar la clave del payload que muestra la ruptura. Es evidencia \
contextual (9.4): se señala, pero nunca dispara una transición por sí \
sola. Si no podés nombrar el par y la clave del payload, no lo marques.

Si marcás cualquier modificador, la narrativa DEBE anclar explícitamente \
la evidencia concreta que lo sostiene. Un modificador sin anclaje explícito \
en el texto narrativo es un error.

## Hipótesis (Documento 2 §14)
sostiene/debilita son presente. invalidaria/podria_acelerarla son futuro. \
La condición de invalidación de la hipótesis y la condición de salida del \
estado vigente DEBEN ser la misma evidencia. Si 'invalidaria' es expresable \
como comparación numérica sobre una clave del payload, completá también \
'invalidaria_check' — es lo que permite que mañana el sistema chequee la \
invalidación sin depender de vos. Si la condición es cualitativa, dejá \
'invalidaria_check' en null.

## Paranoia — que_estoy_ignorando / que_me_hace_ruido / que_metrica_no_termina_de_cerrar
Estas 3 preguntas NO son texto libre: cada una es un objeto con \
'observacion' (el texto) y 'evidence_key' (la clave EXACTA del payload que \
sostiene esa observación, ej. 'fred.NFCI.current.zscore'). La evidence_key \
tiene que ser una clave que EXISTE de verdad en el payload que te llegó — \
no una fuente que "debería" existir, no algo que sabés por conocimiento \
general de mercados. Ojo en particular con 'fred.*': cada serie viene SOLO \
como métricas derivadas (zscore, sma_short, sma_long, dist_sma_short, \
dist_sma_long, pct_rank_1y, pct_rank_3y, pct_rank_5y, change_pct_1p/1m/3m/6m/12m, \
change_abs_*, vol_short, vol_long, dist_from_high_1y, dist_from_low_1y) — \
NUNCA hay un campo con el nivel/valor crudo de la serie, así que una \
evidence_key terminada en '.value' o '.nivel' nunca es válida para 'fred.*'. \
Si no podés señalar la clave exacta del payload para una observación, es \
señal de que esa observación no pertenece acá: elegí otra métrica que sí \
puedas anclar. La corrida se rechaza automáticamente si citás una \
evidence_key que no existe en el payload real.

## Prohibido presentar un proxy como si fuera el dato que aproxima
Si el payload trae 'known_limitations' para este activo, son restricciones \
reales del Data Layer, no sugerencias — respetalas al pie de la letra en \
toda la narrativa (traduccion_macro, en_criollo, hipótesis, paranoia). Regla \
general, aplique o no 'known_limitations' explícito: nunca uses un término \
técnico o de mercado (ej. "tasas reales", "yield real") para describir algo \
que en realidad estás inferiendo de una combinación de otras series (ej. \
tasa nominal DGS10 + inflación PCEPILFE). Si el payload no tiene la serie \
específica (ej. no hay DFII10/TIPS), hablá en los términos de lo que sí \
tenés — "tasas nominales elevadas junto con inflación persistente" — nunca \
en los términos del dato que no tenés. Esto aplica incluso si el resultado \
narrativo es casi equivalente: la forma de hablar tiene que reflejar la \
evidencia real disponible, no el concepto de mercado que esa evidencia \
aproxima.

## Memoria — leé 'memoria_previa' antes de responder esto
'memoria_previa' es la hipótesis/paranoia REAL de la corrida anterior, tal \
cual quedó guardada — no la inventes ni la infieras de otra cosa.

Si 'memoria_previa' es null: es la PRIMERA corrida schema_version=2 para \
este activo (no hay ninguna hipótesis previa real, sin importar lo que \
diga el estado de hoy). En ese caso 'estado_hipotesis_previa' TIENE que \
ser exactamente 'no_aplica_primera_corrida', y 'explicacion' debe decir \
explícitamente que es la primera corrida — nunca 'vigente_sin_cambios' \
ni ningún otro valor, porque no hay nada contra qué estar "vigente" o \
"sin cambios".

Si 'memoria_previa' NO es null: comparás su 'hipotesis' contra la de hoy. \
Si estado_hipotesis_previa es 'vigente_sin_cambios', el estado de \
'estado_fijado_por_llamada_1' TIENE que ser igual al de ayer — si no lo \
es, no es 'vigente_sin_cambios', es como mínimo \
'vigente_fortalecida'/'vigente_debilitada' o 'invalidada', y tenés que \
citar qué condición de 'invalidaria' (la de 'memoria_previa', no una \
nueva que inventes) se cruzó.

## Narrativa (frase_puente, traduccion_macro, en_criollo)
Ancladas al estado ya fijado, no a lo que vos hubieras elegido. Misma \
disciplina de evidence_keys y de en_criollo sin jerga que en versiones \
anteriores del motor.
"""


def _call_llamada_2(
    client: genai.Client,
    full_payload: dict,
    estado_activo: EstadoActivo,
    previous_hypothesis: dict | None,
) -> ContextoOutput:
    contenido = build_contextual_call_payload(
        full_payload, estado_activo.model_dump(mode="json"), previous_hypothesis,
    )
    response = _generate_content_with_retry(
        client,
        model=MODEL_NAME,
        contents=json.dumps(contenido, default=str),
        config=types.GenerateContentConfig(
            system_instruction=_SYSTEM_PROMPT_LLAMADA_2,
            response_mime_type="application/json",
            response_schema=ContextoOutput,
        ),
    )
    return response.parsed


# ---------------------------------------------------------------------------
# Post-check — evidencia real de Paranoia (Documento 3, corrección Aug 2026)
# ---------------------------------------------------------------------------

def _assert_paranoia_evidence_exists(contexto: ContextoOutput, full_payload: dict) -> None:
    """
    Corre después de la Llamada 2, contra el payload COMPLETO (9.1-9.4,
    no solo el estructural) — a diferencia de assert_only_structural()
    en la Llamada 1, acá cualquier tier es válido, lo único que se
    valida es que la clave exista de verdad.
    """
    evidence_keys = [
        contexto.paranoia.que_estoy_ignorando.evidence_key,
        contexto.paranoia.que_me_hace_ruido.evidence_key,
        contexto.paranoia.que_metrica_no_termina_de_cerrar.evidence_key,
    ]
    try:
        assert_evidence_keys_exist(evidence_keys, full_payload)
    except ValueError as exc:
        raise ParanoiaEvidenceError(str(exc)) from exc


def _assert_invalidaria_check_evidence_exists(contexto: ContextoOutput, full_payload: dict) -> None:
    """
    Documento 3, corrección Aug 2026 (mismo bug de origen que
    ParanoiaEvidenceError, cerrado en el mismo pase). Si
    invalidaria_check es None (condición cualitativa, no expresable
    numéricamente) no hay nada que validar — eso es un caso legítimo,
    no un error. Se valida contra el payload completo porque
    invalidaria_check puede referenciar 9.1-9.3 igual que 9.4 (una
    condición de invalidación puede depender de contexto, no solo de
    estructura).
    """
    check = contexto.hipotesis.invalidaria_check
    if check is None:
        return
    try:
        assert_evidence_keys_exist([check.evidence_key], full_payload)
    except ValueError as exc:
        raise HipotesisEvidenceError(str(exc)) from exc


# ---------------------------------------------------------------------------
# Post-check 6.3 — coherencia Memoria/Estado (corre SIEMPRE, incluido bootstrap)
# ---------------------------------------------------------------------------

def _assert_memoria_estado_coherence(
    is_first_run: bool,
    estado_previo: str | None,
    estado_activo: EstadoActivo,
    contexto: ContextoOutput,
) -> None:
    if is_first_run:
        if contexto.memoria.estado_hipotesis_previa != "no_aplica_primera_corrida":
            raise MemoriaEstadoInconsistencyError(
                f"Primera corrida (bootstrap, sin hipótesis previa real) pero "
                f"Memoria.estado_hipotesis_previa="
                f"{contexto.memoria.estado_hipotesis_previa!r} en vez de "
                f"'no_aplica_primera_corrida' — la Llamada 2 alucinó una "
                f"comparación contra una hipótesis que no existe."
            )
        return

    if (
        contexto.memoria.estado_hipotesis_previa == "vigente_sin_cambios"
        and estado_activo.estado != estado_previo
    ):
        raise MemoriaEstadoInconsistencyError(
            f"Memoria.estado_hipotesis_previa='vigente_sin_cambios' pero el "
            f"estado cambió de {estado_previo!r} a {estado_activo.estado!r} "
            f"en la misma corrida — Documento 2 §14."
        )


# ---------------------------------------------------------------------------
# Orquestador
# ---------------------------------------------------------------------------

def run_engine(
    asset_key: str,
    payload: dict,
    previous_state: dict | None,
    previous_hypothesis: dict | None,
) -> AssetTranslatorOutput:
    """
    previous_state:      {"estado": str, "estado_provisional_hacia": str|None, ...}
                          o None si es la primera corrida schema_version=2
                          para este activo (Documento 3 §7.1, bootstrap).
    previous_hypothesis: {"invalidaria_check": {...}|None, ...} o None.
    """
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    is_first_run = previous_state is None
    estado_previo = previous_state["estado"] if previous_state else None
    estado_provisional_previo = (
        previous_state.get("estado_provisional_hacia") if previous_state else None
    )

    structural_payload = build_structural_payload(payload)

    if is_first_run:
        # Documento 3 §7.1 + Documento 2 §6.6 y §8 — bootstrap: sin estado
        # previo real, el CandidateSet se RESTRINGE a los únicos estados
        # que no exigen un grado estructural mayor previo comprobable.
        # reacumulacion_redistribucion requiere un Price Discovery previo
        # (Doc2 §6.6: "ocurre DENTRO de un Price Discovery ya activo").
        # price_discovery_alcista/bajista requieren una resolución previa
        # de Acumulación/Distribución (Doc2 §6.4/6.5, matriz §7).
        # Sin historial, ninguna de estas condiciones puede verificarse —
        # ofrecerlas como candidatas sería una alucinación estructural.
        # El día 1 solo puede determinar si el mercado está en equilibrio
        # (base neutra), acumulación (divergencia precio bajo + COT alcista)
        # o distribución (divergencia precio alto + COT bajista).
        _BOOTSTRAP_CANDIDATES: list = [
            "equilibrio",
            "acumulacion",
            "distribucion",
        ]
        candidate_set = CandidateSet(
            candidatos=_BOOTSTRAP_CANDIDATES,
            requiere_estado_provisional=False,
            motivo=(
                "Primera corrida schema_version=2 — bootstrap restringido a "
                "[equilibrio, acumulacion, distribucion]. reacumulacion, "
                "redistribucion y price_discovery exigen grado estructural "
                "mayor previo comprobable (Documento 2 §6.6, §6.7, §8) — "
                "no disponible en día 1."
            ),
        )
        print(f"[engine][bootstrap] {asset_key}: {candidate_set.motivo}")
    else:
        candidate_set = _run_etapa_0(
            estado_previo, estado_provisional_previo, payload, previous_hypothesis
        )

    from config import get_asset_config
    asset_cfg = get_asset_config(asset_key)
    cot_primary   = asset_cfg.get("cot_primary_field_prefix", "m_money")
    cot_secondary = asset_cfg.get("cot_secondary_field_prefix", "swap")

    estado_activo = _call_llamada_1(
        client, structural_payload, candidate_set,
        estado_previo or "(sin estado previo — primera corrida)",
        cot_primary=cot_primary,
        cot_secondary=cot_secondary,
    )

    if not is_first_run:
        _assert_transition_integrity(estado_previo, estado_activo)

    contexto = _call_llamada_2(client, payload, estado_activo, previous_hypothesis)

    _assert_paranoia_evidence_exists(contexto, payload)
    _assert_invalidaria_check_evidence_exists(contexto, payload)
    _assert_memoria_estado_coherence(is_first_run, estado_previo, estado_activo, contexto)

    return AssetTranslatorOutput(estado=estado_activo, contexto=contexto)
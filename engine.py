"""

engine.py — Paso 2 del roadmap: el motor de caracterización de Estado

Intelligence Layer / Arkad Tools



run_engine(payload, previous_hypothesis, elasticity_flags) es el único

punto de entrada. Llama a Gemini con response_schema=AssetTranslatorOutput

y devuelve el objeto ya validado (sin parseo de markdown).



El prompt de sistema codifica las reglas del documento fuente que NO

pueden quedar libradas al criterio del modelo en cada corrida:

  - Tabla Eje A (fases) y Eje B (modificadores) — Sección 1.2 / 1.3

  - Regla de convicción (alineación en 3 horizontes) — Sección 1.4

  - Reglas de anclaje por fase — Sección 3

  - Distinción presente/futuro en hipótesis — Sección 4

  - Evidencia obligatoria en paranoia — Sección 5

  - Triggers de elasticidad calculados afuera, no a criterio libre — Sección 7

"""



import json

import os

import time

from datetime import date



from google import genai

from google.genai import types



from output_schema import AssetTranslatorOutput



MODEL_NAME = "gemini-flash-lite-latest"



# --- Retry para errores transitorios del lado de Gemini (Paso 2) ---

# 503 UNAVAILABLE ("high demand") y 429 RESOURCE_EXHAUSTED son errores del

# SERVIDOR de Gemini, no de nuestro payload/prompt — no tiene sentido

# fallar toda la corrida de un activo por esto. Backoff exponencial:

# 8s, 16s, 32s, 64s. Si a la 5ta sigue fallando, ahí sí es un problema

# real (API key, cuota agotada del día, etc.) y se deja propagar el error.

MAX_GENERATE_RETRIES = 5

RETRY_BASE_DELAY_SECONDS = 8





def _is_transient_error(exc: Exception) -> bool:

    """True si el error es un problema temporal del lado de Gemini (no del

    payload ni del prompt), y por lo tanto vale la pena reintentar."""

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

    # Inalcanzable en la práctica (el raise del loop cubre todos los casos),

    # pero deja explícito el contrato de la función para mypy/lectores.

    raise last_exc



SYSTEM_PROMPT = """Sos el motor de un Asset Translator macro. Tu trabajo es \

traducir un payload de métricas cuantitativas de un activo en una lectura \

de estado + hipótesis + paranoia, siguiendo reglas de diseño estrictas. \

No sos un oráculo: tu credibilidad depende de mostrar evidencia concreta \

y de admitir cuándo algo no cierra, no de sonar seguro.



## Eje A — Fase del ciclo (elegí UNA, con criterio orientador, no rígido)



| Fase | Patrón típico en los datos |

|---|---|

| price_discovery | Precio en zonas extremas (pct_rank_1y/3y/5y muy alto o bajo, |z-score|>2), momentum sostenido en los 3 snapshots |

| tendencia_madura | Dirección consistente en los 3 snapshots, pero el momentum (return_1m/3m) desacelera vs. hace 4-12 semanas |

| acumulacion | Posicionamiento institucional (m_money/swap percentile) subiendo desde base baja, mientras precio lateral/comprimido |

| distribucion | Posicionamiento institucional cayendo desde percentil alto, precio todavía no lo refleja |

| consolidacion | zscore de precio cerca de 0, variaciones cortas chicas, drivers estructurales sin sesgo claro |

| exhaustion | Percentil de posicionamiento en extremo (>90 o <10) + desaceleración de momentum |

| cambio_de_regimen | Los datos de hoy divergen de los snapshots w4/w12 en algo que antes era cierto y dejó de serlo |



Si elegís una fase, tenés que poder señalar en `evidence_keys` la(s) clave(s) \

concretas del payload que la sostienen. Esto es el ancla anti-alucinación.



## Eje B — Modificador de contexto (0, 1, o más — no son excluyentes)



- contexto_favorable: estructura y catalizadores apuntan en la misma dirección

- contexto_conflictivo: conflicto entre estructura y catalizador táctico reciente

- esperando_catalizadores: hay eventos de calendario de alto impacto próximos (ver calendar_upcoming) sin conflicto ni cambio reciente

- desacople_intermarket: una correlación clave está en zscore/percentil extremo (ver correlations, campo corr_252d_zscore_2y — extremo es |zscore| > ~1.5 o percentil <10/>90)

- contexto_turbulento: múltiples métricas simultáneamente extremas o conflictivas, no un solo punto



## Regla de convicción

ALTA si los 3 snapshots (current/w4/w12) muestran consistentemente la misma \

fase. MEDIA/BAJA si hay ambigüedad entre fases contiguas (ej. tendencia \

madura vs. exhaustion). Decilo explícitamente, no fuerces una sola etiqueta \

con confianza que no tenés.



## Reglas de anclaje de contenido por fase (traduccion_macro)

- Si fase = acumulacion: hablá de posicionamiento construyéndose \

  silenciosamente, NO de momentum de precio (en acumulación el precio \

  típicamente no se mueve todavía).

- Si fase = exhaustion: el foco tiene que estar en el desgaste del \

  combustible (percentiles extremos), más que en el nivel de precio.

- Si fase = cambio_de_regimen: contrastá explícitamente en el texto \

  "lo que era cierto hace 4-12 semanas" contra "lo que es cierto hoy" \

  — es la única fase donde este contraste debe ser explícito en el texto, \

  no solo interno.

El contenido tiene que estar anclado a los criterios que sostienen la fase \

elegida, no ser texto genérico con la fase pegada arriba.



## en_criollo

Explicación en lenguaje simple y llano, pensada para alguien SIN \

conocimientos de finanzas ni de mercados — no un trader, una persona \

común. Dos cosas prohibidas por igual:

1. Nada de jerga de trading ni lunfardo forzado ("largar los trapos", \

   "los gordos de las mesas", etc.) — no es un chat entre traders.

2. Nada de tecnicismos disfrazados — no menciones zscore, percentil, \

   SMA, DGS10, ni cites las cifras crudas. Traducí todo a causa y \

   efecto: "los grandes inversores se retiraron", "el oro bajó porque \

   subieron las tasas de interés en EEUU", "hay poca gente operando, \

   así que el movimiento es débil aunque el precio caiga".

El objetivo es que alguien sin ninguna formación financiera entienda \

QUÉ pasó y POR QUÉ, sin necesitar traducir la jerga ni la estadística. \

NUNCA introduce información nueva que no esté en traduccion_macro — es \

el mismo contenido, en otro registro.



## Hipótesis (4 componentes — la distinción presente/futuro es la clave)

- sostiene / debilita: PRESENTE. Ya está pasando, no alcanza para invalidar.

- invalidaria / podria_acelerarla: FUTURO. Todavía no pasó, pero rompería \

  o reforzaría la tesis si pasa.

No mezcles los tiempos verbales — esa distinción es la que le da humildad \

intelectual real al motor, no cosmética.



## Paranoia — cada pregunta necesita evidencia concreta del payload, no reflexión libre

- que_estoy_ignorando: una métrica que EXISTE en el payload pero no mencionaste en traduccion_macro

- que_me_hace_ruido: una métrica cuyo signo/magnitud es inconsistente con el resto (el outlier del propio payload)

- que_metrica_no_termina_de_cerrar: un dato con confianza baja EN SÍ MISMO (poca historia, sample_size chico en correlaciones, calendar sin zscore_6 por falta de profundidad histórica) — no una duda filosófica

- que_podria_cambiar_la_opinion: la MISMA condición de 'invalidaria', reformulada de cara a la semana próxima. No es una idea nueva.

- variable_a_vigilar: UNA sola, explícita. No una lista.

- contexto_limpio: true SOLO si genuinamente no hay nada real que objetar. \

  Es preferible decir "contexto inusualmente limpio" que inventar una \

  objeción floja para llenar el campo.



## Disciplina de citado — evidence_keys (regla anti-alucinación reforzada)

`evidence_keys` NO es un resumen de 4 datos lindos: tiene que incluir \

UNA clave por cada fuente de datos que menciones en `traduccion_macro` \

o `en_criollo`. Si hablás de posicionamiento COT, tiene que haber al \

menos una clave `cot.*`. Si citás una correlación, tiene que haber una \

clave `correlations.*`. Si citás ETF Flows (BTC), tiene que haber al \

menos una clave `etf_flows.*`. Si citás geopolítica o política comercial \

(military_conflict, sanctions, trade_war, energy_policy), tiene que haber \

al menos una clave `geopolitics.*`. Si el texto menciona una fuente que no está \

en `evidence_keys`, eso es un error de consistencia — revisá antes de \

responder que cada dato citado en el texto tenga su clave correspondiente.



## Uso de trayectoria, no solo foto actual

El payload trae 3 horizontes (current/w4/w12) para pricing, cot y cada \

serie de fred — úsalos. No describas un percentil o zscore como dato \

estático ("el Open Interest está en percentil 5") sin decir si viene \

subiendo, bajando, o estable en las últimas 4-12 semanas. La dirección \

del cambio suele ser más informativa que el nivel puntual.



## Categorías COT — usalas ambas, con roles distintos

El payload trae `cot.primary_field_prefix` (Managed Money — posicionamiento \

especulativo) y `cot.secondary_field_prefix` (Swap — dealers/bancos, \

posicionamiento de intermediación). Son roles distintos, no redundantes: \

mencioná ambas cuando hables de COT, no solo la primaria. dealer_net, \

asset_mgr_net y lev_money_net vienen SIEMPRE null para este activo — no \

es un hueco del análisis, es porque esas categorías pertenecen al dataset \

TFF (equity/bonos/FX) y este activo usa el dataset disaggregated. No lo \

menciones como limitación de tu análisis.



## ETF Flows — rol institucional equivalente a COT (solo BTC)



El payload de BTC trae `etf_flows.TOTAL` con los mismos 3 horizontes \

que el resto de las fuentes (`current`/`w4`/`w12`), cada uno con: \

`flow`, `rolling_sum_5d`, `rolling_sum_20d`, `sma_5`, `sma_20`, \

`trend_5v20`, `pct_rank_1y`, `pct_rank_since_inception`, `zscore`, \

`streak_days`, `cumulative_flow_ytd`, `cumulative_flow_since_inception` \

y `regime`. Para BTC esto ocupa el mismo rol que COT ocupa para GOLD \

y el resto de los activos institucionales — no es una fuente \

secundaria ni un dato accesorio, tratalo con el mismo peso que le \

das a COT en otros activos.

ETF Flows NO es una señal de compra/venta ni un proxy de momentum de \

precio. Mide participación institucional real — el flujo de entrada \

o salida en los ETFs spot de BTC —, independiente de hacia dónde vaya \

el precio. Un flujo positivo NO implica BTC alcista; un flujo \

negativo NO implica BTC bajista. Nunca lo uses para inferir \

dirección de precio ni objetivos futuros por sí solo — su función \

es fortalecer, debilitar o poner en conflicto la hipótesis que ya \

estás construyendo con el resto del payload (precio, COT, macro), \

no predecir.

Cómo leer cada campo:



- `regime` (acumulacion/distribucion/neutral): el estado institucional actual, ya clasificado a partir de rolling_sum_20d y trend_5v20 — no lo recalcules, usalo como el titular de esta fuente.

- `streak_days`: persistencia del flujo (positivo = racha de días de entrada, negativo = racha de salida, magnitud = cuántos días seguidos). Una racha larga pesa más como evidencia que un solo día extremo aislado.

- `trend_5v20` (sma_5 menos sma_20): si el flujo reciente se está acelerando o desacelerando respecto al último mes — importa la dirección del cambio, no solo el nivel del día.

- `pct_rank_1y`, `pct_rank_since_inception` y `zscore`: si el flujo de hoy es estadísticamente normal o extraordinario contra su propia historia — usalos para calibrar si algo amerita mención o es ruido de todos los días.

- `rolling_sum_20d` da contexto táctico (últimas ~4 semanas); `cumulative_flow_ytd` y `cumulative_flow_since_inception` dan contexto estructural (¿la adopción institucional es un fenómeno reciente o de todo el ciclo del producto?).



Usalo para leer trayectoria, no una foto fija: compará `current` \

contra `w4` y `w12` igual que hacés con pricing/COT/FRED — un \

regime que se sostiene en los 3 horizontes pesa distinto que uno \

que recién cambió hoy.

Cómo pesa sobre la hipótesis (Sección Hipótesis y Paranoia):



- ETF Flows fortaleciéndose (regime=acumulacion, streak positivo, trend_5v20>0) alineado con el resto del payload → fortalece la hipótesis vigente; subí convicción si corresponde.

- ETF Flows debilitándose o en regime=distribucion mientras precio o macro sostienen la hipótesis contraria → esto es evidencia de "debilita" en Hipótesis; si el conflicto es agudo, puede justificar el modificador `contexto_conflictivo` (Eje B).

- Precio debilitándose + ETF Flows fortaleciéndose (o viceversa) → desacople entre precio y participación institucional; puede alimentar `desacople_intermarket` si es marcado, o quedar como tensión a nombrar en `paranoia.que_me_hace_ruido`.

- ETF positivos persistentes + contexto macro favorable → fortalecimiento genuino de una hipótesis alcista. ETF negativos persistentes + deterioro macro → fortalecimiento genuino de una hipótesis bajista. En ambos casos es la ALINEACIÓN entre fuentes la que fortalece, no el ETF Flow aislado.



IMPORTANTE: el modelo no debe inferir objetivos de precio ni \

direccionalidad futura únicamente a partir de ETF Flows — es \

contexto institucional, no pronóstico.



## Geopolítica y política comercial — GDELT, Federal Register, MOFCOM (contexto, no señal)

El payload trae `geopolitics`: una lista (puede estar vacía) de eventos \

geopolíticos o de política comercial/energética relevantes para ESTE \

activo en la ventana reciente. El Data Layer ya decidió qué activos \

reciben qué categorías — si un evento aparece en el payload de un \

activo, es porque ya se determinó que es relevante para él; vos no \

tenés que evaluar esa relevancia de nuevo, solo interpretarla.

Cada evento trae: `source` (GDELT, FEDREG o MOFCOM), `category` \

(military_conflict, sanctions, trade_war o energy_policy), `role` \

(por qué es relevante para ESTE activo puntual: safe_haven, risk_asset, \

trade_exposed o supply_exposed — usalo para explicar el vínculo sin \

inventar razonamiento propio), `narrative` (descripción ya redactada \

del evento dominante de esa categoría/día) y `actor1` (siempre presente).

IMPORTANTE — `avg_goldstein`, `max_abs_goldstein`, `avg_tone`, \

`total_mentions` y `actor2` son EXCLUSIVOS de `source: GDELT` — GDELT \

agrega cobertura noticiosa masiva y puede promediar severidad/tono \

sobre cientos de artículos; FEDREG y MOFCOM son documentos oficiales \

puntuales, sin ese agregado, así que esos 5 campos SIEMPRE vienen en \

`null` para `source: FEDREG` y `source: MOFCOM` — eso es esperado, no \

un dato faltante. Nunca inventes un valor de severidad o tono para un \

evento de FEDREG/MOFCOM, ni asumas que "0" o neutral — simplemente no \

existe esa dimensión para esas fuentes. Su peso como evidencia sale de \

la `narrative` y la `category` en sí (ya vienen pre-filtradas por \

relevancia en el Data Layer, no es ruido), no de un score.

Para `avg_goldstein`/`max_abs_goldstein` (solo GDELT, escala -10/+10: \

negativo = conflicto/tensión, positivo = cooperación/distensión, \

cuanto más lejos de 0 más extremo) y `avg_tone` (tono de la cobertura \

mediática, negativo = hostil): usalos para calibrar cuánto peso darle \

a un evento de GDELT puntual — un evento con |avg_goldstein| bajo es \

ruido menor aunque aparezca en el payload, uno extremo amerita más \

atención.

Mismo rol que COT, FRED o ETF Flows — NUNCA estas fuentes: \

1. Nunca determinan por sí solas la fase del ciclo (Eje A) ni la \

   dirección del precio — no forman parte de la tabla de criterios \

   de fase, y no deben usarse para justificar `evidence_keys` de fase \

   sin una métrica de pricing/COT/FRED que la acompañe.

2. Nunca son el argumento central de `hipotesis.sostiene` o \

   `hipotesis.debilita` en soledad — tienen que aparecer junto a, o \

   modulando, evidencia de precio/posicionamiento/macro ya presente. \

   Son evidencia adicional, no el driver.

Cómo SÍ pesan (mismo criterio de alineación que ETF Flows):

- Narrativa geopolítica alineada con la estructura ya presente en el \

  payload (ej. escalada militar + GOLD ya en contexto_favorable \

  alcista por posicionamiento/macro) → fortalece la hipótesis vigente; \

  subí convicción si corresponde, citando también la evidencia de \

  precio/COT/FRED que ya la sostenía, no el evento solo.

- Evento que contradice la estructura ya presente (ej. sanciones \

  nuevas mientras precio/COT sostienen una lectura calma) → evidencia \

  de `hipotesis.debilita`; si el conflicto es agudo (severidad alta \

  + contradicción clara), puede justificar `contexto_conflictivo` (Eje B).

- Varios eventos de distintas categorías el mismo período, o un \

  evento de severidad extrema simultáneo con otras métricas ya \

  extremas del payload → puede sumar a `contexto_turbulento` (Eje B), \

  nunca a una categoría nueva.

- `geopolitics: []` es el resultado normal para la mayoría de los \

  activos la mayoría de los días — NO es una limitación de datos, no \

  lo menciones en `paranoia.que_metrica_no_termina_de_cerrar` ni como \

  hueco del análisis.

Diferencia con Calendar: Calendar son publicaciones económicas \

programadas con agenda conocida (CPI, NFP, FOMC). Geopolitics son \

eventos discretos sin agenda previa (conflictos, sanciones, medidas \

comerciales/energéticas oficiales). No son intercambiables ni se \

reemplazan entre sí — pueden coexistir el mismo día sin ser \

redundantes.

IMPORTANTE: nunca infieras magnitud de impacto de mercado ni \

objetivos de precio a partir de un evento geopolítico aislado — \

`avg_goldstein`/`avg_tone` te dicen si la evidencia es seria o menor, \

no te dan una predicción.

Regla de cobertura obligatoria: si `geopolitics` no viene vacío, no \

podés dejarlo pasar en silencio — tenés que evaluarlo explícitamente, \

por dos caminos posibles: (1) lo integrás a `hipotesis`/`traduccion_macro` \

si es relevante para el escenario de este activo, con su `evidence_keys` \

correspondiente, o (2) si tras evaluarlo concluís que no cambia nada \

relevante, decilo en `paranoia.que_estoy_ignorando` citando la categoría. \

Lo que NO es válido es que `geopolitics` tenga eventos y no aparezca \

mencionado NI descartado en ningún campo del output — eso es tan grave \

como inventar un dato. Regla de severidad — aplica IGUAL a las 4 categorías, no depende de \
tener un score numérico: para GDELT, `avg_goldstein` por debajo de -7 o \
por encima de +7 (o `max_abs_goldstein` >= 8) marca magnitud alta. Para \
FEDREG y MOFCOM, que no traen ese score, la magnitud alta es la \
condición por DEFAULT de cualquier evento que aparezca en el payload — \
ya fueron pre-filtrados por relevancia en el Data Layer antes de \
llegar a vos, no existe un "evento menor" de estas dos fuentes en este \
dataset. No interpretes la ausencia de `avg_goldstein` como ausencia \
de severidad — es ausencia de esa dimensión particular, nada más. Con \
esa aclaración: el default para las 4 categorías (`military_conflict`, \
`sanctions`, `trade_war`, `energy_policy`) es integrarlas activamente a \
la hipótesis, no relegarlas a paranoia, salvo que tengas una razón \
concreta para considerar esa categoría puntual irrelevante para este \
activo (ej. el evento es real pero su `role` no conecta con nada del \
resto del payload esta semana).
Si `geopolitics` trae MÁS DE UNA categoría (puede pasar — DXY, por \
ejemplo, puede tener `military_conflict`, `sanctions`, `trade_war` y \
`energy_policy` a la vez), la regla de cobertura aplica A CADA \
CATEGORÍA POR SEPARADO, no al conjunto como si fuera un solo bloque, \
y en igualdad de condiciones entre sí — ninguna categoría ni ninguna \
fuente (GDELT, FEDREG, MOFCOM) es por default más prioritaria que las \
demás. Evaluá las categorías en el orden en que aparecen en el \
payload, no elijas por cuál empezar según cuál te resulte más \
noticiosa. Cubrir una categoría no te exime de pronunciarte también \
sobre las demás — cada una necesita su propia cita \
`geopolitics.<category>` o su propio descarte explícito.

Estas dos reglas van siempre juntas, no son intercambiables: si \

mencionás geopolítica en `traduccion_macro`/`en_criollo`/`frase_puente` \

pero no le sumaste su clave `geopolitics.*` en `evidence_keys`, cumpliste \

la regla de cobertura pero rompiste la de disciplina de citado — y eso \

sigue siendo un error de consistencia, exactamente igual que si lo \

hicieras con COT o FRED.

Formato exacto de la clave (no uses `\"geopolitics\"` sola, no es válida): \

`geopolitics.<category>`, donde `<category>` es el valor exacto del \

campo `category` del evento que estás citando — `geopolitics.sanctions`, \

`geopolitics.military_conflict`, `geopolitics.trade_war` o \

`geopolitics.energy_policy`. Si citás dos categorías distintas, van dos \

claves separadas en la lista.

Este chequeo es un cruce INTERNO entre lo que escribiste y lo que \

pusiste en `evidence_keys` — nunca lo escribas dentro de la prosa. \

`frase_puente`, `traduccion_macro` y `en_criollo` son texto narrativo, \

no anotaciones: NUNCA insertes un evidence_key ni ningún path técnico \

entre paréntesis (ni de ninguna otra forma) dentro de esos tres campos \

— ejemplos de lo que NO tiene que aparecer ahí: `(pricing.current.zscore_1d)`, \

`(geopolitics)`, `(cot.snapshots.current.m_money_percentile_5y)`. Los \

paths técnicos existen ÚNICAMENTE en el array `evidence_keys`.



## Correlaciones — nunca un número suelto

Cuando cites una correlación (`correlations.*`), acompañala SIEMPRE con \

su `corr_252d_zscore_2y` o `corr_252d_percentile_2y` — decir "correlación \

de -0.61 con DXY" sin contexto no dice si eso es normal o extremo para \

ese par. El zscore/percentil es lo que la vuelve informativa.





Te paso la hipótesis de la corrida anterior (si existe). Clasificá su estado:

- vigente_sin_cambios: nada relevante se movió

- vigente_fortalecida: lo que estaba en 'podria_acelerarla' ocurrió parcialmente

- vigente_debilitada: lo que estaba en 'debilita' se profundizó, sin cruzar invalidación

- invalidada: se cruzó explícitamente una condición de 'invalidaria' — citala

- no_aplica_primera_corrida: si no te paso hipótesis anterior



También decime si la 'variable_a_vigilar' de la corrida anterior aparece \

mencionada en tu traduccion_macro de hoy (true/false). Si es la primera \

corrida (no te pasé hipótesis anterior), poné false — el campo es \

obligatorio y no acepta ausencia de valor.



## Elasticidad del output (Sección 7 — NO es tu criterio libre)

Te paso flags ya calculados (cambio_fase, cambio_modificador, \

conflicto_detectado, conviccion_bajo, sorpresa_calendario). Si NINGUNO \

está activo: traduccion_macro puede ser legítimamente corta (2-3 líneas, \

"sin cambios, sigue vigente la misma lectura"), paranoia con \

contexto_limpio=true si aplica. Si alguno está activo, expandí en \

proporción a cuántos y cuáles se activaron.



## Reglas generales

- Español rioplatense, directo, sin relleno corporativo.

- Todo dato que cites tiene que existir en el payload — no inventes \

  cifras ni eventos.

- Si el payload no tiene suficiente profundidad histórica para algo \

  (sample_size bajo, zscore null), decilo como limitación, no lo \

  disimules.


## Antes de responder — verificación final

Releé `geopolitics` en el payload: contá cuántas categorías distintas \

aparecen. Contá cuántas tienen su `geopolitics.<category>` en \

evidence_keys O su descarte explícito (podés poner más de una en el \

mismo campo de paranoia, no hace falta que sea una sola por campo). \

Si el número de categorías presentes no coincide con el número \

cubierto, todavía te falta una — volvé y resolvela antes de dar la \

respuesta final.

"""





def _get_client() -> genai.Client:

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()

    if not api_key:

        raise EnvironmentError("GEMINI_API_KEY debe estar seteada como variable de entorno.")

    return genai.Client(api_key=api_key)





def _build_user_prompt(payload: dict, previous_hypothesis: dict | None, elasticity_flags: dict | None) -> str:

    parts = [

        f"# Payload de {payload.get('display_name')} — as_of {payload.get('as_of')}",

        "```json",

        json.dumps(payload, indent=2, default=str, ensure_ascii=False),

        "```",

    ]



    if previous_hypothesis:

        parts += [

            "\n# Hipótesis de la corrida anterior (para Sección 6 — Memoria)",

            "```json",

            json.dumps(previous_hypothesis, indent=2, default=str, ensure_ascii=False),

            "```",

        ]

    else:

        parts.append("\n# No hay hipótesis anterior — es la primera corrida para este activo.")



    if elasticity_flags:

        parts += [

            "\n# Flags de elasticidad ya calculados (Sección 7 — no los recalcules, son la fuente de verdad sobre cuánto expandir)",

            "```json",

            json.dumps(elasticity_flags, indent=2, default=str, ensure_ascii=False),

            "```",

        ]

    else:

        parts.append("\n# No se pasaron flags de elasticidad — asumí que corresponde expandir (primera corrida o corrida manual).")



    return "\n".join(parts)





def _warn_if_geopolitics_uncovered(payload: dict, result: AssetTranslatorOutput) -> None:
    """
    No corrige nada (mismo criterio que weekly_engine._warn_if_leaked_nomenclature
    — reescribir el output generado por el modelo es más riesgoso que dejarlo
    pasar y loguear) — solo deja constancia visible en los logs de la corrida
    si el payload trae eventos geopolíticos de magnitud alta y el output no
    cumplió la regla de cobertura obligatoria del SYSTEM_PROMPT (integrarlo o
    descartarlo explícitamente en paranoia), o si el texto lo menciona sin la
    evidence_key correspondiente (regla de disciplina de citado).

    Chequeo POR CATEGORÍA, no agregado: con un solo activo pueden convivir
    hasta 4 categorías (military_conflict, sanctions, trade_war,
    energy_policy) en el mismo payload (ej. DXY). Si el chequeo fuera
    "¿hay AL MENOS UNA cita geopolitics.*?", cubrir una sola categoría
    alcanzaría para tapar que las otras 3 quedaron completamente afuera
    del output — exactamente el caso real que se detectó con DXY citando
    military_conflict/sanctions y omitiendo trade_war/energy_policy sin
    ningún aviso. Cada categoría de magnitud alta se evalúa por separado.

    Umbral de "magnitud alta": |avg_goldstein| >= 7 o max_abs_goldstein >= 8
    (GDELT) — mismo umbral que ya usa el propio SYSTEM_PROMPT. Toda fila de
    FEDREG/MOFCOM cuenta siempre: no traen goldstein (no son eventos
    noticiosos puntuables), pero ya vienen pre-filtradas por categoría en el
    Data Layer — no son ruido.
    """
    events = payload.get("geopolitics") or []
    if not events:
        return

    def _is_high_severity(ev: dict) -> bool:
        if ev.get("source") in ("FEDREG", "MOFCOM"):
            return True
        goldstein = ev.get("avg_goldstein")
        max_abs = ev.get("max_abs_goldstein")
        return (goldstein is not None and abs(goldstein) >= 7) or (
            max_abs is not None and max_abs >= 8
        )

    high_severity_categories = sorted(
        {ev.get("category") for ev in events if _is_high_severity(ev)}
    )
    if not high_severity_categories:
        return

    cited_categories = {
        k.split(".", 1)[1]
        for k in result.estado.evidence_keys
        if k.startswith("geopolitics.") and "." in k
    }

    missing_categories = [c for c in high_severity_categories if c not in cited_categories]
    if not missing_categories:
        return  # cada categoría severa tiene su propia cita — cumplió

    text_fields = " ".join(
        f or ""
        for f in [
            result.frase_puente,
            result.traduccion_macro,
            result.en_criollo,
            result.paranoia.que_estoy_ignorando,
            result.paranoia.que_me_hace_ruido,
        ]
    ).lower()
    mentioned_in_text = any(
        kw in text_fields
        for kw in (
            "geopolít",
            "geopolit",
            "gdelt",
            "mofcom",
            "federal register",
            "sancion",
            "conflicto armado",
            "arancel",
            "comercial",
            "energét",
        )
    )

    if mentioned_in_text:
        print(
            f"[engine] ADVERTENCIA: geopolitics trae categoría(s) de magnitud alta sin su "
            f"evidence_key correspondiente: {', '.join(missing_categories)} — el texto "
            f"menciona geopolítica pero no queda claro que cubra específicamente estas "
            f"categorías (regla de disciplina de citado incumplida)."
        )
    else:
        print(
            f"[engine] ADVERTENCIA: geopolitics trae categoría(s) de magnitud alta que no "
            f"aparecen citadas NI mencionadas/descartadas en ningún campo del output: "
            f"{', '.join(missing_categories)} — regla de cobertura obligatoria incumplida."
        )


def run_engine(

    payload: dict,

    previous_hypothesis: dict | None = None,

    elasticity_flags: dict | None = None,

) -> AssetTranslatorOutput:

    """

    Punto de entrada único del Paso 2.



    payload:             dict devuelto por data_contract.build_payload()

    previous_hypothesis: dict con hipothesis + paranoia de la corrida anterior, o None

    elasticity_flags:    dict devuelto por elasticity.py (Paso 3), o None

    """

    client = _get_client()

    user_prompt = _build_user_prompt(payload, previous_hypothesis, elasticity_flags)



    response = _generate_content_with_retry(

        client,

        model=MODEL_NAME,

        contents=user_prompt,

        config=types.GenerateContentConfig(

            system_instruction=SYSTEM_PROMPT,

            response_mime_type="application/json",

            response_schema=AssetTranslatorOutput,

            temperature=0.2,

        ),

    )



    result: AssetTranslatorOutput = response.parsed

    if result is None:

        raise ValueError(f"Gemini no devolvió un objeto parseable. Respuesta cruda: {response.text[:2000]}")

    _warn_if_geopolitics_uncovered(payload, result)

    return result





if __name__ == "__main__":

    import sys

    from data_contract import build_payload

    from persistence import get_previous_hypothesis



    asset_key = sys.argv[1] if len(sys.argv) > 1 else "GOLD"

    payload = build_payload(asset_key)

    previous = get_previous_hypothesis(asset_key)

    output = run_engine(payload, previous_hypothesis=previous)

    print(output.model_dump_json(indent=2))
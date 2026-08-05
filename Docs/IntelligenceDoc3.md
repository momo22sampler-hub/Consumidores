# Sentinel Intelligence Layer V2 — Documento de Implementación

## 1. Propósito y alcance

Este documento traduce el **Sentinel Market State Model** (Documento 2) en reglas concretas de implementación sobre el sistema actualmente en producción (`engine.py`, `output_schema.py`, `elasticity.py`, `persistence.py`, cron de GitHub Actions).

Regla de relación con el Documento 2: **este documento nunca contradice al Documento 2, solo lo operacionaliza.** Si en algún punto la implementación necesita algo que el Documento 2 no contempla, la corrección se hace primero en el Documento 2, y recién después se refleja acá. Este documento sí puede quedar desactualizado por decisiones de ingeniería (versión de modelo, proveedor de LLM, esquema de base de datos); el Documento 2 no.

## 2. Inventario del sistema actual (as-is)

- **`engine.py`** — `run_engine(payload, previous_hypothesis, elasticity_flags)`, único punto de entrada. Llama a Gemini (`gemini-flash-lite-latest`) con `response_schema=AssetTranslatorOutput`. Un prompt de sistema único decide fase, modificadores, convicción, hipótesis, paranoia y narrativa en una sola inferencia (más una segunda pasada solo para calibrar extensión de texto, no la decisión de estado).
- **`output_schema.py`** — `AssetTranslatorOutput`, con `EstadoActivo` (fase/modificadores/conviccion/evidence_keys), `Hipotesis` (4 componentes), `Paranoia` (5 preguntas), `Memoria` (comparación contra la corrida anterior).
- **`elasticity.py`** — `compute_elasticity_flags()`, sin LLM. Corre **después** de la corrida preliminar, comparando `today_estado` contra `previous_state`. Contiene el único precedente actual de validación determinística: `_sorpresa_calendario()`.
- **`persistence.py`** — patrón de 2 llamadas (preliminar sin flags → flags → final con flags). Upsert por `(asset_key, as_of)` en `asset_history`, `hypotheses_history`, `asset_outputs`.
- **Cron** (`daily_engine.yml`) — corre una vez por activo por día hábil (BTC además fin de semana), después de que termina toda la extracción del Data Layer.

## 3. Mapa de evidencia: Data Layer → Jerarquía del Documento 2 (§9-10)

Este mapa es el artefacto que hace que la jerarquía de evidencia del Documento 2 exista en la arquitectura, no solo en el prompt. Debe vivir como estructura de datos versionada (no como texto libre dentro del prompt), consultable por las validaciones determinísticas de la Sección 6.

| Tier (Doc. 2) | Prefijo de payload | Fuente |
|---|---|---|
| 9.1 — Precio y Tiempo (obligatoria) | `pricing.*` | Scraper |
| 9.2 — Derivada de precio (estructural, apoyo) | `pricing.*.sma_*`, `pricing.*.zscore_*` | Scraper |
| 9.3 — Posicionamiento institucional (estructural, apoyo elevado) | `cot.*` (mayoría de activos) / `etf_flows.*` (BTC) | COT / ETF_Flows |
| 9.4 — Contextual | `fred.*`, `correlations.*`, `calendar_recent.*`, `calendar_upcoming.*`, `geopolitics.*`, `sentiment.*` | FredExtractor / Scraper / Calendario / GDELT-Policy_watch / Sentiment |

Regla de implementación: cada activo declara en `data_contract.py` cuál es su fuente de 9.3 (`cot` o `etf_flows`) — nunca ambas, nunca ninguna ambigüedad sobre cuál aplica.

## 4. Cambios al schema de salida (`output_schema.py`)

### 4.1 Separar estado y modificador en tipos que no puedan confundirse

```python
# Antes: FaseCiclo incluye "exhaustion" — puede colisionar con Eje B
# Después:

EstadoMercado = Literal[
    "equilibrio",
    "acumulacion",
    "distribucion",
    "price_discovery_alcista",
    "price_discovery_bajista",
    "reacumulacion_redistribucion",
]

EstadoProvisionalHacia = Literal[
    "acumulacion", "distribucion",
    "price_discovery_alcista", "price_discovery_bajista",
    "equilibrio",
] | None  # None = no hay Estado Provisional abierto

ModificadorContexto = Literal[
    "exhaustion_alcista",
    "exhaustion_bajista",
    "contexto_conflictivo",
    "esperando_catalizadores",
    "desacople_intermarket",
]
```

`exhaustion` deja de ser un valor posible de `EstadoMercado` a nivel de tipo — no por instrucción de prompt, sino porque el schema no lo acepta. `contexto_favorable` y `contexto_turbulento` (valores actuales sin equivalente en el catálogo §12 del Documento 2) quedan **fuera** de esta versión: si se necesitan, es una discusión de Documento 2 antes de volver a agregarlos, no una decisión de implementación.

### 4.2 Ajustar `Memoria` para que la inconsistencia se pueda detectar, no solo reportar

`Memoria.estado_hipotesis_previa` y `Memoria.explicacion` ya existen y ya piden que, si `invalidada`, se cite la condición de `invalidaria` que se cruzó (Documento 2 §14). Lo que falta es que esto no dependa de que el LLM lo autoreporte bien — ver Sección 6.2.

### 4.3 Corrección Aug 2026 (hallada en smoke test real, activo GOLD): `Paranoia` necesita ancla verificable, no texto libre

La primera corrida real contra Gemini (sin mock) mostró que `que_metrica_no_termina_de_cerrar` citó "encuestas de sentimiento del consumidor" — una fuente que no existe en absoluto en el Data Layer. Nada en el schema forzaba que una observación de Paranoia estuviera atada a un dato real, así que el modelo pudo inventar una fuente plausible sin que ningún tipo se lo impidiera.

Las 3 preguntas de Paranoia que citan datos concretos (`que_estoy_ignorando`, `que_me_hace_ruido`, `que_metrica_no_termina_de_cerrar`) pasan de `str` a un objeto `EvidenciaCitada = {observacion: str, evidence_key: str}`. Esto por sí solo no alcanza — un `evidence_key` puede tener el tier correcto y seguir sin existir de verdad en el payload de esa corrida. El chequeo de tier (`assert_only_structural`, §6.2) valida *dónde debería vivir* la evidencia; hace falta un chequeo adicional que valide que *vive ahí de verdad*.

Se agrega `evidence_map.assert_evidence_keys_exist(evidence_keys, payload)`, que navega el payload real con la misma clave punteada que citó el modelo y falla si no la encuentra. A diferencia de `assert_only_structural` (que exige jerarquía de tier), esta validación es agnóstica de tier — se usa contra el payload que corresponda en cada caso (ver §6.4).

## 5. Arquitectura de llamadas

Se reemplaza la única llamada actual (más la pasada de calibración de extensión) por una etapa determinística previa y dos llamadas, con un corte que coincide exactamente con el límite §9.1-9.3 / §9.4 del Documento 2 — no con "precio vs. resto", según la corrección acordada.

**Etapa 0 — Construcción del conjunto de estados candidatos (determinística, sin LLM).**

El Documento 2 define una máquina de estados (§5), no un clasificador libre. `engine_estado.compute_candidate_states()` calcula, antes de invocar al modelo, el conjunto de estados válidos para hoy — usando `estado_previo`, `estado_provisional_previo`, `invalidacion_confirmada` (§6.1) y `ruptura_precio_detectada` contra la matriz de transiciones del Documento 2 §7 (implementada en `engine_estado.TRANSITION_MATRIX`). Reglas de construcción:

- Sin Estado Provisional abierto y sin invalidación confirmada: el único candidato es `estado_previo` — salvo ruptura de precio detectada, en cuyo caso se agregan los destinos permitidos por la fila de `estado_previo` en la matriz.
- Con Estado Provisional abierto: los únicos candidatos son confirmarlo o volver a `estado_previo`.
- Con invalidación confirmada: se habilitan los estados alcanzables desde `estado_previo` según la matriz, siempre vía Estado Provisional.
- Primera corrida (`estado_previo=None`, bootstrap, Documento 3 §7.1): candidatos = catálogo completo, sin restricción — es la única situación en la que la Llamada 1 ve las 6 opciones.

**Llamada 1 — Estado y Convicción.**
Entrada: payload filtrado a 9.1 + 9.2 + 9.3 (`payload_filter.build_structural_payload()`), estado guardado de la corrida anterior, y el conjunto de candidatos de la Etapa 0 — el prompt describe únicamente esas opciones, nunca el catálogo completo salvo en bootstrap.
Decide: `estado`, `estado_provisional_hacia` (si aplica), `conviccion`, `evidence_keys` (solo de 9.1-9.3).
No tiene en su output schema ningún campo de modificador, hipótesis o narrativa — estructuralmente no puede escribir eso.

**Llamada 2 — Contexto, Modificadores, Hipótesis y Narrativa.**
Entrada: payload completo (incluye 9.4), `estado`/`conviccion` ya fijados por la Llamada 1 como dato de solo lectura, y `memoria_previa` (la hipótesis/paranoia real de la corrida anterior, o `None` en bootstrap — corrección Aug 2026, ver §6.5).
Decide: `modificadores` (catálogo §12), `hipotesis`, `paranoia`, `memoria`, `frase_puente`, `traduccion_macro`, `en_criollo`.
Su output schema no incluye el campo `estado` — no puede reescribirlo aunque quisiera.

Esto es un cambio de estructura de llamadas, no un cambio de costo relevante frente al patrón de 2 llamadas que ya existe hoy en `persistence.py`.

## 6. Validaciones determinísticas (sin LLM)

Extienden el patrón que ya existe en `elasticity._sorpresa_calendario()`.

### 6.1 Pre-check (insumo de la Etapa 0): ¿se cumplió la condición de invalidación?

Antes de construir el conjunto de candidatos, `precheck.check_invalidacion_confirmada()` lee `invalidaria_check` de la hipótesis guardada ayer y evalúa, contra el payload de hoy, si esa condición se cumplió. El resultado (`bool | None`) es uno de los insumos de `compute_candidate_states()` (Sección 5).

Cuando `invalidaria_check` es `None` (condición cualitativa, no expresable numéricamente), el resultado también es `None`, y `compute_candidate_states()` lo trata igual que `False` — sin confirmación determinística, no se abre el catálogo completo.

### 6.2 Aserción de respaldo: matriz de transiciones (§7)

Si la Etapa 0 está bien implementada, el LLM de la Llamada 1 es estructuralmente incapaz de devolver una transición fuera de la matriz — nunca la tuvo como opción. `_assert_transition_integrity()` (en `engine.py`) valida esto igual, pero como aserción de integridad: si se dispara, indica un bug en `compute_candidate_states()` o en el prompt, no una mala decisión del modelo (`TransitionIntegrityError`).

### 6.3 Post-check: coherencia Memoria/Estado

`_assert_memoria_estado_coherence()`. Si `Memoria.estado_hipotesis_previa == "vigente_sin_cambios"` pero el estado cambió respecto de ayer, la salida es inconsistente por definición (Documento 2 §14) y se rechaza (`MemoriaEstadoInconsistencyError`). Corre también en bootstrap, con una rama específica: si es la primera corrida (sin hipótesis previa real) y `estado_hipotesis_previa` no es exactamente `"no_aplica_primera_corrida"`, también se rechaza — este caso se agregó tras el smoke test real (ver 6.5).

### 6.4 Post-check: existencia real de las evidence_keys citadas

Corrección Aug 2026, hallada en el primer smoke test real contra Gemini (sin mock, activo GOLD). `assert_only_structural()` (Sección 4.1) valida que una `evidence_key` tenga el *tier* correcto — nunca valida que esa clave *exista de verdad* en el payload de la corrida. El smoke test mostró el hueco de forma concreta: el modelo citó una fuente ("encuestas de sentimiento del consumidor") que no existe en absoluto en el Data Layer, con un tier plausible pero sin ningún dato real detrás.

`evidence_map.assert_evidence_keys_exist(evidence_keys, payload)` navega el payload real con la clave punteada citada por el modelo y falla si no la encuentra — agnóstico de tier, se usa contra el payload que corresponda en cada punto de la cadena:

| Qué se valida | Contra qué payload | Excepción si falla |
|---|---|---|
| `EstadoActivo.evidence_keys` (Llamada 1) | `structural_payload` (lo único que la Llamada 1 vio) | `EstadoEvidenceError` |
| `Paranoia.*.evidence_key` (3 de 5 preguntas, Llamada 2) | payload completo | `ParanoiaEvidenceError` |
| `Hipotesis.invalidaria_check.evidence_key` (Llamada 2, si no es `None`) | payload completo | `HipotesisEvidenceError` |

Las tres cierran el mismo bug de origen. Las dos últimas se agregaron en la misma sesión que encontró el problema; la primera (`EstadoActivo`) quedó pendiente en el checklist post-smoke-test y se cerró en un pase posterior — mismo patrón, mismo remedio, sin razón para tratarlo distinto solo porque salió de la Llamada 1 en vez de la Llamada 2.

### 6.5 Corrección Aug 2026: `memoria_previa` real, no inferida

El mismo smoke test mostró que la Llamada 2 no recibía la hipótesis previa en absoluto, así que no tenía con qué llenar `Memoria` (Documento 2 §14) de forma genuina — en una corrida bootstrap (sin ninguna hipótesis previa real) alucinó `estado_hipotesis_previa="vigente_sin_cambios"`, y el post-check 6.3 tampoco corría en bootstrap, así que no lo atrapó.

Se corrige pasando `memoria_previa` explícito al payload de la Llamada 2 (`payload_filter.build_contextual_call_payload()`): `None` en bootstrap, o el dict real de `persistence.get_previous_hypothesis()` en corridas normales. El prompt de la Llamada 2 exige que, si `memoria_previa` es `None`, `estado_hipotesis_previa` sea exactamente `"no_aplica_primera_corrida"` — nunca inferido. El post-check 6.3 ahora corre siempre, incluido bootstrap, con la rama específica descrita en 6.3.

### 6.6 Corrección Aug 2026: prohibición de presentar un proxy como el dato real

La misma corrida mostró a la narrativa hablando de "tasas reales" pese a que `config.py` documenta explícitamente que no existe la serie de tasas reales (DFII10/TIPS) en el Data Layer — el modelo estaba combinando tasa nominal (DGS10) + inflación (PCEPILFE) y presentando el resultado con el nombre del dato que en realidad no tiene. Se agrega regla explícita en el prompt de la Llamada 2: nunca nombrar un concepto de mercado que el payload no tiene una serie real para sostener, incluso si el resultado narrativo es casi equivalente. Además, `data_contract.build_payload()` ahora incluye `known_limitations` por activo (restricciones reales del Data Layer que el modelo debe respetar al pie de la letra en toda la narrativa) — campo nuevo, no existía en la versión anterior de este documento.

## 7. Migración de datos existentes

El historial ya guardado en `asset_history` y `hypotheses_history` **no se relabelea retroactivamente** — es el registro real de lo que el sistema dijo en su momento, y tiene valor como evidencia del comportamiento V1 (de hecho, ya lo usamos como caso de estudio). Se agrega un campo `schema_version` a las tablas para que el Dashboard y el harness (Sección 8) puedan distinguir filas generadas bajo V1 de filas generadas bajo el Sentinel Market State Model. La migración SQL concreta está en `migration_v2_schema.sql`: agrega columnas nuevas (`estado`, `estado_provisional_hacia`, `evidence_map_version`, `schema_version`, `invalidaria_check`) sin tocar ni renombrar las columnas viejas — `fase` sigue existiendo tal cual para que las filas `schema_version=1` se puedan seguir leyendo.

### 7.1 Bootstrap: la primera corrida de cada activo bajo el motor nuevo

`compute_candidate_states()` (Sección 5) necesita un `estado_previo` válido del catálogo §6 del Documento 2. La última fila real de cada activo tiene `fase` con un valor que puede no existir en ese catálogo (`exhaustion`, `cambio_de_regimen`, `tendencia_madura`, `consolidacion`, `price_discovery` sin dirección). No se infiere ese mapeo de forma automática — sería inventar continuidad que no está respaldada por evidencia (violaría Documento 2 §9, "Sentinel no infiere datos que no existen").

En cambio, la primera corrida de cada activo bajo `schema_version=2` se trata explícitamente como **primera corrida** (`estado_provisional_previo=None`, `invalidacion_confirmada=None`, sin `estado_previo` de referencia): el motor evalúa el payload actual desde cero contra los 6 estados del catálogo, sin el sesgo de continuidad de la fila V1 anterior. Esto es más lento que asumir continuidad, pero es la única opción consistente con que el modelo "no asume, no completa con conocimiento externo" (Documento 2 §7). A partir de esa primera corrida `schema_version=2`, la máquina de estados funciona con normalidad.

## 8. Harness de validación histórica

Antes de reemplazar `engine.py` en producción: reprocesar offline (sin persistir) los payloads reales de GOLD del período jul-ago 2026 contra el motor nuevo, y confirmar dos cosas — (a) que el número de cambios de `estado` en el período baja frente a los 5 cambios reales observados, dado que el COT nunca cruzó el umbral de invalidación, y (b) que ninguna transición fuera de la matriz §7 ocurre. Este caso queda como test de regresión obligatorio, no opcional, antes de cualquier despliegue.

**Corrida real (`harness_gold_real.py`, ver repo):** `pricing_metrics` (symbol `GC`) y `cot_metrics` (symbol `GOLD`, `disagg_fut`) son point-in-time — una fila real por fecha, no un snapshot que se pisa — así que el harness corrió contra datos reales de Supabase, no reconstruidos de memoria. 14 sesiones hábiles (15/07 al 01/08), COT real uniéndose por "última fila on-or-before" (misma lógica que `data_contract._latest_row_on_or_before()`). Resultado: **un único candidato (`acumulacion`) los 14 días** — el COT real nunca bajó de percentil 58.8, lejos del umbral de invalidación (50). Ambos criterios (a) y (b) quedan satisfechos con evidencia real, no sintética.

**Alcance de esta corrida — lo que prueba y lo que no.** Esto valida la Etapa 0 (`compute_candidate_states` + `precheck`), que es la pieza que efectivamente impide el flip-flop de fases — con un solo candidato disponible, la Llamada 1 no tiene margen para inventar una transición aunque quisiera. No incluye una llamada real a Gemini para las 14 fechas (eso consume cuota de API real y requiere `GEMINI_API_KEY`, que no está disponible en este entorno) — sigue pendiente correr el pipeline completo (Etapa 0 + Llamada 1 + Llamada 2) fecha por fecha para confirmar que el modelo, ya restringido a un solo candidato, efectivamente lo devuelve sin fricción.

## 9. Fuera de alcance de esta versión

- Grado estructural multi-timeframe real (Documento 2 §8): con datos diarios únicamente, el "grado" se aproxima con ventanas de distinta longitud sobre la misma serie, no con timeframes intradiarios. Queda para una iteración posterior.
- Procesamiento de volumen/Volume Profile: no existe en el Data Layer actual: no forma parte de esta versión (Documento 2 §9.5 ya contempla esto).
- Adaptación de `weekly_engine.py`: hereda las mismas reglas de este documento, pero su cadencia y su rol (repaso semanal vs. corrida diaria) requieren su propia sección — se aborda en una revisión posterior de este mismo documento, no en esta versión.

## 10. Orden de trabajo sugerido

1. ✅ Cambios de schema (Sección 4).
2. ✅ Mapa de evidencia como estructura de datos versionada (Sección 3).
3. ~~Validaciones determinísticas en modo "solo log"~~ — **decisión tomada:** se saltó este paso. El smoke test real contra Gemini (`smoke_test_gold.py`, sin persistencia) vino sirviendo como esa validación informal y encontró más bugs reales de los que un modo "solo log" hubiera encontrado a esta altura (Secciones 6.4-6.6). No se agrega el modo log retroactivo — se va directo al harness histórico (paso 4) como medición más seria.
4. 🟡 **Harness histórico (Sección 8) sobre el caso GOLD — corrido contra datos reales de Supabase, Etapa 0 solamente.** `harness_gold_real.py` confirma con 14 días reales que la Etapa 0 reduce el espacio de decisión a un único candidato todo el período. Falta correr el pipeline completo (con Llamada 1 y Llamada 2 reales) fecha por fecha — requiere gastar cuota real de `GEMINI_API_KEY`, así que es una decisión tuya cuándo pagarla, no un bloqueo técnico.
5. ✅ Separación en dos llamadas + Etapa 0 (Sección 5).
6. 🟡 Activar bloqueo real de las validaciones determinísticas — parcialmente hecho: 6.1-6.6 ya bloquean de verdad en `smoke_test_gold.py` (no hay modo "solo log" activo). Falta confirmar el schema real de `pricing_metrics` y `cot_metrics` en el Data Layer (solo se confirmó `fred_metrics` durante el debug de la corrida pasada) — si el modelo cita un campo de `pricing.*`/`cot.*` que tampoco existe, hoy pasa sin que 6.4 lo note porque nadie confirmó que el nombre "lógico" del campo coincide con el real.
7. ⬜ Corte de versión y despliegue — `daily_engine.yml` sigue sin apuntar al motor V2 en producción.
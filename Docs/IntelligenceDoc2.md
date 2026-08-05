# Sentinel Market State Model

## 1. Propósito

Este documento define el modelo conceptual mediante el cual Sentinel interpreta el estado de un mercado.

Constituye la especificación funcional completa del Intelligence Layer y es independiente de cualquier modelo de inteligencia artificial.

Toda implementación presente o futura deberá respetar las reglas aquí definidas. Ninguna implementación puede introducir un comportamiento que contradiga este documento; si lo necesita, primero debe modificarse la Constitución.

## 2. Filosofía

Sentinel no intenta predecir el mercado.

Sentinel interpreta el estado actual del proceso de subasta.

Las hipótesis representan escenarios condicionales derivados del estado actual y no constituyen predicciones.

El Intelligence Layer describe el contexto estructural del mercado utilizando evidencias objetivas provenientes del Data Layer, interpretadas bajo los principios de Auction Market Theory y Wyckoff.

## 3. Modelo del mercado

Un mercado es un proceso dinámico de búsqueda continua de equilibrio.

Cuando compradores y vendedores consideran aceptable un determinado nivel de precios, el mercado entra en equilibrio.

Cuando ese equilibrio deja de ser aceptado aparece un desequilibrio, que impulsa una nueva exploración de precios.

Ese proceso continúa hasta alcanzar un nuevo equilibrio o iniciar una nueva expansión.

El valor de un activo no es un dato fijo. Se define mediante la interacción de Precio y Tiempo: el precio es la herramienta de descubrimiento, y el tiempo que el mercado permanece en un nivel es lo que confirma si ese nivel es aceptado como valor. El tiempo no se mide como una variable aparte: se devenga implícitamente a partir del propio comportamiento del precio (persistencia de cierres en una zona, rotación entre extremos, ausencia de expansión direccional).

El objeto de Sentinel no es medir únicamente la dirección del precio, sino identificar en qué etapa de ese proceso se encuentra actualmente el mercado.

## 4. Estado del mercado

El estado representa la interpretación estructural vigente del proceso de subasta.

Debe responder únicamente a la evidencia acumulada disponible.

Mientras no exista evidencia suficiente para invalidarlo, el estado permanece vigente. La continuidad es el comportamiento esperado; el cambio es la excepción que requiere confirmación.

## 5. Máquina de estados

Los estados representan configuraciones persistentes del proceso de subasta. El catálogo formal se define en la Sección 6.

Las transiciones entre estados no son libres. Cada transición requiere evidencia compatible con la teoría y debe respetar continuidad lógica. No todas las transiciones posibles son válidas, y no todas poseen la misma probabilidad. La matriz de transiciones permitidas se define en la Sección 7.

### 5.1 Estado Provisional (Transición)

Ninguna transición se declara de forma inmediata a partir de una ruptura de nivel. Toda ruptura estructural relevante abre, en cambio, un **Estado Provisional**: una hipótesis de transición en proceso de validación.

- El Estado Provisional no reemplaza al estado vigente; convive con él como hipótesis dominante mientras se espera confirmación.
- Se resuelve en una de dos direcciones:
  - **Confirmación:** el precio sostiene la nueva zona, sin reingresar a la zona previa durante el período de validación. El estado vigente transiciona formalmente al estado destino indicado en la matriz de la Sección 7.
  - **Invalidación:** el precio reingresa a la zona previa (sacudida / falsa ruptura). El estado vigente se mantiene, y el episodio pasa a formar parte de la evidencia acumulada de ese estado.
- Mientras un Estado Provisional está activo, la convicción del estado vigente se reduce, pero no se anula.
- El criterio operativo de confirmación (cuántas sesiones, qué comportamiento de precio constituye "sostener la zona") queda a criterio de calibración por activo, pero el mecanismo en sí — ruptura → provisional → confirmación o invalidación — es obligatorio y no configurable.

Este mecanismo es el que impide que una ruptura, por sí sola, produzca un cambio de estado — la ruptura es evidencia de una hipótesis, no evidencia de un hecho consumado. Ningún estado del catálogo de la Sección 6 puede transicionar a otro sin pasar por un Estado Provisional, sin excepción.

### 5.2 Fases de Wyckoff como sub-secuencia, no como estado

Las fases de Wyckoff (A, B, C, D, E) no son estados de Sentinel. Son una sub-secuencia de evidencia interna que puede usarse, dentro de un Estado, como criterio de entrada, permanencia y salida — por ejemplo, para reconocer que una Acumulación está madurando o que se aproxima a su resolución. El estado de Sentinel es el nivel de abstracción que el sistema comunica; la fase de Wyckoff es evidencia interna que sostiene esa lectura.

## 6. Catálogo de Estados

Este catálogo reemplaza y cierra lo que iba a ser el "Documento 2.1": es la enumeración formal y definitiva de los estados válidos de Sentinel. Ningún estado fuera de este catálogo es válido. Ninguna implementación puede agregar un estado nuevo sin modificar esta sección.

Cada estado se define con: evidencia de entrada obligatoria (Precio y Tiempo, §9.1), evidencia de apoyo cuando exista (§9.2/9.3), criterio de permanencia, y qué evidencia abre un Estado Provisional hacia otro estado.

### 6.1 Equilibrio / Rango

**Definición:** El precio y el valor coinciden. No hay sesgo direccional identificable todavía entre compradores y vendedores dentro del rango.

**Entrada (obligatoria):** Compresión de rango (barras de rango decreciente respecto al tramo direccional previo), rotación entre extremos sin dirección neta sostenida, ausencia de una racha de cierres consecutivos hacia un extremo.

**Apoyo:** Posicionamiento (COT) sin sesgo sostenido — el percentil de Managed Money no muestra tendencia clara al comparar snapshot actual contra ventanas de 4 y 12 semanas.

**Permanencia:** Mientras el precio siga rotando dentro del rango, sin ruptura sostenida.

**Abre Estado Provisional cuando:** el precio rompe el rango (arriba o abajo) con cierre fuera del área.

### 6.2 Acumulación

**Definición:** Caso particular de rango donde la evidencia de precio muestra agotamiento de una tendencia bajista previa, y la evidencia de apoyo (COT) muestra manos fuertes incrementando posición de forma sostenida pese al precio deprimido.

**Entrada (obligatoria):** Precio en zona baja relativa (distancia negativa relevante respecto a una referencia de mediano plazo, p. ej. media móvil de tendencia), señales de pérdida de momentum bajista (rango de barras decreciente, mechas inferiores, *shortening of the thrust*).

**Apoyo (jerarquía elevada, §10):** Percentil de posicionamiento neto de Managed Money en ascenso sostenido en varias semanas (comparando snapshot actual vs. w4 vs. w12), incluso si el precio no acompaña todavía. Esta divergencia — precio deprimido + posicionamiento institucional en ascenso — es la firma característica del estado.

**Permanencia:** Mientras el COT no capitule (caída sostenida por debajo de un umbral de referencia, p. ej. percentil 50) y el precio no rompa el mínimo del rango con cierre sostenido. La presión de evidencia contextual (tasas, DXY, calendario) **no** es, por sí sola, motivo de salida — ver caso de referencia más abajo.

**Abre Estado Provisional cuando:** el precio rompe al alza el techo del rango (posible JAC) → provisional hacia Price Discovery Alcista. O rompe a la baja el piso del rango con cierre sostenido → provisional de invalidación (posible reclasificación tardía como Distribución si el COT también gira).

**Caso de referencia (GOLD, jul-ago 2026):** durante 12 sesiones el posicionamiento de Managed Money se mantuvo firme entre percentil 68 y 70, sin cruzar nunca el umbral de invalidación (percentil 50). Bajo este modelo, el estado debería haberse mantenido en Acumulación (o, como máximo, en Estado Provisional) durante todo el período. La implementación actual, en cambio, relabeleó la fase cinco veces (`acumulacion` → `exhaustion` → `consolidacion` → `tendencia_madura` → `exhaustion`) sin que la evidencia estructural definida como determinante se moviera. Este es el comportamiento que este documento existe para eliminar.

### 6.3 Distribución

**Definición:** Análogo simétrico de Acumulación. Rango donde la evidencia de precio muestra agotamiento de una tendencia alcista previa, y el posicionamiento institucional muestra reducción sostenida pese a que el precio se mantiene firme o eufórico.

**Entrada (obligatoria):** Precio en zona alta relativa, señales de pérdida de momentum alcista (rango decreciente, mechas superiores).

**Apoyo:** Percentil de Managed Money en descenso sostenido en varias semanas, con precio todavía firme — la divergencia bajista simétrica a 6.2.

**Permanencia y condiciones de salida:** simétricas a 6.2, invirtiendo la dirección.

### 6.4 Price Discovery Alcista (Markup)

**Definición:** Desequilibrio vertical con dirección alcista confirmada, típicamente resultado de una Acumulación o Reacumulación resuelta.

**Entrada (obligatoria):** Barras de rango amplio, cierres consistentemente cerca de máximos, bajo consumo de tiempo por nivel de precio (rotación mínima), ausencia de reingreso a la zona previa.

**Apoyo:** El COT puede seguir sosteniendo la posición o empezar a tomar ganancias parcialmente sin que esto invalide el estado por sí solo.

**Permanencia:** Mientras continúe el patrón de cierres direccionales, sin evidencia de Exhaustion (§12) ni ruptura de la propia estructura del movimiento.

**Abre Estado Provisional cuando:** aparece Exhaustion (pérdida de velocidad, *shortening of the thrust*, mechas de rechazo en máximos) seguida de lateralización — abre la pregunta de si deriva en Reacumulación (pausa, continúa la tendencia) o en Distribución (techo de grado mayor).

### 6.5 Price Discovery Bajista (Markdown)

**Definición:** Análogo simétrico de 6.4, dirección bajista.

### 6.6 Reacumulación

**Definición:** Pausa lateral dentro de un **Price Discovery Alcista** ya activo. Estructuralmente idéntica a un rango de Acumulación (6.2) en cuanto a evidencia de precio, pero el contexto es de continuación de tendencia alcista, no de inicio de ciclo. La diferencia con Acumulación no es de evidencia de precio: es de grado estructural mayor — qué estado estaba vigente inmediatamente antes de la pausa.

**Entrada (obligatoria):** Compresión de rango (barras de rango decreciente, rotación sin dirección neta) ocurriendo después de un Price Discovery Alcista (6.4) ya confirmado. Sin ese antecedente directo de PD Alcista, el estado no puede ser Reacumulación — es, como máximo, Acumulación.

**Apoyo:** El COT puede mostrar reducción parcial de posición larga (toma de ganancias institucional) sin que eso invalide el estado por sí solo — es esperable que las manos fuertes aligeren en las pausas.

**Permanencia:** Mientras el precio no rompa el mínimo del rango de pausa con cierre sostenido ni supere el máximo con expansión de barras.

**Salida:** Retoma del Price Discovery Alcista (caso más frecuente, vía EP) o falla de la continuación hacia Equilibrio (vía EP, si el precio rompe a la baja el piso de la pausa de forma sostenida). Nunca transiciona directamente a Distribución ni a Price Discovery Bajista — eso requeriría pasar por Equilibrio o PD Bajista primero.

### 6.7 Redistribución

**Definición:** Análogo simétrico de Reacumulación (6.6), dentro de un **Price Discovery Bajista** ya activo. Pausa lateral que la evidencia de precio y el contexto estructural sugieren como continuación de la tendencia bajista, no como su reversión.

**Entrada (obligatoria):** Compresión de rango ocurriendo después de un Price Discovery Bajista (6.5) ya confirmado. Sin ese antecedente directo, el estado no puede ser Redistribución — es Distribución.

**Apoyo:** El COT puede mostrar reducción parcial de posición corta (cobertura táctica) sin invalidar el estado.

**Permanencia y salida:** Simétrico a Reacumulación, invirtiendo la dirección. Retoma del Price Discovery Bajista (vía EP) o falla hacia Equilibrio (vía EP). Nunca transiciona directamente a Acumulación ni a Price Discovery Alcista.

## 7. Matriz de transiciones permitidas

Toda celda marcada "vía EP" requiere pasar por un Estado Provisional (§5.1) antes de confirmarse. Toda combinación no listada se considera **transición inválida** y no puede ocurrir en ninguna implementación.

| Desde \ Hacia | Equilibrio | Acumulación | Distribución | PD Alcista | PD Bajista | Reacumulación | Redistribución |
|---|---|---|---|---|---|---|---|
| Equilibrio | — | directo | directo | vía EP | vía EP | — | — |
| Acumulación | vía EP (invalidación) | — | inválida* | vía EP | inválida | — | — |
| Distribución | vía EP (invalidación) | inválida* | — | inválida | vía EP | — | — |
| PD Alcista | — | — | vía EP (techo mayor) | — | inválida | vía EP (pausa alcista) | — |
| PD Bajista | — | vía EP (piso mayor) | — | inválida | — | — | vía EP (pausa bajista) |
| Reacumulación | vía EP (falla) | — | — | vía EP (retoma) | inválida | — | — |
| Redistribución | vía EP (falla) | — | — | inválida | vía EP (retoma) | — | — |

*Acumulación → Distribución (o viceversa) directa es inválida bajo AMT/Wyckoff: no existe mecanismo teórico por el cual manos fuertes pasen de comprar a vender sin que medie una fase de expansión de precio. Si el COT gira de forma abrupta sin resolución direccional previa, el sistema lo reporta como invalidación hacia Equilibrio, nunca como transición directa.

**Separación de Reacumulación y Redistribución:** cada una solo es alcanzable desde el Price Discovery de su propia dirección. PD Alcista puede pausar en Reacumulación; PD Bajista puede pausar en Redistribución. No existe transición cruzada (PD Alcista → Redistribución o PD Bajista → Reacumulación) porque implicaría un cambio de dirección estructural que requiere pasar por Equilibrio o por el PD opuesto primero.

Toda transición que no pueda justificarse con una fila/columna de esta matriz debe rechazarse en el motor de razonamiento, independientemente de qué tan fuerte parezca la evidencia contextual que la sugiere.

## 8. Grado estructural

El mercado puede exhibir simultáneamente estructuras de distinto grado sobre la misma serie de datos (por ejemplo, un rango de corto plazo anidado dentro de un rango de mayor duración).

La ruptura de una estructura de grado menor no invalida por sí sola el estado vigente de grado mayor. Toda lectura de estado debe indicar explícitamente a qué grado estructural corresponde, y una transición de grado menor no se propaga automáticamente al grado mayor sin evidencia propia de ese grado.

Este principio es también lo que distingue a una Reacumulación (6.6) o Redistribución (6.7) de una Acumulación/Distribución de grado mayor (6.2/6.3): las primeras ocurren *dentro* de una estructura de grado superior ya identificada; las segundas definen ellas mismas el grado superior.

## 9. Evidencias

Toda interpretación debe construirse exclusivamente mediante evidencias disponibles en el Data Layer. Sentinel no asume, no completa con conocimiento externo y no infiere datos que no existen.

### 9.1 Evidencia de Precio y Tiempo (base, obligatoria)

Comportamiento de rango, cierres, mechas, rotación entre extremos y persistencia temporal en una zona. Es la única evidencia que el sistema debe considerar siempre disponible, y es suficiente por sí sola para determinar el estado. Toda lectura de estado debe poder sostenerse únicamente con esta evidencia.

### 9.2 Evidencia derivada de precio (estructural, de apoyo)

Medias móviles y demás métricas calculadas a partir de la serie de precio disponibles en el Data Layer. Refuerzan o matizan la lectura de tendencia, equilibrio o desequilibrio. Se tratan como estructurales porque se derivan directamente del mismo proceso de subasta, no de una fuente externa.

### 9.3 Evidencia de posicionamiento (COT) — estructural de apoyo, con rol elevado

El posicionamiento de los grandes operadores (COT) tiene un rol distinto al resto de la evidencia contextual: en Wyckoff, la acumulación y la distribución son, por definición, procesos protagonizados por manos fuertes. El COT es la evidencia más directa que el Data Layer puede ofrecer sobre ese comportamiento, y por lo tanto puede reforzar significativamente una lectura de Acumulación o Distribución que ya esté sostenida por evidencia de Precio y Tiempo.

El COT no sustituye a la evidencia de Precio y Tiempo ni puede, por sí solo, abrir un Estado Provisional. Su función es elevar o reducir la convicción de una lectura ya presente en el precio, y ayudar a distinguir una acumulación genuina de una lateralización sin causa.

### 9.4 Evidencia contextual (Correlaciones, FRED, Sentiment, Calendario Económico, Geopolítica)

Enriquecen la interpretación del estado vigente. Pueden anticipar un catalizador de desequilibrio o explicar por qué el mercado se comporta de determinada manera, pero ninguna, por sí sola, provoca una transición de estado ni abre un Estado Provisional.

### 9.5 Sobre evidencia no disponible

Cuando una fuente de evidencia no está disponible para un activo determinado (por ejemplo, volumen transaccional, que no existe para todos los instrumentos), el sistema no debe tratarla como ausente-por-lo-tanto-negativa ni como condición bloqueante. Simplemente no participa del razonamiento para ese activo. El modelo debe ser capaz de determinar el estado completo usando únicamente 9.1, incluso cuando 9.2, 9.3 o 9.4 no estén disponibles.

## 10. Jerarquía de evidencias

Cuando existan conflictos entre fuentes, prevalece la evidencia de mayor jerarquía: 9.1 sobre 9.2 y 9.3, y estas sobre 9.4.

Ninguna evidencia aislada, sin importar su jerarquía, debe dominar la interpretación completa del mercado. Una lectura sostenida por una sola fuente, por más estructural que sea, tiene menor convicción que una lectura sostenida por múltiples fuentes coherentes entre sí.

## 11. Persistencia

Un estado permanece activo hasta que exista evidencia suficiente para invalidarlo, según los criterios de salida definidos para ese estado en la Sección 6. La ausencia de esa evidencia específica implica continuidad, no incertidumbre — la presión de evidencia contextual (tasas, sentimiento, geopolítica) no constituye, por sí sola, esa evidencia.

El motor debe favorecer la estabilidad frente al ruido de corto plazo. Toda transición, incluida la resolución de un Estado Provisional, debe poder justificarse mediante un conjunto coherente de evidencias — nunca mediante un único evento aislado, y nunca mediante evidencia contextual actuando sola.

## 12. Modificadores

Los modificadores no representan estados independientes ni fases, y **no pueden aparecer como valor del campo de estado**. Representan condiciones adicionales que alteran la interpretación del estado principal vigente. Nunca lo reemplazan, y deben poder retirarse cuando la evidencia que los sostenía deja de estar vigente.

Catálogo de modificadores válidos:

- **Exhaustion (alcista / bajista):** pérdida de momentum de uno de los dos lados dentro de un estado vigente (Price Discovery, Acumulación o Distribución). Es la evidencia que puede abrir un Estado Provisional, pero no es en sí misma un destino. Un mercado no "está en Exhaustion": un Price Discovery Bajista "muestra Exhaustion".
- **Contexto Conflictivo:** la evidencia contextual (9.4) apunta en sentido contrario a la evidencia estructural (9.1/9.2/9.3) que sostiene el estado vigente. Se declara explícitamente en la narrativa; no cambia el estado ni reduce la convicción por sí solo más allá de reflejar la tensión existente.
- **Esperando Catalizadores:** existen eventos de calendario relevantes pendientes de publicarse. Incrementa la exigencia de confirmación antes de resolver cualquier Estado Provisional abierto, pero no abre ni cierra estados por sí mismo.
- **Desacople Intermarket:** una correlación históricamente estable entre el activo y otra serie (p. ej. oro vs. VIX) se rompe. Es evidencia contextual (9.4): puede señalarse, pero nunca dispara una transición por sí sola.

Cualquier concepto que no encaje en este catálogo y que la implementación necesite expresar como "estado intermedio" (por ejemplo, lo que la implementación actual llama `cambio_de_regimen`) debe modelarse como Estado Provisional (§5.1), no como una fase ni como un modificador nuevo sin definir aquí.

## 13. Convicción

La convicción expresa el grado de consistencia entre las evidencias disponibles, ponderadas según la jerarquía definida en la Sección 10.

No mide la probabilidad de que una hipótesis ocurra. Mide la solidez de la interpretación actual del estado del mercado.

La convicción se reduce mientras existe un Estado Provisional activo, y se recompone cuando este se resuelve — al alza si hay confirmación, o de vuelta a su nivel previo si hay invalidación.

## 14. Hipótesis

Las hipótesis representan los escenarios coherentes con el estado vigente.

Toda hipótesis es condicional. Debe indicar explícitamente bajo qué condiciones continúa siendo válida y qué evidencia provocaría su invalidación.

La condición de invalidación de la hipótesis y la condición de salida del estado vigente (Sección 6) deben ser la misma evidencia. Si una hipótesis dice "vigente sin cambios" y el estado cambió en el mismo ciclo, hay una contradicción que el sistema debe poder detectar y rechazar antes de publicar el resultado.

## 15. Narrativa

La narrativa comunica el resultado del razonamiento. No introduce nuevas conclusiones. Debe ser completamente consistente con el estado, la convicción y las hipótesis.

## 16. Explicación pedagógica

La explicación pedagógica traduce el razonamiento técnico a un lenguaje accesible. No modifica la interpretación ni simplifica la lógica del modelo. Su función es hacer comprensible la decisión adoptada.

## 17. Principios operativos

- El mercado se interpreta como un proceso continuo de subasta entre equilibrio y desequilibrio.
- El tiempo se devenga a partir del comportamiento del precio; no es una variable independiente.
- El estado tiene prioridad sobre la dirección del precio.
- Solo existen los estados enumerados en la Sección 6. Ninguna implementación puede introducir un estado nuevo sin modificar este documento.
- Ninguna ruptura constituye una transición por sí sola: toda ruptura relevante abre un Estado Provisional sujeto a confirmación o invalidación (§5.1).
- Solo son válidas las transiciones listadas en la matriz de la Sección 7. Una transición no listada debe rechazarse aunque la evidencia contextual la sugiera con fuerza.
- Las fases de Wyckoff son evidencia interna de un Estado, no estados en sí mismas.
- Los modificadores nunca aparecen como valor del campo de estado (§12). Exhaustion no es una fase.
- Una estructura de grado menor no invalida por sí sola un estado de grado mayor.
- La evidencia de Precio y Tiempo es suficiente, por sí sola, para determinar el estado; el resto de las fuentes refuerzan o matizan, nunca sustituyen.
- El COT tiene un rol estructural de apoyo elevado para distinguir acumulación/distribución genuina de lateralización sin causa.
- Ninguna evidencia contextual (correlaciones, FRED, sentiment, calendario, geopolítica) modifica por sí sola un estado estructural.
- La ausencia de una fuente de evidencia para un activo determinado no bloquea el razonamiento; el modelo debe funcionar con la evidencia mínima disponible.
- La condición de invalidación de una hipótesis y la condición de salida del estado deben coincidir; una hipótesis "vigente sin cambios" no puede convivir con un cambio de estado en el mismo ciclo.
- La hipótesis deriva del estado; la narrativa deriva de la hipótesis. Nunca al revés.
- La estabilidad prevalece sobre la sensibilidad al ruido.
- Toda transición debe ser explicable y auditable.
- El Intelligence Layer interpreta; el Data Layer observa; el Dashboard comunica.

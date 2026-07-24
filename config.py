"""

config.py — Asset Translator configuration

Intelligence Layer / Arkad Tools



Define el universo de activos soportados y, por activo, qué subconjunto

de correlaciones y series FRED entran al payload. Curado a propósito:

meter TODAS las correlaciones/series disponibles en el payload sería

ruido, no señal (ver doc fuente, filosofía de "jerarquía de entrada").



DISEÑO CONGELADO — 14 activos:

  GOLD, SILVER, CRUDE_OIL, NASDAQ, SP500, DXY (ronda 1)

  COPPER, RUSSELL2000, EURUSD, GBPUSD, USDJPY, USDCAD, USDCHF, AUDUSD (ronda 2)

Cualquier métrica nueva debe pasar el filtro: "¿esto le permite al Motor

explicar una narrativa macro importante que hoy no podría explicar?".

Si la respuesta no es un sí claro, no se agrega.



FUERA DE ASSET_CONFIGS a propósito (no son un "activo con motor propio"):

  - DAX, HSI, NKY, VIX, VXN: sin datos de COT en Data Layer (índices no

    reportados al CFTC, o índices de volatilidad sin futuro reportado).

    Se usan únicamente como correlation_pairs dentro de otros activos —

    con eso alcanza, el dato de pricing/correlación es sólido (~5.5 años

    de historia, matriz de pricing_correlations completa). No se les

    arma asset_key propio: además de faltar COT, no son activos que se

    vayan a operar, así que no se justifica rediseñar el motor para un

    caso "sin COT".

  - BTC, ETH: en standby. Hoy no hay COT para BTC en Data Layer (aunque

    sí existe en CME/CFTC real — no está cargado acá). Si en el trabajo

    de enriquecimiento de este fin de semana se suma COT para alguno de

    los dos, ahí sí pasan a ser candidatos reales a asset_key propio —

    hasta entonces, se usan como correlation_pair nomás (ya lo hace

    AUDUSD, por ejemplo).

  - NATGAS (NG): tiene pricing + COT completos (dataset disagg_fut), pero

    se descartó a propósito. Su driver dominante es clima/inventarios

    semanales (EIA) — nada de eso está en Data Layer, y ninguna fuente

    disponible (FRED, correlaciones, calendario) lo compensa como sí pasa

    con CRUDE_OIL. Un texto "macro-formado" sobre NG con este payload

    explicaría poco de su varianza real. Revisar si en el futuro se suma

    una fuente de clima/inventarios; hasta entonces, afuera.

  - DGS10/DGS2/DGS5/US02Y/US10Y (rates/yields): anclas macro dentro de

    otros activos (fred_series/correlation_pairs), nunca asset_key propio

    — decisión explícita del usuario, no una limitación de datos.

  - ES1!, NQ1!: mismo family_code que ES/NQ (is_primary=false en

    symbol_registry) — son el mismo instrumento en otro formato, no un

    activo nuevo.

"""



# Offsets de snapshot histórico, en días calendario, usados para la

# comparación de 3 horizontes (Regla 1 / convicción, Sección 6 memoria).

SNAPSHOT_OFFSETS_DAYS = {

    "current": 0,

    "w4": 28,

    "w12": 84,

}



ASSET_CONFIGS = {

    "GOLD": {

        "display_name": "Oro (GC)",



        # --- identificadores reales por tabla (confirmados con el usuario) ---

        "pricing_symbol": "GC",             # pricing_metrics.symbol (¡distinto a cot_metrics!)

        "cot_symbol": "GOLD",               # cot_metrics.symbol

        "cot_dataset_type": "disagg_fut",  # cot_metrics.dataset_type

        "correlation_symbol": "GC",        # pricing_correlations.symbol_a/b



        # --- posicionamiento institucional (decisión del usuario) ---

        # Managed Money = métrica principal. Asset Managers no aplica a

        # disagg_fut (esa categoría es de TFF); dejamos "swap" como

        # complementaria de disagg_fut ya que es la categoría más cercana

        # a posicionamiento "institucional" fuera de m_money en ese dataset.

        "cot_primary_field_prefix": "m_money",

        "cot_secondary_field_prefix": "swap",



        # --- correlaciones curadas (símbolo del otro lado del par) ---

        "correlation_pairs": ["DXY", "DGS10", "DGS2", "VIX", "SI", "USDJPY"],



        # --- series FRED curadas ---

        # WALCL agregado: aísla la acción directa del balance de la Fed

        # (QE/QT), narrativa de "debasement/liquidez" que M2SL (agregado

        # amplio, rezagado) y NFCI (índice compuesto) no capturan de forma

        # específica. Para Oro es el activo donde este pilar más pesa.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI", "PCEPILFE", "WALCL"],



        # LIMITACIÓN CONOCIDA: no existe DFII10 (ni ningún TIPS/real yield)

        # en fred_metrics del Data Layer (confirmado). DGS10 nominal +

        # PCEPILFE son un proxy imperfecto de tasas reales — no controlan

        # por expectativas de inflación de mercado. El motor NO debe

        # presentar esto como si tuviera visibilidad de tasas reales.



        # --- calendario ---

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,   # eventos ya ocurridos (Sección 1, Regla 2)

        "calendar_forward_days": 14,    # eventos upcoming (modificador "Esperando catalizadores")

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "SILVER": {

        "display_name": "Plata (SI)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "SI",

        "cot_symbol": "SILVER",

        "cot_dataset_type": "disagg_fut",  # mismo dataset que GOLD

        "correlation_symbol": "SI",



        # --- posicionamiento institucional ---

        # Mismo criterio que GOLD: SI es disagg_fut, misma estructura de

        # campos (m_money / swap / prod_merc), así que se reutiliza 1:1.

        "cot_primary_field_prefix": "m_money",

        "cot_secondary_field_prefix": "swap",



        # --- correlaciones curadas ---

        # USDJPY sacado: era herencia directa de GOLD sin justificación

        # propia para Plata (carry/funding en yenes no es una narrativa

        # específica de Plata; DXY ya cubre "dólar fuerte/débil" agregado).

        # HG (cobre) se mantiene: componente de demanda industrial que el

        # Oro no tiene (metal semi-monetario / semi-industrial).

        "correlation_pairs": ["GC", "DXY", "DGS10", "DGS2", "VIX", "HG"],



        # --- series FRED curadas ---

        # INDPRO agregado: sin esto, el lado "monetario" de Plata estaba

        # cubierto (tasas/liquidez/inflación) pero el lado "industrial"

        # no tenía ningún dato macro de respaldo — solo HG en

        # correlaciones, sin contexto de actividad real. Es la corrección

        # más importante del set: permite explicar underperformance de

        # Plata vs Oro por demanda industrial floja.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI", "PCEPILFE", "INDPRO"],



        # --- calendario (idéntico a GC) ---

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "CRUDE_OIL": {

        "display_name": "Petróleo WTI (CL)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "CL",

        "cot_symbol": "WTI",               # ojo: en cot_metrics el símbolo es "WTI", no "CL"

        "cot_dataset_type": "disagg_fut",

        "correlation_symbol": "CL",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "m_money",

        "cot_secondary_field_prefix": "swap",



        # --- correlaciones curadas ---

        # DXY (relación inversa clásica), yields (costo de oportunidad /

        # expectativas de crecimiento), ES (barómetro de risk-on/off),

        # USDCAD (petro-moneda, altísima correlación con WTI).

        # HG (cobre) evaluado y descartado: la narrativa de demanda

        # global ya la cubren INDPRO + BAMLH0A0HYM2 + ES; no hay lectura

        # de 3-4 párrafos sobre crudo que dependa de tener cobre además.

        "correlation_pairs": ["DXY", "DGS10", "DGS2", "VIX", "ES", "USDCAD"],



        # --- series FRED curadas ---

        # CL es el activo más "macro-dependiente" del set: toca las 4

        # categorías del registro (rates, inflación, liquidez, actividad)

        # en vez de curar solo 1-2 como el resto. Aun así, PAYEMS e ICSA

        # se sacaron en la revisión final: ambos son el mismo proxy

        # (mercado laboral de EEUU) duplicado, y la conexión con crudo es

        # indirecta (transmite vía tasas/dólar, ya cubiertos directo).

        # UMCSENT reemplazado por BAMLH0A0HYM2: spread de crédito HY es

        # de mercado (tiempo real) vs. encuesta mensual ruidosa — mejor

        # señal para la narrativa "miedo a recesión / destrucción de

        # demanda" que mueve crudo pese a recortes de OPEC+.

        "fred_series": [

            "DGS10", "DGS2", "T10Y2Y", "DFF",   # rates

            "M2SL", "NFCI",                      # liquidez

            "CPIAUCSL", "PCEPILFE",              # inflación (contexto de régimen, no driver)

            "INDPRO", "BAMLH0A0HYM2",            # actividad / crédito-riesgo

        ],



        # LIMITACIÓN CONOCIDA: BAMLH0A0HYM2 solo tiene historia desde

        # 2023-06-26 en fred_metrics (~3 años, no 5). Su campo pct_rank_5y

        # no es un percentil real de 5 años para esta serie — es, como

        # mucho, un percentil sobre la historia completa disponible

        # (mismo tipo de imprecisión que *_percentile_5y en COT, que es

        # expandido y no de ventana fija). El motor no debe presentar un

        # percentil de BAMLH0A0HYM2 como si tuviera 5 años de profundidad

        # real detrás.



        # NOTA DE INTERPRETACIÓN: CPIAUCSL/PCEPILFE deben leerse como

        # contexto de régimen ("cómo el propio crudo alimenta la

        # inflación que ve la Fed"), NUNCA como si la inflación fuera un

        # driver directo del precio del crudo — la causalidad real va al

        # revés (crudo → inflación, vía CPI de energía).



        # --- calendario ---

        # "Crude Oil Inventories" (EIA) está catalogado como High impact

        # en USD, así que el filtro estándar ya lo captura sin tocar nada.

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "NASDAQ": {

        "display_name": "Nasdaq 100 (NQ)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "NQ",

        "cot_symbol": "NQ",

        "cot_dataset_type": "tff_fut",     # distinto dataset a GOLD/SILVER/CL

        "correlation_symbol": "NQ",



        # --- posicionamiento institucional ---

        # tff_fut NO tiene m_money/swap poblados (confirmado en cot_metrics:

        # count=0). Las categorías reales son dealer/asset_mgr/lev_money/

        # other_rept. Leveraged Funds = dinero especulativo apalancado,

        # la señal táctica más relevante para un índice; Asset Managers

        # como secundaria (posicionamiento "real money"/institucional).

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # Foco en yields (sensibilidad a duración de growth stocks), DXY,

        # VIX y VXN (volatilidad específica de Nasdaq), y ES como ancla

        # de mercado amplio. PAYEMS evaluado como ancla de correlación/serie

        # y descartado: la transmisión de nóminas a NQ pasa casi

        # enteramente por tasas, que ya están cubiertas — agregarlo

        # duplica la misma causa, no agrega una narrativa nueva.

        "correlation_pairs": ["ES", "DXY", "DGS10", "DGS2", "VIX", "VXN"],



        # --- series FRED curadas ---

        # Sesgo fuerte a rates/liquidez (yields reales, condiciones

        # financieras, balance de la Fed) por sobre inflación/actividad,

        # que es donde NQ es más sensible. DFF sacado en la revisión

        # final: redundante con SOFR (casi el mismo número salvo eventos

        # puntuales de estrés de funding, que SOFR ya capta mejor).

        # BAMLH0A0HYM2 agregado: comparte el pilar de crédito con

        # SP500/CRUDE_OIL pero por una razón propia — apetito de riesgo

        # crediticio anticipa el estrés en growth stocks antes que el

        # equity mismo se mueva.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "SOFR", "M2SL", "NFCI", "WALCL", "BAMLH0A0HYM2"],



        # LIMITACIÓN CONOCIDA: BAMLH0A0HYM2 solo tiene historia desde

        # 2023-06-26 en fred_metrics (~3 años, no 5) — su pct_rank_5y no

        # refleja una ventana real de 5 años (ver detalle en CRUDE_OIL).



        # --- calendario ---

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "SP500": {

        "display_name": "S&P 500 (ES)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "ES",

        "cot_symbol": "ES",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "ES",



        # --- posicionamiento institucional ---

        # Misma lógica que NQ (mismo dataset tff_fut).

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # Comparte casi toda la lógica de NQ (NQ, DXY, yields, VIX), pero

        # cambia VXN por RUT: ES es mercado amplio, así que la breadth

        # contra small caps (Russell 2000) es más relevante que la vol

        # específica de tech. HG evaluado y descartado: no hay una

        # narrativa establecida "cobre-S&P500" comparable a "cobre-crudo";

        # sería agregar sin explicar nada nuevo.

        "correlation_pairs": ["NQ", "DXY", "DGS10", "DGS2", "VIX", "RUT"],



        # --- series FRED curadas ---

        # A diferencia de NQ (rates/liquidez puro), ES está más expuesto

        # a la economía real y amplia (empleo, consumo), no solo a

        # duración/growth. ICSA sacado en la revisión final: redundante

        # con PAYEMS (mismo proxy de mercado laboral duplicado) — PAYEMS

        # es el dato más citado y central para "cómo está la economía".

        # UMCSENT reemplazado por INDPRO: dato duro (producción industrial)

        # en vez de encuesta de sentimiento, mismo pilar de "economía

        # real" con mejor calidad de señal. BAMLH0A0HYM2 agregado: mismo

        # pilar de crédito que NQ/CL, aquí como barómetro de estrés

        # financiero amplio antes de que golpee al equity.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI", "PAYEMS", "INDPRO", "BAMLH0A0HYM2"],



        # LIMITACIÓN CONOCIDA: BAMLH0A0HYM2 solo tiene historia desde

        # 2023-06-26 en fred_metrics (~3 años, no 5) — su pct_rank_5y no

        # refleja una ventana real de 5 años (ver detalle en CRUDE_OIL).



        # --- calendario ---

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "DXY": {

        "display_name": "Índice Dólar (DXY)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "DXY",

        "cot_symbol": "DXY",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "DXY",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # DXY es el activo más interconectado del dashboard: se le permite

        # una lista más ancha que al resto (8 vs. 6-7) a propósito. Los 5

        # pares FX son literalmente los componentes de la canasta (EUR

        # pesa ~57%, JPY ~14%, GBP ~12%, CAD ~9%, CHF ~4%; falta SEK, que

        # no está en symbol_registry). GC y ES quedan como anclas de

        # relación inversa (oro) y de risk-on/off (equity). Revisada en

        # el freeze final: sigue siendo la lista más apropiada, sin

        # agregados ni recortes.

        "correlation_pairs": ["EURUSD", "USDJPY", "GBPUSD", "USDCAD", "USDCHF", "GC", "ES", "VIX"],



        # --- series FRED curadas ---

        # DFF sacado: redundante con SOFR (ambos ~tasa overnight en

        # condiciones normales; SOFR además capta estrés de funding en

        # dólares, que es la narrativa DXY-específica que DFF no aporta).

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "SOFR", "M2SL", "NFCI", "WALCL", "PCEPILFE"],



        # LIMITACIÓN CONOCIDA: el Data Layer no tiene series FRED-equivalentes

        # de tasas/actividad de bancos centrales extranjeros (ECB, BOJ, BOE,

        # BOC, SNB) — solo calendario de eventos discretos para esas monedas.

        # El motor puede señalar "hoy hay reunión del BCE" pero NO puede

        # comparar niveles/trayectorias de política monetaria relativa de

        # forma continua. Diferenciales de crecimiento/tasas relativos

        # quedan fuera de alcance del V1.



        # --- calendario ---

        # ÚNICA excepción real a "solo USD" en todo el set: el índice se

        # mueve tanto por catalizadores propios (Fed) como por los de sus

        # componentes (ECB, BOJ, BOE, BOC, SNB). Confirmado que

        # economic_calendar_events tiene EUR/JPY/GBP/CAD/CHF poblados.

        "calendar_currencies": ["USD", "EUR", "JPY", "GBP", "CAD", "CHF"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "COPPER": {

        "display_name": "Cobre (HG)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "HG",

        "cot_symbol": "COPPER",             # cot_metrics.symbol (ojo: no "HG")

        "cot_dataset_type": "disagg_fut",   # mismo dataset que GOLD/SILVER/CL

        "correlation_symbol": "HG",



        # --- posicionamiento institucional ---

        # Mismo criterio que GOLD/SILVER/CL: disagg_fut, m_money principal,

        # swap como secundaria (intermediación).

        "cot_primary_field_prefix": "m_money",

        "cot_secondary_field_prefix": "swap",



        # --- correlaciones curadas ---

        # DXY (dólar), CL (narrativa conjunta de crecimiento global —

        # "Dr. Copper + petróleo"), ES (riesgo/equity de EEUU), HSI (proxy

        # de demanda china — el driver más específico de Cobre y el que

        # ningún otro activo del set cubre), VIX (risk-on/off), SI

        # (complejo de metales: industrial + semi-monetario).

        "correlation_pairs": ["DXY", "CL", "ES", "HSI", "VIX", "SI"],



        # --- series FRED curadas ---

        # INDPRO como pulso directo de manufactura/demanda industrial,

        # rates/liquidez como backdrop de ciclo global, BAMLH0A0HYM2

        # como barómetro de miedo a recesión (mismo rol que en CRUDE_OIL).

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI", "INDPRO", "BAMLH0A0HYM2"],



        # LIMITACIÓN CONOCIDA: BAMLH0A0HYM2 solo tiene historia desde

        # 2023-06-26 en fred_metrics (~3 años, no 5) — su pct_rank_5y no

        # refleja una ventana real de 5 años (ver detalle en CRUDE_OIL).



        # --- calendario ---

        # Único activo (junto con AUDUSD) con CNY habilitado: Cobre es el

        # activo más ligado a demanda china de todo el set, y

        # economic_calendar_events tiene CNY poblado (confirmado).

        "calendar_currencies": ["USD", "CNY"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "RUSSELL2000": {

        "display_name": "Russell 2000 (RUT)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "RUT",

        "cot_symbol": "RTY",                # ojo: en cot_metrics el símbolo es "RTY", no "RUT"

        "cot_dataset_type": "tff_fut",       # mismo dataset que NASDAQ/SP500/DXY

        "correlation_symbol": "RUT",



        # --- posicionamiento institucional ---

        # Mismo criterio que NQ/ES/DXY (tff_fut): lev_money principal,

        # asset_mgr secundaria.

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # ES como benchmark de breadth (RUT/ES es LA narrativa clásica de

        # small caps vs. large caps), DXY, rates, VIX. Nada de VXN/NQ acá:

        # RUT no tiene la sensibilidad de duración/growth de NQ, es más

        # "economía doméstica apalancada" que "growth tech".

        "correlation_pairs": ["ES", "DXY", "DGS10", "DGS2", "VIX"],



        # --- series FRED curadas ---

        # Mismo pilar que SP500 (economía real/doméstica), pero acá

        # BAMLH0A0HYM2 pesa MÁS que en NQ/ES: las small caps dependen mucho

        # más de crédito bancario/leveraged loans, así que el spread HY es

        # un driver más directo para RUT que para large caps.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI", "BAMLH0A0HYM2"],



        # LIMITACIÓN CONOCIDA: BAMLH0A0HYM2 solo tiene historia desde

        # 2023-06-26 en fred_metrics (~3 años, no 5) — ver detalle en

        # CRUDE_OIL.



        # --- calendario ---

        "calendar_currencies": ["USD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "EURUSD": {

        "display_name": "Euro / Dólar (EURUSD)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "EURUSD",

        "cot_symbol": "EUR",                 # cot_metrics usa el código de moneda sola, no el par

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "EURUSD",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # DXY (la cara opuesta de la misma moneda, ~57% del basket), rates

        # de EEUU (proxy de diferencial, con la limitación de abajo),

        # VIX (EUR como contraparte de flujos risk-off), GBPUSD (bloque

        # europeo/libra correlacionado), DAX (salud del equity europeo,

        # proxy de crecimiento de la Eurozona que ninguna otra fuente cubre).

        "correlation_pairs": ["DXY", "DGS10", "DGS2", "VIX", "GBPUSD", "DAX"],



        # --- series FRED curadas ---

        # Solo lado EEUU disponible — ver limitación abajo.

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasas ni actividad del BCE en Data

        # Layer — a diferencia de DXY (donde esto era una limitación

        # secundaria), acá es el driver #1 de un cruce FX (diferencial de

        # tasas EEUU-Eurozona) y no está disponible de forma continua.

        # Solo hay calendario de eventos discretos (BCE vía currency=EUR).



        # --- calendario ---

        "calendar_currencies": ["USD", "EUR"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "GBPUSD": {

        "display_name": "Libra / Dólar (GBPUSD)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "GBPUSD",

        "cot_symbol": "GBP",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "GBPUSD",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # DXY, rates EEUU, VIX, EURUSD (bloque europeo correlacionado).

        # Sin proxy de equity UK propio (no hay FTSE en symbol_registry) —

        # se sostiene con menos pares que EURUSD, no por descuido.

        "correlation_pairs": ["DXY", "DGS10", "DGS2", "VIX", "EURUSD"],



        # --- series FRED curadas ---

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasas ni actividad del BOE en Data

        # Layer — mismo caso que EURUSD/BCE. Solo calendario discreto

        # (currency=GBP).



        # --- calendario ---

        "calendar_currencies": ["USD", "GBP"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "USDJPY": {

        "display_name": "Dólar / Yen (USDJPY)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "USDJPY",

        "cot_symbol": "JPY",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "USDJPY",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # DXY, rates EEUU (el diferencial USD-JPY es el driver clásico del

        # carry trade), VIX (yen como moneda de funding — se fortalece en

        # risk-off), NQ (el carry trade unwind golpea primero a growth

        # tech — narrativa real y recurrente, no especulativa).

        "correlation_pairs": ["DXY", "DGS10", "DGS2", "VIX", "NQ"],



        # --- series FRED curadas ---

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasa del BOJ ni yield de JGB en Data

        # Layer. Es la limitación más severa de las seis FX — la

        # normalización de política del BOJ viene siendo EL driver

        # dominante de USDJPY hace años, y el motor no tiene forma de

        # verlo de manera continua, solo vía calendario discreto (JPY).



        # --- calendario ---

        "calendar_currencies": ["USD", "JPY"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "USDCAD": {

        "display_name": "Dólar / Dólar Canadiense (USDCAD)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "USDCAD",

        "cot_symbol": "CAD",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "USDCAD",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # CL es el driver distintivo (petro-moneda, ya lo usa CRUDE_OIL

        # en la dirección inversa), DXY, rates EEUU, VIX.

        "correlation_pairs": ["DXY", "CL", "DGS10", "DGS2", "VIX"],



        # --- series FRED curadas ---

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasas del BOC en Data Layer. Mismo

        # patrón que el resto de las FX — solo calendario discreto

        # (currency=CAD).



        # --- calendario ---

        "calendar_currencies": ["USD", "CAD"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "USDCHF": {

        "display_name": "Dólar / Franco Suizo (USDCHF)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "USDCHF",

        "cot_symbol": "CHF",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "USDCHF",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # VIX pesa más acá que en el resto de las FX: CHF es el refugio

        # clásico, se fortalece en risk-off puro. DXY, rates EEUU, EURUSD

        # (bloque europeo, históricamente muy ligado pre-2015).

        "correlation_pairs": ["DXY", "VIX", "DGS10", "DGS2", "EURUSD"],



        # --- series FRED curadas ---

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasas del SNB en Data Layer. Mismo

        # patrón que el resto de las FX — solo calendario discreto

        # (currency=CHF).



        # --- calendario ---

        "calendar_currencies": ["USD", "CHF"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },



    "AUDUSD": {

        "display_name": "Dólar Australiano / Dólar (AUDUSD)",



        # --- identificadores reales por tabla ---

        "pricing_symbol": "AUDUSD",

        "cot_symbol": "AUD",

        "cot_dataset_type": "tff_fut",

        "correlation_symbol": "AUDUSD",



        # --- posicionamiento institucional ---

        "cot_primary_field_prefix": "lev_money",

        "cot_secondary_field_prefix": "asset_mgr",



        # --- correlaciones curadas ---

        # HG (cobre) en vez de un segundo proxy de China: Australia es

        # exportador de commodities/minería, el link con cobre es real y

        # evita duplicar la narrativa china con dos fuentes distintas.

        # DXY, VIX (AUD es la FX de mayor beta a risk-on/off del set),

        # rates EEUU.

        "correlation_pairs": ["DXY", "HG", "VIX", "DGS10", "DGS2"],



        # --- series FRED curadas ---

        "fred_series": ["DGS10", "DGS2", "T10Y2Y", "DFF", "M2SL", "NFCI"],



        # LIMITACIÓN CONOCIDA: no hay tasas del RBA en Data Layer. Mismo

        # patrón que el resto de las FX — solo calendario discreto

        # (currency=AUD).



        # --- calendario ---

        # CNY habilitado además de USD/AUD: China es el principal socio

        # comercial de Australia (exportación de commodities), narrativa

        # real y distinta a la de USD/AUD por separado.

        "calendar_currencies": ["USD", "AUD", "CNY"],

        "calendar_lookback_days": 21,

        "calendar_forward_days": 14,

        "calendar_impact_filter": "High",

        "weekly_expected_runs": 5,

    },

}





def get_asset_config(asset_key: str) -> dict:

    if asset_key not in ASSET_CONFIGS:

        raise KeyError(

            f"Asset '{asset_key}' no está configurado en ASSET_CONFIGS. "

            f"Disponibles: {list(ASSET_CONFIGS.keys())}"

        )

    return ASSET_CONFIGS[asset_key]
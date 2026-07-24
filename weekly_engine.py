"""
weekly_engine.py — Motor del Weekly Recap
Intelligence Layer / Arkad Tools

run_weekly_engine(...) es el único punto de entrada. Llama a Gemini con
response_schema=WeeklyRecapOutput y devuelve el objeto ya validado.

El system prompt codifica las reglas conceptuales acordadas:
  - Frontera Intelligence Layer / Data Layer (no exponer nomenclatura interna).
  - Variables a vigilar en lenguaje natural, nunca paths técnicos.
  - Honestidad narrativa (no implicar observación completa si la semana es parcial).
  - Weekly confidence con 4 factores explícitos, no un campo de criterio libre.
  - Week completeness: no abortar, avisar, no inferir días faltantes.
  - Principio fundamental: no es un segundo motor de inteligencia.

Además de las reglas del prompt, hay 2 anclas anti-alucinación reforzadas
en código (mismo criterio que elasticity.py aplica a sorpresa_calendario:
lo que se puede verificar con un chequeo determinístico no queda librado
al criterio del modelo):
  1. weekly_confidence no puede ser HIGH si la semana está incompleta.
  2. Si se cuela nomenclatura del Data Layer (fred.xxx, cot.xxx, DGS10,
     zscore, etc.) en variables_a_vigilar o que_aprendio_sentinel, se
     loguea una advertencia visible (no se corrige el texto solo — 
     reescribir contenido generado es más riesgoso que avisar y dejar
     que ajustes el prompt si se repite).
"""

import json
import re
import os
from google import genai
from google.genai import types

from weekly_output_schema import WeeklyRecapOutput

# gemini-3.5-flash tenía cuota gratuita de solo 20 req/día (mismo
# problema que ya resolvimos en engine.py). Se usa el alias, no un ID
# fijo: los IDs fijos aparecen en ListModels pero Google los bloquea
# para generateContent en cuentas nuevas — el alias apunta siempre al
# modelo Flash-Lite habilitado para la cuenta en cada momento.
MODEL_NAME = "gemini-flash-lite-latest"

SYSTEM_PROMPT = """Sos Sentinel, la capa de inteligencia macro de Arkad Tools. \
Tu única función es leer los outputs diarios que el motor de caracterización \
de Estado (Asset Translator) generó durante la semana para UN activo, y \
resumir cómo evolucionó su narrativa.

## Frontera Intelligence Layer / Data Layer
El Weekly Recap vive 100% en la Intelligence Layer. NUNCA expone nombres de \
métricas, paths del payload ni nomenclatura interna del Data Layer (fred.xxx, \
cot.xxx, pricing.xxx, zscore, percentile, tickers como DGS10 o m_money_net). \
El lector nunca debería necesitar conocer el nombre de una métrica para \
entender las conclusiones del motor — todo se traduce a concepto o driver \
macro en lenguaje natural.

## Variables a vigilar
Se expresan como preguntas o conceptos, nunca como paths técnicos.
Correcto: "¿Los fondos especulativos continuarán acumulando posiciones?", \
"¿Las tasas de largo plazo comenzarán a relajarse?", "¿La próxima reunión \
de la Fed modificará las expectativas del mercado?".
Incorrecto: "cot.snapshots.current.m_money_net", "fred.DGS10.current.zscore", \
"correlation_252d.vix_zscore".

## Honestidad narrativa
Nunca uses expresiones que impliquen una observación completa de la semana \
si el recap fue generado con información parcial. Evitá: "durante toda la \
semana", "se observó consistentemente que", "el activo se comportó de \
forma sostenida" — cuando runs_used < runs_expected. Usá en su lugar: "en \
las corridas disponibles de la semana", "la narrativa observada se mantuvo \
intacta", "la información disponible sugiere que...", "no se observaron \
cambios significativos en los registros disponibles". La ausencia de \
información NUNCA debe interpretarse como evidencia de un cambio narrativo.

## Weekly confidence
NO representa una predicción de mercado — representa el nivel de claridad \
que tenés sobre la evolución narrativa de la semana. Para asignarlo, \
considerá explícitamente estos 4 factores:
1. Completitud de la semana (runs_used vs. runs_expected).
2. Consistencia de la narrativa observada.
3. Estado de las hipótesis (fortalecidas, debilitadas, abiertas o \
   contradictorias entre sí).
4. Si hay suficiente información para extraer conclusiones robustas.

HIGH: semana completa o casi completa + narrativas consistentes + \
hipótesis claras y alineadas con las observaciones.
MEDIUM: información parcial o señales mixtas + hipótesis abiertas o \
parcialmente fortalecidas.
LOW: información insuficiente o narrativas contradictorias — no se pueden \
extraer conclusiones narrativas robustas.

## Week completeness
Te paso runs_expected y runs_used. Si runs_used < runs_expected: generá \
igual el recap (nunca abortes — el objetivo es caracterizar incertidumbre, \
no dejar de producir inteligencia), completá nota_completitud explícitamente \
(ej. "Este Weekly Recap fue generado utilizando 4 de las 5 corridas \
esperadas para la semana. Algunas narrativas podrían encontrarse \
incompletas."), y no infieras narrativas que dependan de los días \
faltantes. Si runs_used == runs_expected, nota_completitud queda en null.

## Principio fundamental
El Weekly Recap NO explica qué ocurrió en el mercado. Explica qué aprendió \
Sentinel sobre la evolución narrativa del activo durante la semana. Una \
semana sin cambios significativos es una conclusión perfectamente válida y \
constituye inteligencia útil.

## Reglas generales
- Priorizá cambios narrativos sobre movimientos de precio.
- Priorizá contexto macro sobre volatilidad diaria.
- No inventes narrativas que no hayan aparecido en los outputs diarios.
- Tono institucional y objetivo. Sin lenguaje de trading ("comprar", \
  "vender", "target", etc.).
- Español rioplatense, directo, sin relleno corporativo.
"""

_LEAKED_NOMENCLATURE_PATTERN = re.compile(
    r"\b(fred|cot|pricing|correlations|calendar)\.[a-zA-Z_0-9]+"
    r"|\bDGS(10|2|5)\b|\bm_money_net\b|\bzscore\b|\bcorr_252d\b",
    re.IGNORECASE,
)


def _get_client() -> genai.Client:
    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise EnvironmentError("GEMINI_API_KEY debe estar seteada como variable de entorno.")
    return genai.Client(api_key=api_key)


def _build_user_prompt(
    asset_display_name: str,
    week_start,
    week_end,
    daily_runs: list[dict],
    hypotheses_week: list[dict],
    previous_weekly_recap: dict | None,
    runs_expected: int,
    runs_used: int,
) -> str:
    parts = [
        f"# Weekly Recap de {asset_display_name} — semana {week_start} a {week_end}",
        f"runs_expected: {runs_expected} | runs_used: {runs_used}",
        "\n# Corridas diarias de la semana (asset_history, orden cronológico)",
        "```json",
        json.dumps(daily_runs, indent=2, default=str, ensure_ascii=False),
        "```",
        "\n# Evolución de hipótesis de la semana (hypotheses_history)",
        "```json",
        json.dumps(hypotheses_week, indent=2, default=str, ensure_ascii=False),
        "```",
    ]

    if previous_weekly_recap:
        parts += [
            "\n# Weekly Recap de la semana anterior (para la comparación)",
            "```json",
            json.dumps(previous_weekly_recap, indent=2, default=str, ensure_ascii=False),
            "```",
        ]
    else:
        parts.append("\n# No hay Weekly Recap anterior — es la primera semana con historial suficiente.")

    return "\n".join(parts)


def _warn_if_leaked_nomenclature(output: WeeklyRecapOutput) -> None:
    """
    No corrige nada (reescribir texto generado es más riesgoso que dejarlo
    pasar) — solo deja un log visible si se coló nomenclatura del Data
    Layer, para notarlo en la corrida y ajustar el prompt si se repite.

    Revisa TODOS los campos de texto libre del output, no solo los que
    parecían más propensos a filtrarse — un leak en, por ejemplo,
    sorpresas_semana es tan grave como uno en variables_a_vigilar.
    """
    text_fields = [
        output.que_aprendio_sentinel,
        output.narrativas_cambiaron,
        output.sorpresas_semana,
        output.comparacion_semana_anterior,
        output.nota_completitud,
        output.evolucion_hipotesis.fortalecidas,
        output.evolucion_hipotesis.debilitadas,
        output.evolucion_hipotesis.validadas,
        output.evolucion_hipotesis.abiertas,
        *output.lo_mas_importante,
        *output.variables_a_vigilar,
    ]
    joined = " ".join(f for f in text_fields if f)
    if _LEAKED_NOMENCLATURE_PATTERN.search(joined):
        print("[weekly_engine] ADVERTENCIA: se detectó posible nomenclatura del Data Layer en el output.")


def run_weekly_engine(
    asset_display_name: str,
    week_start,
    week_end,
    daily_runs: list[dict],
    hypotheses_week: list[dict],
    previous_weekly_recap: dict | None,
    runs_expected: int,
    runs_used: int,
) -> WeeklyRecapOutput:
    """Punto de entrada único del motor Weekly Recap."""
    client = _get_client()
    user_prompt = _build_user_prompt(
        asset_display_name, week_start, week_end,
        daily_runs, hypotheses_week, previous_weekly_recap,
        runs_expected, runs_used,
    )

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=user_prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=WeeklyRecapOutput,
            temperature=0.2,
        ),
    )

    result: WeeklyRecapOutput = response.parsed
    if result is None:
        raise ValueError(f"Gemini no devolvió un objeto parseable. Respuesta cruda: {response.text[:2000]}")

    # Ancla anti-alucinación 1: la completitud es un dato duro ya
    # calculado, no depende del criterio del LLM.
    if runs_used < runs_expected and result.weekly_confidence == "HIGH":
        result.weekly_confidence = "MEDIUM"
        if not result.nota_completitud:
            result.nota_completitud = (
                f"Este Weekly Recap fue generado utilizando {runs_used} de las "
                f"{runs_expected} corridas esperadas para la semana."
            )

    # Ancla anti-alucinación 2: aviso (no corrección automática) si se
    # coló nomenclatura del Data Layer.
    _warn_if_leaked_nomenclature(result)

    return result
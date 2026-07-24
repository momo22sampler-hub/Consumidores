"""
weekly_output_schema.py — Schema de salida del Weekly Recap
Intelligence Layer / Arkad Tools

Mapea 1:1 el "Output esperado" del instructivo Weekly Recap.
Usado como response_schema en la llamada a Gemini — misma disciplina
que output_schema.py del motor diario, la API valida la forma exacta.
"""

from typing import Literal
from pydantic import BaseModel, Field

WeeklyConfidence = Literal["HIGH", "MEDIUM", "LOW"]


class EvolucionHipotesis(BaseModel):
    """Los 4 sub-campos de 'Evolución de las hipótesis' del instructivo."""
    fortalecidas: str = Field(description="Qué hipótesis se fortalecieron esta semana y por qué. 'Ninguna' si no aplica.")
    debilitadas: str = Field(description="Qué hipótesis se debilitaron esta semana. 'Ninguna' si no aplica.")
    validadas: str = Field(description="Qué hipótesis fueron validadas por los acontecimientos de la semana. 'Ninguna' si no aplica.")
    abiertas: str = Field(description="Qué hipótesis continúan abiertas, sin resolución esta semana.")


class WeeklyRecapOutput(BaseModel):
    """Objeto completo devuelto por el motor — una corrida = un recap semanal de un activo."""

    lo_mas_importante: list[str] = Field(
        description="3-4 bullets con lo más relevante de la semana, en lenguaje natural. Puede ser una sola línea si no hubo novedades."
    )
    que_aprendio_sentinel: str = Field(
        description=(
            "Resumen ejecutivo de cómo evolucionó el contexto macro y las narrativas "
            "predominantes esta semana. Debe ser honesto sobre la completitud de la "
            "información: si la semana fue parcial, evitar frases que impliquen haber "
            "observado toda la semana ('durante toda la semana', 'de forma consistente', "
            "'de forma sostenida') y usar en su lugar formulaciones como 'en las corridas "
            "disponibles de la semana' o 'la información disponible sugiere que...'."
        )
    )
    narrativas_cambiaron: str = Field(
        description="Qué cambió significativamente vs. qué mantuvo intacta su tesis principal."
    )
    evolucion_hipotesis: EvolucionHipotesis
    sorpresas_semana: str = Field(
        description="Comportamientos inesperados o contradicciones. 'No hay sorpresas relevantes esta semana' es una respuesta válida y preferible a inventar una."
    )
    comparacion_semana_anterior: str = Field(
        description="Comparación contra el weekly recap anterior. 'Primera semana con historial suficiente' si no se pasó recap previo."
    )
    variables_a_vigilar: list[str] = Field(
        description=(
            "Drivers macro relevantes para la próxima semana, en LENGUAJE NATURAL — "
            "preguntas o conceptos, nunca nomenclatura del Data Layer. Correcto: "
            "'¿Los fondos especulativos continuarán acumulando posiciones?', "
            "'¿Las tasas de largo plazo comenzarán a relajarse?'. Incorrecto: "
            "'cot.snapshots.current.m_money_net', 'fred.DGS10.current.zscore' — "
            "esos paths NUNCA deben aparecer acá ni en ningún otro campo del output."
        )
    )

    weekly_confidence: WeeklyConfidence = Field(
        description=(
            "Nivel de claridad que Sentinel tiene sobre la evolución narrativa de la "
            "semana — NO una predicción de mercado. Considera: completitud de la "
            "semana, consistencia narrativa, estado de las hipótesis, y si hay "
            "información suficiente para conclusiones robustas. No debería ser HIGH "
            "si la semana está incompleta (runs_used < runs_expected) — igual, no "
            "confíes solo en tu propio criterio para esto: el caller lo fuerza a "
            "MEDIUM si hace falta."
        )
    )
    nota_completitud: str = Field(
        description=(
            "Si runs_used < runs_expected, frase explícita avisando que el recap es "
            "parcial (ej. 'Este Weekly Recap fue generado utilizando 4 de las 5 "
            "corridas esperadas para la semana.'). Si la semana está completa "
            "(runs_used == runs_expected), devolvé un string vacío '' — el campo es "
            "obligatorio y no acepta ausencia de valor (limitación de la API de Gemini)."
        )
    )
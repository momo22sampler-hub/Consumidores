"""
output_schema.py — Schema de salida del Asset Translator
Intelligence Layer / Arkad Tools

Mapea 1:1 la estructura del doc fuente:
  Sección 1  -> EstadoActivo (Fase x Modificador, Convicción)
  Sección 2  -> AssetTranslatorOutput (frase puente, traducción macro, criolla)
  Sección 4  -> Hipotesis (4 componentes)
  Sección 5  -> Paranoia (5 preguntas con evidencia obligatoria)
  Sección 6  -> Memoria (comparación contra la hipótesis anterior)

Usado como response_schema en la llamada a Gemini — la API valida que
el modelo devuelva exactamente esta forma, sin parseo de markdown.
"""

from typing import Literal
from pydantic import BaseModel, Field


# --- Sección 1.2: Fase del ciclo (tabla de criterios del doc) ---
FaseCiclo = Literal[
    "price_discovery",
    "tendencia_madura",
    "acumulacion",
    "distribucion",
    "consolidacion",
    "exhaustion",
    "cambio_de_regimen",
]

# --- Sección 1.3: Modificador de contexto (puede ser más de uno) ---
ModificadorContexto = Literal[
    "contexto_favorable",
    "contexto_conflictivo",
    "esperando_catalizadores",
    "desacople_intermarket",
    "contexto_turbulento",
]

Conviccion = Literal["ALTA", "MEDIA", "BAJA"]

# --- Sección 6: clasificación de la hipótesis anterior contra la actual ---
EstadoHipotesisPrevia = Literal[
    "vigente_sin_cambios",
    "vigente_fortalecida",
    "vigente_debilitada",
    "invalidada",
    "no_aplica_primera_corrida",
]


class EstadoActivo(BaseModel):
    """Sección 1 — el titular compuesto Fase x Modificador."""
    fase: FaseCiclo
    modificadores: list[ModificadorContexto] = Field(
        description="Uno o más. Ver Sección 1.3 del doc — pueden coexistir."
    )
    conviccion: Conviccion
    evidence_keys: list[str] = Field(
        description=(
            "Claves concretas del payload que sostienen la fase elegida "
            "(ej. 'pricing.current.zscore_1d', 'cot.snapshots.current.m_money_percentile_5y'). "
            "Ancla anti-alucinación exigida por la Sección 1.2 del doc."
        )
    )


class Hipotesis(BaseModel):
    """Sección 4 — los 4 componentes, no 2."""
    sostiene: str = Field(description="Evidencia actual que apoya el escenario.")
    debilita: str = Field(
        description="Evidencia actual en contra, sin llegar a invalidar. Presente, no futuro."
    )
    invalidaria: str = Field(
        description="Condición futura falseable que rompería la tesis. Futuro, no presente."
    )
    podria_acelerarla: str = Field(
        description="Condición futura que confirmaría/reforzaría el escenario más rápido."
    )


class Paranoia(BaseModel):
    """Sección 5 — cada pregunta requiere evidencia concreta, no reflexión genérica."""
    que_estoy_ignorando: str = Field(
        description="Una métrica del payload NO mencionada en la traducción macro, y por qué se consideró secundaria."
    )
    que_me_hace_ruido: str = Field(
        description="Una métrica cuyo signo/magnitud es inconsistente con el resto del relato."
    )
    que_metrica_no_termina_de_cerrar: str = Field(
        description="Un dato con confianza baja en sí mismo (poca profundidad histórica, ventana corta, etc.), no una duda filosófica."
    )
    que_podria_cambiar_la_opinion: str = Field(
        description="Debe ser la misma condición de 'invalidaria' reformulada de cara a la semana próxima, no una idea nueva."
    )
    variable_a_vigilar: str = Field(
        description="UNA sola variable explícita, no una lista. Fuerza a priorizar."
    )
    contexto_limpio: bool = Field(
        description="True si genuinamente no hay nada real que objetar esta semana (ver Sección 5, regla de diseño clave)."
    )


class Memoria(BaseModel):
    """Sección 6 — comparación contra la corrida anterior."""
    estado_hipotesis_previa: EstadoHipotesisPrevia
    explicacion: str = Field(
        description="Si invalidada, debe citar explícitamente qué condición de 'invalidaria' se cruzó."
    )
    variable_vigilada_semana_pasada_aparecio_hoy: bool = Field(
        description="False si es la primera corrida (no hay variable previa que comparar) o si no apareció. True si se mencionó en la traducción macro de hoy."
    )


class AssetTranslatorOutput(BaseModel):
    """Objeto completo devuelto por el motor — una corrida = un objeto."""

    estado: EstadoActivo

    frase_puente: str = Field(
        description=(
            "1 frase (Sección 2 del doc: 'En una frase'). Bisagra entre el "
            "titular técnico y la traducción macro completa — regla de los 10 segundos."
        )
    )

    traduccion_macro: str = Field(
        description=(
            "Sección 3. Largo variable según triggers de elasticidad (ver Sección 7). "
            "Debe anclarse a los criterios de la fase elegida en 'estado.fase', "
            "no ser texto genérico con la fase pegada arriba."
        )
    )

    en_criollo: str = Field(
        description="Espejo de traduccion_macro en lenguaje informal. NUNCA introduce información nueva."
    )

    hipotesis: Hipotesis
    paranoia: Paranoia
    memoria: Memoria
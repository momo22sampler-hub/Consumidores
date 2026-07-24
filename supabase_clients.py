"""
supabase_clients.py — Conexiones a los dos proyectos Supabase
Intelligence Layer / Arkad Tools

Data Layer   (solo lectura): pricing_metrics, cot_metrics, fred_metrics,
                              calendar_metrics, sentiment_metrics,
                              pricing_correlations, economic_calendar_events
Intel Layer  (lectura/escritura): asset_outputs, asset_history,
                                   hypotheses_history

Misma convención que los extractores existentes: dotenv + create_client.
"""

import os
from pathlib import Path
from dotenv import load_dotenv
from supabase import create_client, Client

# Ruta explícita (no depende de desde dónde se ejecute el script) y
# utf-8-sig para tolerar el BOM que Notepad/algunos editores de Windows
# agregan al guardar el .env — sin esto, la primera variable del archivo
# puede no parsearse aunque el archivo "se vea" bien.
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", encoding="utf-8-sig")


def _client_from_env(url_var: str, key_var: str) -> Client:
    url = os.environ.get(url_var, "").strip()
    key = os.environ.get(key_var, "").strip()
    if not url or not key:
        raise EnvironmentError(
            f"{url_var} y {key_var} deben estar seteadas como variables de entorno."
        )
    return create_client(url, key)


def get_data_layer_client() -> Client:
    return _client_from_env("DATA_LAYER_SUPABASE_URL", "DATA_LAYER_SUPABASE_KEY")


def get_intel_layer_client() -> Client:
    return _client_from_env("INTEL_LAYER_SUPABASE_URL", "INTEL_LAYER_SUPABASE_KEY")
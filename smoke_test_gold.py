"""
smoke_test_gold.py — Prueba real (sin mock) del motor V2 para un activo
Intelligence Layer / Arkad Tools

NO persiste nada en Supabase. Llama a Gemini de verdad. Pensado para
correr una vez, mirar el output y decidir si el prompt de Llamada 1
(candidatos restringidos) se está respetando en la práctica.

Uso:
    python smoke_test_gold.py GOLD

Requiere en el entorno (o en un .env cargado con python-dotenv):
    GEMINI_API_KEY
    DATA_LAYER_SUPABASE_URL / DATA_LAYER_SUPABASE_KEY
    INTEL_LAYER_SUPABASE_URL / INTEL_LAYER_SUPABASE_KEY
"""

import json
import sys

# Si tenés un .env local y python-dotenv instalado, esto lo carga solo.
# Si no está instalado, no rompe — asume que las env vars ya están seteadas.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from data_contract import build_payload
from persistence import get_previous_state, get_previous_hypothesis
from engine import run_engine
from elasticity import compute_elasticity_flags


def main(asset_key: str) -> None:
    print(f"[smoke_test] Activo: {asset_key}")

    previous_state = get_previous_state(asset_key)
    previous_hypothesis = get_previous_hypothesis(asset_key)
    print(f"[smoke_test] previous_state: {json.dumps(previous_state, ensure_ascii=False, default=str)}")
    print(f"[smoke_test] previous_hypothesis: {json.dumps(previous_hypothesis, ensure_ascii=False, default=str)}")

    payload = build_payload(asset_key)
    print(f"[smoke_test] payload construido — claves top-level: {list(payload.keys())}")

    # DEBUG temporal (Documento 3, corrección Aug 2026): confirmar el nombre
    # real de los campos que devuelve fred_metrics — el prompt de la Llamada 2
    # asumía ".value" como sufijo de evidence_key sin haberlo confirmado
    # contra el schema real del Data Layer. Sacar esta línea una vez
    # confirmado y corregido el prompt/ejemplo en engine.py.
    print("[debug] fred.NFCI:", json.dumps(payload.get("fred", {}).get("NFCI"), ensure_ascii=False, default=str))

    print("[smoke_test] Llamando a run_engine() con Gemini real...")
    output = run_engine(asset_key, payload, previous_state, previous_hypothesis)

    print("\n=== EstadoActivo (Llamada 1) ===")
    print(json.dumps(output.estado.model_dump(mode="json"), ensure_ascii=False, indent=2))

    print("\n=== ContextoOutput (Llamada 2) ===")
    print(json.dumps(output.contexto.model_dump(mode="json"), ensure_ascii=False, indent=2))

    # today_estado en el shape que espera compute_elasticity_flags()
    # (mismo shape que persistence.get_previous_state() devuelve).
    today_estado = {
        "estado": output.estado.estado,
        "modificadores": output.contexto.modificadores,
        "conviccion": output.estado.conviccion,
    }
    flags = compute_elasticity_flags(today_estado, previous_state, payload)

    print("\n=== Elasticity flags ===")
    print(json.dumps(flags, ensure_ascii=False, indent=2))

    print("\n[smoke_test] Nada de esto se persistió en Supabase. "
          "Revisá arriba si el estado/modificadores/hipótesis tienen sentido "
          "antes de correr esto con persistencia real.")


if __name__ == "__main__":
    asset = sys.argv[1] if len(sys.argv) > 1 else "GOLD"
    main(asset)
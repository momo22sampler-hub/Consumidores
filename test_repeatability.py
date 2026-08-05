"""
test_repeatability.py — Prueba de repetibilidad de la Llamada 1
Intelligence Layer / Arkad Tools

OBJETIVO: medir si la Llamada 1 (Estado + Convicción) es determinista
o estocástica con datos idénticos. Un motor de clasificación estructural
que cambia su lectura con el mismo payload es fundamentalmente no
confiable — no importa cuánto texto tenga en el prompt.

CÓMO FUNCIONA:
1. Construye el payload real para el activo pedido (igual que producción)
   y lo congela en JSON en disco. Las corridas 2..N usan EXACTAMENTE el
   mismo dict en memoria — no se vuelve a llamar a data_contract ni a
   Supabase. Esto garantiza que cualquier variación en la respuesta es
   pura estocasticidad del modelo, no diferencia en el dato.

2. Determina el CandidateSet de hoy usando engine_estado + precheck, con
   el previous_state de Supabase (o None si es bootstrap). El candidate
   set también se congela — es idéntico para las N corridas.

3. Llama a _call_llamada_1() exactamente N veces y registra:
   - estado elegido
   - estado_provisional_hacia
   - conviccion
   - evidence_keys (lista completa)

4. Imprime un resumen de varianza: cuántas corridas coinciden, qué
   combinaciones distintas aparecieron, y el índice de Jaccard de
   las evidence_keys entre corridas.

USO:
    python test_repeatability.py [ASSET_KEY] [--runs N] [--save-payload]

    ASSET_KEY: clave del activo (default: GOLD). Cualquiera de los
               listados en config.ASSET_CONFIGS.
    --runs N:  cuántas veces repetir la Llamada 1 (default: 5).
    --save-payload: si se pasa, guarda el payload congelado en
               payload_frozen_<ASSET_KEY>.json (útil para auditoría
               o para reusar en futuras sesiones de debug).

REQUIERE:
    GEMINI_API_KEY, DATA_LAYER_SUPABASE_URL, DATA_LAYER_SUPABASE_KEY,
    INTEL_LAYER_SUPABASE_URL, INTEL_LAYER_SUPABASE_KEY (o .env).
"""

import argparse
import json
import os
import sys
import time
from collections import Counter
from typing import NamedTuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from data_contract import build_payload
from engine import _call_llamada_1, _run_etapa_0, CandidateSet
from engine_estado import compute_candidate_states
from output_schema import EstadoActivo
from payload_filter import build_structural_payload
from persistence import get_previous_state, get_previous_hypothesis
from precheck import check_invalidacion_confirmada, check_ruptura_precio

from google import genai

# --------------------------------------------------------------------------
# Tipos auxiliares
# --------------------------------------------------------------------------

class RunResult(NamedTuple):
    run_idx: int
    estado: str
    estado_provisional_hacia: str | None
    conviccion: str
    evidence_keys: list[str]
    elapsed_seconds: float


# --------------------------------------------------------------------------
# Utilidades de análisis
# --------------------------------------------------------------------------

def jaccard_similarity(set_a: set, set_b: set) -> float:
    """Índice de Jaccard entre dos conjuntos de evidence_keys."""
    if not set_a and not set_b:
        return 1.0
    union = set_a | set_b
    if not union:
        return 1.0
    return len(set_a & set_b) / len(union)


def print_separator(char: str = "─", width: int = 70) -> None:
    print(char * width)


def summarize_results(results: list[RunResult], asset_key: str, candidate_set: CandidateSet) -> None:
    print_separator("═")
    print(f"  RESUMEN DE REPETIBILIDAD — {asset_key}  ({len(results)} corridas)")
    print_separator("═")

    print(f"\n  Candidatos evaluados (Etapa 0):")
    print(f"    {candidate_set.candidatos}")
    print(f"    Motivo: {candidate_set.motivo}")

    # ── Estado elegido ────────────────────────────────────────────────────
    estado_counts = Counter(r.estado for r in results)
    provisional_counts = Counter(r.estado_provisional_hacia for r in results)
    conviccion_counts = Counter(r.conviccion for r in results)

    print(f"\n  Estado elegido:")
    for estado, count in estado_counts.most_common():
        pct = count / len(results) * 100
        bar = "█" * count + "░" * (len(results) - count)
        print(f"    {bar}  {estado}  ({count}/{len(results)}, {pct:.0f}%)")

    print(f"\n  Estado provisional hacia:")
    for prov, count in provisional_counts.most_common():
        pct = count / len(results) * 100
        label = str(prov) if prov is not None else "(ninguno)"
        print(f"    {label}: {count}/{len(results)} ({pct:.0f}%)")

    print(f"\n  Convicción:")
    for conv, count in conviccion_counts.most_common():
        pct = count / len(results) * 100
        print(f"    {conv}: {count}/{len(results)} ({pct:.0f}%)")

    # ── Combinaciones únicas ───────────────────────────────────────────────
    combos = Counter(
        (r.estado, r.estado_provisional_hacia, r.conviccion)
        for r in results
    )
    print(f"\n  Combinaciones únicas (estado, provisional, conviccion): {len(combos)}")
    for (est, prov, conv), count in combos.most_common():
        prov_str = prov or "—"
        print(f"    [{count}x] estado={est} | provisional_hacia={prov_str} | conviccion={conv}")

    # ── Varianza de evidence_keys ──────────────────────────────────────────
    all_keys: list[set] = [set(r.evidence_keys) for r in results]
    print(f"\n  Varianza de evidence_keys (Jaccard entre pares):")
    jaccard_scores = []
    for i in range(len(all_keys)):
        for j in range(i + 1, len(all_keys)):
            score = jaccard_similarity(all_keys[i], all_keys[j])
            jaccard_scores.append(score)
            print(f"    Corrida {i+1} vs {j+1}: J={score:.3f}  "
                  f"(|A|={len(all_keys[i])}, |B|={len(all_keys[j])}, "
                  f"|A∩B|={len(all_keys[i] & all_keys[j])})")

    if jaccard_scores:
        avg_j = sum(jaccard_scores) / len(jaccard_scores)
        min_j = min(jaccard_scores)
        print(f"\n    Promedio Jaccard: {avg_j:.3f}  |  Mínimo: {min_j:.3f}")
        if min_j < 0.5:
            print("    ⚠  ALERTA: varianza alta en evidence_keys — el modelo "
                  "cita fuentes distintas para el mismo payload.")
        elif avg_j >= 0.9:
            print("    ✓  Jaccard alto — el modelo cita fuentes consistentes.")
        else:
            print("    ~  Jaccard moderado — hay alguna variación en las fuentes "
                  "citadas, pero dentro de rango aceptable.")

    # ── Claves más frecuentes ──────────────────────────────────────────────
    all_keys_flat = [k for r in results for k in r.evidence_keys]
    key_freq = Counter(all_keys_flat)
    print(f"\n  Evidence keys más frecuentes (aparece en N de {len(results)} corridas):")
    for key, freq in key_freq.most_common(10):
        print(f"    [{freq}x] {key}")

    # ── Tiempos ───────────────────────────────────────────────────────────
    elapsed = [r.elapsed_seconds for r in results]
    avg_t = sum(elapsed) / len(elapsed)
    print(f"\n  Tiempos (Llamada 1): "
          f"promedio={avg_t:.1f}s | "
          f"min={min(elapsed):.1f}s | "
          f"max={max(elapsed):.1f}s")

    # ── Veredicto final ────────────────────────────────────────────────────
    print_separator()
    n_distintos = len(estado_counts)
    if n_distintos == 1:
        est_unico = list(estado_counts.keys())[0]
        n_conv_distintas = len(conviccion_counts)
        if n_conv_distintas == 1:
            print(f"  ✅  MOTOR DETERMINISTA: todas las corridas dieron "
                  f"'{est_unico}' con convicción '{list(conviccion_counts.keys())[0]}'.")
        else:
            print(f"  🟡  ESTADO ESTABLE ({est_unico}) pero convicción varía "
                  f"entre {list(conviccion_counts.keys())} — aceptable, pero "
                  f"monitorear si afecta las hipótesis.")
    else:
        print(f"  🔴  ESTADO INESTABLE: {n_distintos} estados distintos en "
              f"{len(results)} corridas con datos idénticos. El motor NO es "
              f"determinista. Esto impide el despliegue a producción.")
    print_separator("═")


# --------------------------------------------------------------------------
# Script principal
# --------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prueba de repetibilidad de la Llamada 1 del motor Sentinel."
    )
    parser.add_argument(
        "asset_key",
        nargs="?",
        default="GOLD",
        help="Clave del activo (default: GOLD).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=5,
        help="Cantidad de repeticiones de la Llamada 1 (default: 5).",
    )
    parser.add_argument(
        "--save-payload",
        action="store_true",
        help="Guarda el payload congelado en payload_frozen_<ASSET_KEY>.json.",
    )
    args = parser.parse_args()

    asset_key: str = args.asset_key.upper()
    n_runs: int = args.runs

    print_separator("═")
    print(f"  TEST DE REPETIBILIDAD — {asset_key} — {n_runs} corridas")
    print_separator("═")

    # ── 1. Payload real congelado ──────────────────────────────────────────
    print(f"\n[test] Construyendo payload real para {asset_key}...")
    payload = build_payload(asset_key)
    structural_payload = build_structural_payload(payload)
    print(f"[test] Payload construido. Claves top-level: {list(payload.keys())}")
    print(f"[test] Payload estructural (Llamada 1). Claves: {list(structural_payload.keys())}")

    if args.save_payload:
        fname = f"payload_frozen_{asset_key}.json"
        with open(fname, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f"[test] Payload congelado guardado en: {fname}")

    # ── 2. Estado previo y CandidateSet (se congela también) ──────────────
    print(f"\n[test] Leyendo estado previo de Supabase...")
    previous_state = get_previous_state(asset_key)
    previous_hypothesis = get_previous_hypothesis(asset_key)
    print(f"[test] previous_state: {json.dumps(previous_state, ensure_ascii=False, default=str)}")
    print(f"[test] previous_hypothesis claves: "
          f"{list(previous_hypothesis.keys()) if previous_hypothesis else None}")

    is_first_run = previous_state is None

    if is_first_run:
        candidate_set = CandidateSet(
            candidatos=["equilibrio", "acumulacion", "distribucion"],
            requiere_estado_provisional=False,
            motivo=(
                "Bootstrap (sin estado previo) — restringido a "
                "[equilibrio, acumulacion, distribucion] "
                "por Documento 2 §6.6 y §8."
            ),
        )
    else:
        estado_previo = previous_state["estado"]
        estado_provisional_previo = previous_state.get("estado_provisional_hacia")
        invalidacion = check_invalidacion_confirmada(payload, previous_hypothesis)
        ruptura = check_ruptura_precio(payload)
        candidate_set = compute_candidate_states(
            estado_previo=estado_previo,
            estado_provisional_previo=estado_provisional_previo,
            invalidacion_confirmada=invalidacion,
            ruptura_precio_detectada=ruptura,
        )

    estado_previo_str = (
        previous_state["estado"] if previous_state else "(sin estado previo — bootstrap)"
    )
    print(f"\n[test] CandidateSet fijado:")
    print(f"       candidatos    = {candidate_set.candidatos}")
    print(f"       estado_previo = {estado_previo_str}")
    print(f"       motivo        = {candidate_set.motivo}")

    # ── 3. N corridas de la Llamada 1 ──────────────────────────────────────
    api_key = os.environ["GEMINI_API_KEY"]
    client = genai.Client(api_key=api_key)

    results: list[RunResult] = []
    for i in range(1, n_runs + 1):
        print(f"\n[test] ── Corrida {i}/{n_runs} ──")
        t0 = time.time()
        estado_activo: EstadoActivo = _call_llamada_1(
            client=client,
            structural_payload=structural_payload,
            candidate_set=candidate_set,
            estado_previo=estado_previo_str,
        )
        elapsed = time.time() - t0

        result = RunResult(
            run_idx=i,
            estado=estado_activo.estado,
            estado_provisional_hacia=estado_activo.estado_provisional_hacia,
            conviccion=estado_activo.conviccion,
            evidence_keys=estado_activo.evidence_keys,
            elapsed_seconds=elapsed,
        )
        results.append(result)

        print(f"       estado              = {result.estado}")
        print(f"       estado_provisional  = {result.estado_provisional_hacia}")
        print(f"       conviccion          = {result.conviccion}")
        print(f"       evidence_keys       = {result.evidence_keys}")
        print(f"       tiempo              = {elapsed:.2f}s")

        # Pausa entre corridas para no saturar el rate-limit del free tier
        if i < n_runs:
            time.sleep(3)

    # ── 4. Análisis de varianza ────────────────────────────────────────────
    print()
    summarize_results(results, asset_key, candidate_set)


if __name__ == "__main__":
    main()
